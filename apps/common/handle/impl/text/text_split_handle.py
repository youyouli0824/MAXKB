# coding=utf-8
"""
    @project: maxkb
    @Author：虎
    @file： text_split_handle.py
    @date：2024/3/27 18:19
    @desc:
"""
import re
import traceback
from typing import List

from charset_normalizer import detect

from common.handle.base_split_handle import BaseSplitHandle
from common.utils.cn_heading import inject_cn_heading_markers, is_cn_heading_enable
from common.utils.logger import maxkb_logger
from common.utils.split_model import SplitModel

default_pattern_list = [
    re.compile('(?<=^)# (?!-\\*- coding:).*|(?<=\\n)# (?!-\\*- coding:).*'),
    re.compile('(?<=\\n)(?<!#)## (?!#).*|(?<=^)(?<!#)## (?!#).*'),
    re.compile("(?<=\\n)(?<!#)### (?!#).*|(?<=^)(?<!#)### (?!#).*"),
    re.compile("(?<=\\n)(?<!#)#### (?!#).*|(?<=^)(?<!#)#### (?!#).*"),
    re.compile("(?<=\\n)(?<!#)##### (?!#).*|(?<=^)(?<!#)##### (?!#).*"),
    re.compile("(?<=\\n)(?<!#)###### (?!#).*|(?<=^)(?<!#)###### (?!#).*")
]

end = [".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm", ".mpeg", ".mpg", ".3gp", ".ts", ".rmvb",
       ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus", ".alac", ".aiff", ".amr",
       ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".heif", ".raw", ".ico", ".svg", ".pdf"]


class TextSplitHandle(BaseSplitHandle):
    def support(self, file, get_buffer):
        file_name: str = file.name.lower()
        if file_name.endswith(".md") or file_name.endswith('.txt') or file_name.endswith('.TXT') or file_name.endswith(
                '.MD'):
            return True
        lower_name = file_name.lower()
        if any([True for item in end if lower_name.endswith(item)]):
            return False
        buffer = get_buffer(file)
        result = detect(buffer)
        if result['encoding'] is not None and result['confidence'] is not None and result['encoding'] != 'ascii' and \
                result['confidence'] > 0.5:
            return True
        return False

    def handle(self, file, pattern_list: List, with_filter: bool, limit: int, get_buffer, save_image):
        buffer = get_buffer(file)
        if type(limit) is str:
            limit = int(limit)
        if type(with_filter) is str:
            with_filter = with_filter.lower() == 'true'
        if pattern_list is not None and len(pattern_list) > 0:
            split_model = SplitModel(pattern_list, with_filter, limit)
        else:
            split_model = SplitModel(default_pattern_list, with_filter=with_filter, limit=limit)
        try:
            content = buffer.decode(detect(buffer)['encoding'])
        except BaseException as e:
            maxkb_logger.error(f"Error processing TEXT file {file.name}: {e}, {traceback.format_exc()}")
            return {'name': file.name, 'content': []}
        # 智能分段(未指定分段标识)时, 按中文公文标题特征补充 markdown 标记,
        # 让「（一）（二）」这类条款也能作为标题参与分段。
        # 高级分段由用户自选分段标识, 不做任何改动。
        if (pattern_list is None or len(pattern_list) == 0) and is_cn_heading_enable():
            content = inject_cn_heading_markers(content)
        return {'name': file.name, 'content': split_model.parse(content)}

    def get_content(self, file, save_image):
        buffer = file.read()
        try:
            return buffer.decode(detect(buffer)['encoding'])
        except BaseException as e:
            traceback.print_exception(e)
            return f'{e}'
