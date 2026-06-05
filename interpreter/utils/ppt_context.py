"""
PPT / 演讲幻灯片上下文：注入翻译 prompt，提升专有名词与主题一致性。
"""
from __future__ import annotations

import json
from typing import Any

from django.conf import settings


def normalize_ppt_text(raw: Any, max_chars: int | None = None) -> str:
    """清洗并截断 PPT 上下文文本。"""
    if max_chars is None:
        max_chars = getattr(settings, "PPT_CONTEXT_MAX_CHARS", 4000)
    if raw is None:
        return ""
    if isinstance(raw, (dict, list)):
        raw = json.dumps(raw, ensure_ascii=False)
    text = str(raw).strip()
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def format_ppt_prompt(context: str) -> str:
    """生成注入翻译 prompt 的 PPT 上下文片段。"""
    context = normalize_ppt_text(context)
    if not context:
        return ""
    return (
        "【演讲 PPT / 幻灯片上下文（翻译时请参考以下主题、章节与术语背景）】\n"
        + context
    )


def parse_upload_payload(body: dict | None, post_data: dict | None = None) -> str:
    """从 JSON body 或 form 字段解析 PPT 文本。"""
    post_data = post_data or {}
    if body:
        for key in ("text", "context", "ppt_context", "content"):
            if body.get(key):
                return normalize_ppt_text(body[key])
    for key in ("text", "context", "ppt_context", "content"):
        if post_data.get(key):
            return normalize_ppt_text(post_data[key])
    return ""


def summarize_context(context: str, meta: dict | None = None) -> dict[str, Any]:
    """返回可序列化的上下文摘要。"""
    context = normalize_ppt_text(context)
    meta = meta or {}
    preview = context[:120] + ("…" if len(context) > 120 else "")
    return {
        "chars": len(context),
        "preview": preview,
        "configured": bool(context),
        "source_filename": meta.get("source_filename", ""),
        "source_type": meta.get("source_type", ""),
    }
