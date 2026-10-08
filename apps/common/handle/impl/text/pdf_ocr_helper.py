# coding=utf-8
"""
@project: MaxKB
@file： pdf_ocr_helper.py
@desc: 扫描版 PDF(无文本层) 的 OCR 兜底能力。

       PDF 只由整页图片构成时, pypdf 无法提取任何文本, 分段结果为空。
       此时改为调用 models_provider 中已配置的视觉模型(IMAGE) 对页面图片做识别,
       把识别出的 markdown 文本作为 content 返回, 交由原有分段流程处理。

       模型解析优先级:
         1) 显式传入/上下文中的 model_id
         2) 配置项 PDF_OCR_MODEL_ID (config.yaml 或 MAXKB_PDF_OCR_MODEL_ID)
         3) 自动取工作空间内最新的一个视觉模型(IMAGE), 未指定工作空间时取全局最新的一个

       开关: PDF_OCR_ENABLE, 默认开启; 仅在文本层为空时才会触发, 不影响普通 PDF。
"""

import base64
import io
import threading
from typing import Optional, Tuple

from django.db.models import QuerySet
from langchain_core.messages import HumanMessage

from common.utils.logger import maxkb_logger
from maxkb.const import CONFIG

# 默认 OCR 提示词: 强调逐字还原 + 保留层级结构, 便于后续按 markdown 标题分段
DEFAULT_OCR_PROMPT = (
    "你是一个高精度的文档 OCR 与版面还原引擎。请识别图片中的全部文字内容, 并按原文结构输出 Markdown:\n"
    "1. 逐字识别, 不要翻译、不要总结、不要改写、不要增删内容;\n"
    "2. 保留原有段落与层级, 标题使用 ## 或 ### 表示, 有序/无序列表保留原有编号或符号;\n"
    "3. 表格使用 Markdown 表格还原, 保持行列对应关系;\n"
    "4. 页眉、页脚、页码、印章文字可省略, 正文中的文号、日期、落款必须保留;\n"
    "5. 只输出识别结果本身, 不要添加任何解释性说明, 也不要用代码块包裹。"
)

# 解析任务上下文。分段与 OCR 在同一线程内同步执行, 因此使用 thread local 即可传递。
_context = threading.local()

_FALSE_VALUES = ("false", "0", "no", "off", "none", "")


def set_ocr_context(workspace_id=None, model_id=None):
    """在执行分段前注入 OCR 上下文(工作空间/指定模型), 供 OCR 解析模型时使用。"""
    _context.workspace_id = workspace_id
    _context.model_id = model_id


def clear_ocr_context():
    """清理 OCR 上下文, 避免影响后续同线程的其他解析任务。"""
    for attr in ("workspace_id", "model_id"):
        if hasattr(_context, attr):
            delattr(_context, attr)


def get_ocr_context():
    return {
        "workspace_id": getattr(_context, "workspace_id", None),
        "model_id": getattr(_context, "model_id", None),
    }


def is_ocr_enabled() -> bool:
    value = CONFIG.get("PDF_OCR_ENABLE", True)
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_VALUES
    return bool(value)


def _get_int_config(key, default):
    try:
        return int(CONFIG.get(key, default))
    except (TypeError, ValueError):
        return default


def resolve_ocr_model(workspace_id=None, model_id=None) -> Tuple[Optional[object], Optional[str]]:
    """
    解析用于 OCR 的视觉模型实例。
    @return: (模型实例, 模型id); 无法解析时返回 (None, None)
    """
    if not is_ocr_enabled():
        return None, None

    model_id = model_id or get_ocr_context().get("model_id") or CONFIG.get("PDF_OCR_MODEL_ID")
    if isinstance(model_id, str) and model_id.strip() == "":
        model_id = None

    # 未显式指定时, 自动挑选一个视觉模型
    if model_id is None:
        from models_provider.base_model_provider import ModelTypeConst
        from models_provider.models import Model

        query_set = QuerySet(Model).filter(model_type=ModelTypeConst.IMAGE.name)
        if workspace_id:
            query_set = query_set.filter(workspace_id=workspace_id)
        model_id = query_set.order_by("-create_time").values_list("id", flat=True).first()

    if not model_id:
        return None, None

    try:
        from models_provider.tools import get_model_instance_by_model_workspace_id

        model = get_model_instance_by_model_workspace_id(str(model_id), workspace_id)
        return model, str(model_id)
    except Exception as e:
        maxkb_logger.error(f"PDF OCR 模型初始化失败, model_id: {model_id}, error: {e}")
        return None, None


