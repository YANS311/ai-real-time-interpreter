"""
共享 LLM HTTP 客户端（OpenAI 兼容 API），复用连接降低延迟。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

_client: httpx.Client | None = None
_client_lock = threading.Lock()


def get_llm_client() -> httpx.Client:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(60.0, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return _client


def call_llm(
    messages: list[dict],
    *,
    temperature: float = 0.3,
    json_mode: bool = False,
    max_tokens: int = 512,
) -> str:
    """调用 chat/completions，返回 assistant 文本。"""
    if not settings.LLM_API_KEY:
        return ""

    base = settings.LLM_API_BASE.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    url = f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    client = get_llm_client()
    resp = client.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    data = resp.json()
    return (data["choices"][0]["message"]["content"] or "").strip()
