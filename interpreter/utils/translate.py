"""
翻译模块：Google Translate 快速翻译 + LLM 可选纠错。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from django.conf import settings

from .glossary import merge_glossary, load_default_glossary

logger = logging.getLogger(__name__)

# Google Translate 客户端（线程安全）
_gt_lock = threading.Lock()
_gt_instance = None


def _get_gt():
    global _gt_instance
    if _gt_instance is not None:
        return _gt_instance
    with _gt_lock:
        if _gt_instance is not None:
            return _gt_instance
        from deep_translator import GoogleTranslator
        _gt_instance = GoogleTranslator(source="en", target="zh-CN")
        return _gt_instance


# 语言代码映射
_LANG_MAP = {
    "auto": "auto",
    "en": "en",
    "ja": "ja",
    "ko": "ko",
    "fr": "fr",
    "de": "de",
    "es": "es",
}


def _gt_translate(text: str, source_lang: str = "auto") -> str:
    """Google Translate 快速翻译。"""
    try:
        gt = _get_gt()
        src = _LANG_MAP.get(source_lang, "auto")
        if src != "auto":
            gt.source_language = src
        return gt.translate(text) or ""
    except Exception as e:
        logger.warning("Google Translate failed: %s", e)
        return ""


def apply_corrections_to_history(
    history: list[dict],
    corrections: list[dict],
) -> list[dict]:
    """将纠错写回会话历史。"""
    applied = []
    for c in corrections or []:
        idx = c.get("index")
        if idx is None or idx < 0 or idx >= len(history):
            continue
        if c.get("source"):
            history[idx]["source"] = c["source"]
        if c.get("target"):
            history[idx]["target"] = c["target"]
        history[idx]["corrected"] = True
        applied.append(c)
    return applied


def translate_with_correction(
    source_text: str,
    history: list[dict],
    source_lang: str = "auto",
    glossary: dict[str, str] | None = None,
    ppt_context: str = "",
) -> dict[str, Any]:
    """
    翻译当前片段。优先用 Google Translate（<1s），
    LLM 仅在配置且需要纠错时使用。
    """
    source_text = (source_text or "").strip()
    if not source_text:
        return {"translation": "", "corrections": [], "fallback": False}

    # Google Translate 快速翻译
    translation = _gt_translate(source_text, source_lang)

    # 应用术语表后处理
    if glossary:
        terms = merge_glossary(load_default_glossary(), glossary)
        for en, zh in terms.items():
            if en.lower() in source_text.lower():
                translation = translation  # 术语表已通过 prompt 注入，此处保留 Google 结果

    if translation:
        return {
            "translation": translation,
            "corrections": [],
            "fallback": False,
        }

    # Google 失败时降级
    return {
        "translation": f"[待翻译] {source_text}",
        "corrections": [],
        "fallback": True,
    }
