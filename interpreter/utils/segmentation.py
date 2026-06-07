"""
句子级断句：检测 ASR 输出中的句子边界，决定何时送翻译。
"""
from __future__ import annotations

import re

# 英文句末标点（强边界）
_SENTENCE_END_EN = re.compile(r"[.!?;]\s*$")
# 中文句末标点
_SENTENCE_END_ZH = re.compile(r"[。！？；…]\s*$")
# 逗号/顿号（弱边界，仅文本较长时才触发）
_CLAUSE_END = re.compile(r"[,，、]\s*$")
# 换行
_NEWLINE = re.compile(r"\n\s*$")

# 强制断句阈值
MAX_BUFFER_CHARS = 120
MAX_BUFFER_SEC = 2.5


def is_sentence_complete(text: str) -> bool:
    """检测文本是否包含完整的句子边界（句号/问号/感叹号/分号）。"""
    text = text.strip()
    if not text:
        return False
    if _SENTENCE_END_EN.search(text):
        return True
    if _SENTENCE_END_ZH.search(text):
        return True
    if _NEWLINE.search(text):
        return True
    return False


def is_clause_complete(text: str) -> bool:
    """逗号边界：仅当文本较长（>=15字符）且以逗号结尾时才触发。"""
    text = text.strip()
    if len(text) < 15:
        return False
    if _CLAUSE_END.search(text):
        return True
    return False


def should_flush(text: str, buffer_sec: float) -> bool:
    """综合判断是否应该结束当前句子并送翻译。"""
    text = text.strip()
    if not text:
        return False
    # 完整句子（句号/问号/感叹号）→ 立即翻译
    if is_sentence_complete(text):
        return True
    # 长逗号子句（>=20字）→ 翻译
    if is_clause_complete(text):
        return True
    # 缓冲超长 → 强制翻译
    if len(text) >= MAX_BUFFER_CHARS:
        return True
    # 缓冲超时 → 强制翻译
    if buffer_sec >= MAX_BUFFER_SEC:
        return True
    return False
