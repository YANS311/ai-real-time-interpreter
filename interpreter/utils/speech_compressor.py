"""
口语压缩：纯正则去除填充词与重复片段（无 LLM 调用，零延迟）。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

# 英文填充词
_EN_FILLERS = re.compile(
    r"\b(um+|uh+|er+|ah+|like|you know|i mean|sort of|kind of|basically|actually|right)\b",
    re.IGNORECASE,
)
# 中文填充词
_ZH_FILLERS = re.compile(r"[嗯啊呃那个就是然后所以说不过还是其实]")
# 重复词（连续重复 2+ 次同一词）
_REPEAT_WORD = re.compile(r"\b(\w+)(\s+\1){1,}\b", re.IGNORECASE)
# 多余空格
_MULTI_SPACE = re.compile(r" {2,}")


def compress_speech(raw_text: str, source_lang: str = "en") -> dict[str, Any]:
    """
    纯正则压缩：去除填充词、合并重复片段。

    返回:
      text: 压缩后文本
      compressed: 是否做了压缩
      skipped: 是否跳过
      reason: 跳过原因
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return {"text": "", "compressed": False, "skipped": True, "reason": "empty"}
    if not getattr(settings, "SPEECH_COMPRESSOR_ENABLED", True):
        return {"text": raw_text, "compressed": False, "skipped": True, "reason": "disabled"}

    text = raw_text
    text = _EN_FILLERS.sub("", text)
    text = _ZH_FILLERS.sub("", text)
    text = _REPEAT_WORD.sub(r"\1", text)
    text = _MULTI_SPACE.sub(" ", text).strip()

    compressed = text != raw_text
    if not text or len(text) < 2:
        return {"text": raw_text, "compressed": False, "skipped": True, "reason": "empty_result"}
    return {"text": text, "compressed": compressed, "skipped": False, "reason": ""}
