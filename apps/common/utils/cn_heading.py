# coding=utf-8
"""
    @project: maxkb
    @Author：虎
    @file： cn_heading.py
    @date：2024/3/27 18:19
    @desc: 中文公文标题识别。

           把「第X章|编|节」「一、」「第X条」「（一）」这类公文条款标题映射为
           markdown 标题层级, 供 PDF 与文本两条分段链路复用, 使「按标题分段」
           能够下钻到条款级。
"""
import re

from django.utils.translation import gettext_lazy as _

from maxkb.const import CONFIG

# 中文数字集合(含 百/千/零/〇/两), 缺了 百千零〇两 会漏掉「（十一）」「第二十条」等
_CN_NUM = "一二三四五六七八九十百千零〇两"

# 标题行的长度上限。真实公文条款可以很长(实测最长 211 字), 这里只做兜底,
# 防止把整段正文误判成标题, 因此放得比较宽松。
CN_HEADING_MAX_LEN = 500

# 有序: 粗 -> 细, 顺序即层级。
# 只下钻到「条款级」:
#   - 「一、」/「第X章|编|节」-> ##
#   - 「（一）」/「第X条」      -> ###
# 不再识别「1.」「（1）」「①」这类子项, 否则会把一条款内的枚举拆成大量碎片
# (实测某公文「1.」子项有 122 处, 最短仅 7 字)。
# 每项为 (标题正则, markdown 层级, 「高级分段」下拉框展示名)。
_CN_HEADING_RULES = [
    (r"第[%s]+(?:章|编|节)" % _CN_NUM, "##", _("第X章/编/节")),
    (r"[%s]+、" % _CN_NUM, "##", _("一、章节")),
    (r"第[%s]+条" % _CN_NUM, "###", _("第X条")),
    (r"[（(][%s]+[)）]" % _CN_NUM, "###", _("（一）条款")),
]

# 供逐行判断使用: 行首锚定。
# 注意: 正则里不能出现捕获组 —— SplitModel 直接用 re.findall 的结果作为标题,
# 有捕获组时只会拿到组内容(split_model.py:184, re_findall 见 :191)。
CN_HEADING_PATTERN_LIST = [(re.compile("^" + rule), level) for rule, level, _label in _CN_HEADING_RULES]


def is_cn_heading_enable():
    """是否启用中文公文标题识别, 复用现有开关, 不新增配置键。"""
    value = CONFIG.get("PDF_HEADING_PATTERN_ENABLE", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(value)


def match_cn_heading_level(text):
    """
    判断一行文本是否为中文公文标题, 是则返回对应的 markdown 层级, 否则返回 None。
    """
    if not text:
        return None
    line = text.strip()
    if not line or len(line) > CN_HEADING_MAX_LEN:
        return None
    for pattern, level in CN_HEADING_PATTERN_LIST:
        if pattern.match(line):
            return level
    return None


def inject_cn_heading_markers(text, enable=True):
    """
    逐行扫描文本, 给识别出的中文条款标题补上 markdown 标题标记,
    使既有的 markdown 分段逻辑(只认 #/#/###)能够识别它们。

    已带 # 的行与 ``` 代码块内的内容不会被改动。
    """
    if not enable or not text:
        return text
    result = []
    in_code_fence = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            result.append(line)
            continue
        if in_code_fence or stripped.startswith("#"):
            result.append(line)
            continue
        level = match_cn_heading_level(stripped)
        result.append(f"{level} {stripped}" if level is not None else line)
    return "\n".join(result)


def _to_line_pattern(rule):
    """把「行首匹配」的规则转成「整行匹配」的正则字符串。"""
    return f"(?<=^){rule}.*|(?<=\\n){rule}.*"


def cn_split_pattern_strings():
    """
    返回「匹配整行标题」的正则字符串, 与 CN_HEADING_PATTERN_LIST 同序同层级。

    写法与仓库既有的分段标识(如 "#"/"##")保持一致: SplitModel 用 re.findall 且不带
    re.MULTILINE, 因此用 (?<=^)/(?<=\\n) 锚定行首而不是 ^; 且全部为非捕获组。
    """
    return [_to_line_pattern(rule) for rule, _level, _label in _CN_HEADING_RULES]


def cn_split_pattern_list():
    """cn_split_pattern_strings 的已编译版本, 供 PDF 书签分支直接交给 SplitModel 使用。"""
    return [re.compile(pattern) for pattern in cn_split_pattern_strings()]


def cn_split_pattern_options():
    """
    返回「高级分段」的「分段标识」下拉框选项: [{'key': 展示名, 'value': 正则字符串}]。
    前端把选中的 value 原样作为 patterns 提交, 因此必须与 cn_split_pattern_strings 一致。
    """
    return [{"key": label, "value": _to_line_pattern(rule)} for rule, _level, label in _CN_HEADING_RULES]
