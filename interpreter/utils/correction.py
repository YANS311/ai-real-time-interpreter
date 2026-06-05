"""
人工修正与 LLM 重译：更新会话历史并供 WebSocket 推送。
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from .glossary import format_glossary_prompt, merge_glossary, load_default_glossary
from .llm_client import call_llm
from .ppt_context import format_ppt_prompt

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是专业同声传译审校员。用户会提供：
1. 当前句子的英文原文
2. 当前不够准确的中文译文
3. 前后文英文句子

请结合上下文，重新翻译当前句，输出更准确、简洁的中文同声传译风格译文。
只输出修正后的中文，不要解释，不要 markdown。"""


def _call_llm_text(messages: list[dict]) -> str:
    return call_llm(messages, temperature=0.2, json_mode=False)


def build_context_sources(history: list[dict], index: int, window: int = 2) -> list[str]:
    """取目标行前后若干句英文原文作为上下文。"""
    start = max(0, index - window)
    end = min(len(history), index + window + 1)
    return [history[i].get("source", "") for i in range(start, end)]


def correct_translation(
    original_en: str,
    wrong_zh: str,
    context: list[str],
    glossary: dict[str, str] | None = None,
    ppt_context: str = "",
) -> str:
    """调用 LLM 结合上下文重新翻译一句。"""
    original_en = (original_en or "").strip()
    wrong_zh = (wrong_zh or "").strip()
    if not original_en:
        return wrong_zh

    if not settings.LLM_API_KEY:
        return wrong_zh or f"[待翻译] {original_en}"

    context_text = "\n".join(f"- {line}" for line in context if line) or "（无）"
    terms = merge_glossary(load_default_glossary(), glossary or {})
    glossary_block = format_glossary_prompt(terms)
    ppt_block = format_ppt_prompt(ppt_context)
    extra = ""
    if glossary_block:
        extra += f"\n\n{glossary_block}"
    if ppt_block:
        extra += f"\n\n{ppt_block}"

    user_content = f"""前后文英文：
{context_text}

当前句英文原文：
{original_en}

当前中文译文（可能不准确）：
{wrong_zh}

请输出更准确的中文译文。{extra}"""

    try:
        corrected = _call_llm_text(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ]
        )
        corrected = corrected.strip().strip('"').strip("'")
        return corrected or wrong_zh
    except Exception as e:
        logger.warning("LLM correction failed: %s", e)
        return wrong_zh


def history_payload(history: list[dict]) -> list[dict[str, Any]]:
    return [
        {
            "index": i,
            "source": h.get("source", ""),
            "source_raw": h.get("source_raw", ""),
            "target": h.get("target", ""),
            "corrected": h.get("corrected", False),
            "start_sec": h.get("start_sec"),
            "end_sec": h.get("end_sec"),
            "speaker": h.get("speaker"),
        }
        for i, h in enumerate(history)
    ]


def apply_session_correction(
    session,
    index: int,
    *,
    new_text: str = "",
    mode: str = "manual",
) -> dict[str, Any]:
    """
    修正指定字幕行。

    mode:
      - manual: 直接使用 new_text
      - llm: 调用 LLM 重译
    """
    history = session.history
    if index is None or index < 0 or index >= len(history):
        raise ValueError("invalid index")

    row = history[index]
    source = row.get("source", "")
    current_target = row.get("target", "")

    if mode == "llm":
        context = build_context_sources(history, index)
        target = correct_translation(
            source,
            current_target,
            context,
            glossary=session.glossary or None,
            ppt_context=session.ppt_context or "",
        )
    else:
        target = (new_text or "").strip()
        if not target:
            raise ValueError("empty correction")

    history[index]["target"] = target
    history[index]["corrected"] = True

    correction = {
        "index": index,
        "source": source,
        "target": target,
        "mode": mode,
    }
    return {
        "ok": True,
        "session_id": session.session_id,
        "correction": correction,
        "history": history_payload(history),
    }
