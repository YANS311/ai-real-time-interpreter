"""
大模型翻译 + 基于上下文的识别/翻译自动纠错。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from django.conf import settings

from .glossary import format_glossary_prompt, merge_glossary, load_default_glossary
from .llm_client import call_llm
from .ppt_context import format_ppt_prompt

logger = logging.getLogger(__name__)

LANG_LABELS = {
    "auto": "自动检测",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
}

SYSTEM_PROMPT = """你是同声传译。翻译当前英文为中文，简洁口语化。如有明显历史识别错误，用 corrections 修正。

JSON格式：
{"translation":"译文","corrections":[{"index":行号,"source":"修正原文","target":"修正译文"}]}"""


def _call_llm(messages: list[dict]) -> str:
    """调用 OpenAI 兼容 API。"""
    return call_llm(messages, temperature=0.1, json_mode=True, max_tokens=256)


def _parse_llm_json(raw: str) -> dict:
    """解析 LLM 返回的 JSON。"""
    raw = raw.strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        raw = m.group(0)
    return json.loads(raw)


def _build_history_context(history: list[dict], window: int = 10) -> tuple[str, int]:
    """构建带全局 index 的历史上下文，返回 (文本, 起始偏移)。"""
    if not history:
        return "（无）", 0
    offset = max(0, len(history) - window)
    lines = []
    for i, h in enumerate(history[offset:]):
        idx = offset + i
        lines.append(
            f"[{idx}] 原文: {h.get('source', '')} | 中文: {h.get('target', '')}"
        )
    return "\n".join(lines), offset


def apply_corrections_to_history(
    history: list[dict],
    corrections: list[dict],
) -> list[dict]:
    """将 LLM 返回的纠错写回会话历史。"""
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
    翻译当前片段，并可能对历史字幕纠错。

    history 每项: {"source": "...", "target": "..."}
    """
    source_text = (source_text or "").strip()
    if not source_text:
        return {"translation": "", "corrections": [], "fallback": False}

    if not settings.LLM_API_KEY:
        return {
            "translation": _fallback_translate(source_text),
            "corrections": [],
            "fallback": True,
        }

    history_text, _ = _build_history_context(history)
    lang_hint = LANG_LABELS.get(source_lang, source_lang)

    terms = merge_glossary(load_default_glossary(), glossary or {})
    glossary_block = format_glossary_prompt(terms)
    glossary_section = f"\n\n{glossary_block}" if glossary_block else ""
    ppt_block = format_ppt_prompt(ppt_context)
    ppt_section = f"\n\n{ppt_block}" if ppt_block else ""

    user_content = f"""{lang_hint} | 历史：
{history_text}

原文：{source_text}
{glossary_section}{ppt_section}"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        raw = _call_llm(messages)
        parsed = _parse_llm_json(raw)
        corrections = parsed.get("corrections") or []
        # 过滤非法 index
        corrections = [
            c for c in corrections
            if isinstance(c.get("index"), int) and 0 <= c["index"] < len(history)
        ]
        return {
            "translation": parsed.get("translation", "").strip(),
            "corrections": corrections,
            "fallback": False,
        }
    except Exception as e:
        logger.exception("LLM translate failed: %s", e)
        return {
            "translation": _fallback_translate(source_text),
            "corrections": [],
            "fallback": True,
            "error": str(e),
        }


def _fallback_translate(text: str) -> str:
    """未配置 API Key 时的占位。"""
    return f"[待翻译] {text}"
