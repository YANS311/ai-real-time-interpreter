"""
口语压缩：ASR 输出规整后再送翻译，去除填充词与重复片段。
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from django.conf import settings

from .circuit_breaker import get_llm_breaker

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个口语文本规整器。去除填充词（um, uh, you know, like, I mean, sort of, 嗯, 啊 等），合并重复片段，优化断句，输出书面化英文。保持原意。

只输出规整后的文本，不要解释，不要 markdown。"""

_FILLER_RE = re.compile(
    r"\b(um+|uh+|er+|ah+|like|you know|i mean|sort of|kind of)\b",
    re.IGNORECASE,
)


def _should_skip(raw_text: str, source_lang: str) -> str | None:
    text = (raw_text or "").strip()
    if not text:
        return "empty"
    if not getattr(settings, "SPEECH_COMPRESSOR_ENABLED", True):
        return "disabled"
    min_chars = getattr(settings, "SPEECH_COMPRESSOR_MIN_CHARS", 12)
    if len(text) < min_chars:
        return "too_short"
    if source_lang not in ("en", "auto"):
        return "unsupported_lang"
    if not settings.LLM_API_KEY:
        return "no_llm"
    if get_llm_breaker().is_open():
        return "circuit_open"
    cleaned = _FILLER_RE.sub("", text).strip()
    if cleaned and len(cleaned) >= len(text) - 2:
        return "already_clean"
    return None


def _call_llm_text(messages: list[dict]) -> str:
    url = f"{settings.LLM_API_BASE.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": 0.2,
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return (data["choices"][0]["message"]["content"] or "").strip()


def compress_speech(raw_text: str, source_lang: str = "en") -> dict[str, Any]:
    """
    规整 ASR 口语文本。

    返回:
      text: 规整后文本（失败或未启用时等于原文）
      compressed: 是否实际做了 LLM 规整
      skipped: 是否跳过
      reason: 跳过原因
    """
    raw_text = (raw_text or "").strip()
    skip_reason = _should_skip(raw_text, source_lang)
    if skip_reason:
        return {
            "text": raw_text,
            "compressed": False,
            "skipped": True,
            "reason": skip_reason,
        }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": raw_text},
    ]
    try:
        compressed = _call_llm_text(messages)
        compressed = compressed.strip().strip('"').strip("'")
        if not compressed or len(compressed) < 2:
            return {
                "text": raw_text,
                "compressed": False,
                "skipped": True,
                "reason": "empty_result",
            }
        get_llm_breaker().record_success()
        return {
            "text": compressed,
            "compressed": compressed != raw_text,
            "skipped": False,
            "reason": "",
        }
    except Exception as e:
        get_llm_breaker().record_failure()
        logger.warning("Speech compression failed: %s", e)
        return {
            "text": raw_text,
            "compressed": False,
            "skipped": True,
            "reason": "error",
            "error": str(e),
        }
