"""
术语表 / 热词：翻译时优先使用指定译法。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from django.conf import settings


def _parse_line_terms(line: str) -> dict[str, str]:
    """解析 GPT=生成式预训练模型,API=接口 格式。"""
    terms: dict[str, str] = {}
    for part in re.split(r"[,;，；\n]", line):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and v:
            terms[k] = v
    return terms


def load_default_glossary() -> dict[str, str]:
    """从环境变量 GLOSSARY 加载默认术语。"""
    raw = os.getenv("GLOSSARY", "").strip()
    if not raw:
        return {}
    try:
        if raw.startswith("{"):
            data = json.loads(raw)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        return _parse_line_terms(raw)
    except json.JSONDecodeError:
        return _parse_line_terms(raw)


def merge_glossary(*sources: dict[str, str] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for src in sources:
        if src:
            merged.update(src)
    return merged


def parse_client_glossary(raw: Any) -> dict[str, str]:
    """解析前端/API 传入的术语表。"""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return {}
        try:
            if raw.startswith("{"):
                return parse_client_glossary(json.loads(raw))
            return _parse_line_terms(raw)
        except json.JSONDecodeError:
            return _parse_line_terms(raw)
    if isinstance(raw, list):
        terms = {}
        for item in raw:
            if isinstance(item, dict) and item.get("term"):
                terms[str(item["term"])] = str(item.get("translation", ""))
        return terms
    return {}


def format_glossary_prompt(terms: dict[str, str]) -> str:
    if not terms:
        return ""
    lines = [f"- {k} → {v}" for k, v in terms.items()]
    return "【术语表（翻译时必须优先采用以下译法）】\n" + "\n".join(lines)
