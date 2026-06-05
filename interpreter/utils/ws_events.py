"""
WebSocket 事件推送（Django Channels）。
"""
from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import async_to_sync
from django.conf import settings

logger = logging.getLogger(__name__)


def session_group_name(session_id: str) -> str:
    return f"interpreter_{session_id}"


def channels_enabled() -> bool:
    return getattr(settings, "CHANNELS_ENABLED", True)


def push_session_event(session_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """向会话 WebSocket 组推送事件（同步上下文安全）。"""
    if not channels_enabled():
        return
    try:
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        message = {"type": event_type, **payload}
        async_to_sync(channel_layer.group_send)(
            session_group_name(session_id),
            {
                "type": "interpreter.message",
                "payload": message,
            },
        )
    except Exception as e:
        logger.debug("WebSocket push skipped: %s", e)


def push_chunk_result(session_id: str, result: dict[str, Any]) -> None:
    push_session_event(
        session_id,
        "chunk_result",
        {
            "session_id": session_id,
            "subtitle": result.get("subtitle"),
            "corrections": result.get("corrections", []),
            "history": result.get("history"),
            "latency_ms": result.get("latency_ms"),
            "quality": result.get("quality"),
            "progress": result.get("progress"),
            "file_eof": result.get("file_eof"),
            "paused": result.get("paused"),
        },
    )
