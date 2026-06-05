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

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是专业同声传译助手。用户会提供：
1. 历史字幕行（可能含识别错误）
2. 当前新识别的原文片段

请完成：
1. 将「当前原文」翻译成流畅的中文（同声传译风格，简洁口语化）
2. 若历史行中存在明显 ASR 误识别或翻译错误，在 corrections 中给出修正（仅修正确有问题的行）

严格返回 JSON，不要 markdown：
{
  "translation": "当前片段的中文翻译",
  "corrections": [
    {"index": 0, "source": "修正后原文或空", "target": "修正后中文"}
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
    # 去掉可能的 markdown 代码块
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        raw = m.group(0)
    return json.loads(raw)


def translate_with_correction(
    source_text: str,
    history: list[dict],
) -> dict[str, Any]:
    """
    翻译当前片段，并可能对历史字幕纠错。

    history 每项: {"source": "...", "target": "..."}

    Returns:
        {
            "translation": str,
            "corrections": [{"index": int, "source": str, "target": str}],
            "fallback": bool,
        }
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

    history_text = ""
    if history:
        lines = []
        for i, h in enumerate(history[-8:]):
            lines.append(f"[{i}] 原文: {h.get('source', '')} | 中文: {h.get('target', '')}")
        history_text = "\n".join(lines)

    user_content = f"""历史字幕（最近几行，index 从 0 起）：
{history_text or "（无）"}

当前新识别原文：
{source_text}

请翻译当前原文，并检查历史是否需要纠错。"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        raw = _call_llm(messages)
        parsed = _parse_llm_json(raw)
        return {
            "translation": parsed.get("translation", "").strip(),
            "corrections": parsed.get("corrections") or [],
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
    """未配置 API Key 时的占位：原样标注，便于本地调试 ASR。"""
    return f"[待翻译] {text}"
