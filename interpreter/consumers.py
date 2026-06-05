"""
WebSocket 消费者：实时推送字幕与接收修正指令。
"""
from __future__ import annotations

import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async

from .utils.correction import apply_session_correction
from .utils.stream import get_or_create_session
from .utils.ws_events import session_group_name

logger = logging.getLogger(__name__)


class InterpreterConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope["url_route"]["kwargs"]["session_id"]
        self.group_name = session_group_name(self.session_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send(
            text_data=json.dumps(
                {
                    "type": "connected",
                    "session_id": self.session_id,
                    "message": "WebSocket connected",
                },
                ensure_ascii=False,
            )
        )

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(
                text_data=json.dumps({"type": "error", "error": "invalid json"})
            )
            return

        action = data.get("action")
        if action == "correct":
            await self._handle_correct(data)
            return
        if action == "ping":
            await self.send(text_data=json.dumps({"type": "pong"}))
            return
        if action == "reset":
            await self._handle_reset()
            return
        if action == "resync":
            await self.send(text_data=json.dumps({"type": "resync_ok"}))
            return

        await self.send(
            text_data=json.dumps({"type": "error", "error": f"unknown action: {action}"})
        )

    async def _handle_reset(self) -> None:
        try:
            await sync_to_async(_reset_session_sync)(self.session_id)
        except Exception as e:
            logger.exception("WebSocket reset failed: %s", e)
        await self.send(text_data=json.dumps({"type": "reset_ok"}))

    async def _handle_correct(self, data: dict) -> None:
        index = data.get("index")
        mode = data.get("mode", "manual")
        new_text = (data.get("new_text") or "").strip()

        try:
            payload = await sync_to_async(_apply_correction_sync)(
                self.session_id,
                int(index),
                new_text=new_text,
                mode=mode,
            )
        except (TypeError, ValueError) as e:
            await self.send(
                text_data=json.dumps({"type": "error", "error": str(e)}, ensure_ascii=False)
            )
            return
        except Exception as e:
            logger.exception("WebSocket correction failed: %s", e)
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "error": "correction failed"},
                    ensure_ascii=False,
                )
            )
            return

        await self.channel_layer.group_send(
            self.group_name,
            {"type": "interpreter.message", "payload": {"type": "correction", **payload}},
        )

    async def interpreter_message(self, event):
        payload = event.get("payload") or {}
        await self.send(text_data=json.dumps(payload, ensure_ascii=False))


def _apply_correction_sync(
    session_id: str,
    index: int,
    *,
    new_text: str = "",
    mode: str = "manual",
) -> dict:
    from .utils.stream import save_session

    session = get_or_create_session(session_id)
    payload = apply_session_correction(session, index, new_text=new_text, mode=mode)
    save_session(session)
    return payload


def _reset_session_sync(session_id: str) -> None:
    from .utils.stream import save_session

    session = get_or_create_session(session_id)
    session.reset_buffer()
    save_session(session)
