"""
大模型翻译 + 基于上下文的识别/翻译自动纠错。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from django.conf import settings

from .glossary import format_glossary_prompt, merge_glossary, load_default_glossary

logger = logging.getLogger(__name__)

LANG_LABELS = {
    "auto": "自动检测",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
}

SYSTEM_PROMPT = """你是专业同声传译助手。用户会提供：
1. 历史字幕行（可能含识别错误，index 为全局行号）
2. 当前新识别的原文片段
3. 源语言提示

请完成：
1. 将「当前原文」翻译成流畅的中文（同声传译风格，简洁口语化）
2. 结合完整上下文，若历史行中存在明显 ASR 误识别或翻译错误，在 corrections 中给出修正
3. 仅修正确信度高的错误，不要过度修改

严格返回 JSON，不要 markdown：
{
  "translation": "当前片段的中文翻译",
  "corrections": [
    {"index": 0, "source": "修正后原文（无改动可省略）", "target": "修正后中文"}
  ]
}
若无修正，corrections 为空数组。"""


def _call_llm(messages: list[dict]) -> str:
    """调用 OpenAI 兼容 API。"""
    if not settings.LLM_API_KEY:
        return ""

    url = f"{settings.LLM_API_BASE.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }

    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


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

    user_content = f"""源语言：{lang_hint}

历史字幕（index 为全局行号）：
{history_text}

当前新识别原文：
{source_text}

请翻译当前原文，并检查历史是否需要纠错。{glossary_section}"""

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