def _to_data_url(image_name, raw_bytes) -> Optional[str]:
    """
    把页面图片统一编码为视觉模型可直接消费的 data url。
    优先使用 PIL 转成体积更小的 JPEG; PIL 不可用或解码失败时按原始格式回退。
    """
    max_side = _get_int_config("PDF_OCR_IMAGE_MAX_SIDE", 2000)
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw_bytes)) as image:
            image = image.convert("RGB")
            if max_side > 0 and max(image.size) > max_side:
                ratio = max_side / max(image.size)
                image = image.resize(
                    (max(1, int(image.size[0] * ratio)), max(1, int(image.size[1] * ratio))),
                    Image.LANCZOS,
                )
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=90)
            return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception as e:
        maxkb_logger.debug(f"PDF OCR 图片转码失败, 按原始格式回退: {image_name}, error: {e}")

    suffix = (image_name or "").rsplit(".", 1)
    image_format = suffix[-1].lower() if len(suffix) == 2 else "jpeg"
    if image_format in ("jpg", "jpeg", "png", "gif", "webp", "bmp"):
        return f"data:image/{image_format};base64,{base64.b64encode(raw_bytes).decode('utf-8')}"
    return None


def _clean_markdown(text) -> str:
    """归一化模型输出: 兼容 list 形式的 content, 并去掉可能被包裹的代码块。"""
    if isinstance(text, list):
        text = "".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in text)
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) > 2 and lines[-1].strip().startswith("```"):
            text = "\n".join(lines[1:-1])
    return text.strip()


def ocr_page(page, model, prompt=None) -> str:
    """对单页做 OCR, 返回识别出的 markdown 文本(可能为空字符串)。"""
    prompt = prompt or DEFAULT_OCR_PROMPT
    try:
        images = page.images
    except Exception as e:
        maxkb_logger.warning(f"PDF OCR 读取页面图片失败: {e}")
        return ""

    page_text = []
    for image in images:
        data_url = _to_data_url(image.name, image.data)
        if data_url is None:
            maxkb_logger.warning(f"PDF OCR 跳过不支持的图片格式: {image.name}")
            continue
        result = model.invoke(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ]
                )
            ]
        )
        text = _clean_markdown(getattr(result, "content", result))
        if text:
            page_text.append(text)

    return "\n\n".join(page_text)


def ocr_document(file_name, pdf_document, workspace_id=None) -> Optional[str]:
    """
    扫描版 PDF 的 OCR 兜底入口。
    @return: 识别出的 markdown 文本; 未启用/无可用模型/识别失败时返回 None
    """
    if not is_ocr_enabled():
        return None

    model, model_id = resolve_ocr_model(workspace_id=workspace_id or get_ocr_context().get("workspace_id"))

    if model is None:
        maxkb_logger.warning(
            f"File: {file_name}, 该 PDF 未检测到文本层(扫描件), 且未配置可用的视觉模型, 无法进行 OCR。"
            f"请先在 模型设置 中配置视觉模型, 或通过 PDF_OCR_MODEL_ID 指定模型。"
        )
        return None

    max_pages = _get_int_config("PDF_OCR_MAX_PAGES", 100)
    prompt = CONFIG.get("PDF_OCR_PROMPT") or DEFAULT_OCR_PROMPT

    sections = []
    for page_num, page in enumerate(pdf_document.pages):
        if max_pages > 0 and page_num >= max_pages:
            maxkb_logger.warning(f"File: {file_name}, 超过 PDF_OCR_MAX_PAGES({max_pages}) 限制, 剩余页面不再 OCR")
            break
        try:
            text = ocr_page(page, model, prompt)
        except Exception as e:
            maxkb_logger.error(f"File: {file_name}, Page: {page_num + 1}, PDF OCR 失败: {e}")
            continue
        if text:
            sections.append(text)

    if not sections:
        return None

    maxkb_logger.info(
        f"File: {file_name}, 未检测到文本层, 已使用视觉模型(model_id: {model_id}) OCR 识别 {len(sections)} 页"
    )
    return "\n\n".join(sections)
