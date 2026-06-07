"""
WebSocket 消费者：实时推送字幕、接收修正指令与音频流。
"""
from __future__ import annotations

import base64
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
        if action == "audio_chunk":
            await self._handle_audio_chunk(data)
            return
        if action == "audio_flush":
            await self._handle_audio_flush(data)
            return
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

    async def _handle_audio_chunk(self, data: dict) -> None:
        """通过 WebSocket 接收音频分片并处理。"""
        try:
            logger.info("WS audio_chunk session=%s fmt=%s data_len=%s flush=%s",
                         self.session_id, data.get("format"), len(data.get("data", "")), data.get("flush"))
            result = await sync_to_async(_process_audio_chunk_sync, thread_sensitive=False)(
                self.session_id, data,
            )
            if result:
                logger.info("WS audio_chunk result type=%s has_subtitle=%s has_history=%s latency=%s",
                             result.get("type"), bool(result.get("subtitle")), bool(result.get("history")), result.get("latency_ms"))
                await self.send(text_data=json.dumps(result, ensure_ascii=False))
            else:
                logger.info("WS audio_chunk: result is None (no segment ready)")
        except Exception as e:
            logger.exception("WS audio chunk failed: %s", e)

    async def _handle_audio_flush(self, data: dict) -> None:
        """通过 WebSocket 发送 flush 指令。"""
        try:
            result = await sync_to_async(_process_audio_chunk_sync, thread_sensitive=False)(
                self.session_id, {**data, "flush": True},
            )
            if result:
                await self.send(text_data=json.dumps(result, ensure_ascii=False))
        except Exception as e:
            logger.exception("WS audio flush failed: %s", e)


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


def _process_audio_chunk_sync(session_id: str, data: dict) -> dict | None:
    """通过 WebSocket 接收的音频分片处理（同步，在线程池执行）。"""
    from django.conf import settings
    from .utils.pipeline import process_audio_segment
    from .utils.stream import float32_pcm_to_int16, save_session

    session = get_or_create_session(session_id)
    session.touch()

    # 更新会话配置
    if "glossary" in data:
        from .utils.glossary import merge_glossary
        session.glossary = merge_glossary(session.glossary, data["glossary"])
    session.speaker_tracker.enabled = data.get("speaker_labels", True) != "0"
    source_lang = data.get("source_lang") or "auto"
    flush = data.get("flush", False)
    compress_speech_enabled = data.get("compress_speech", "1") == "1"

    # 支持前端传入分片时长（秒），灵活调整 ASR 分片
    chunk_dur = data.get("chunk_duration_sec")
    if chunk_dur is not None:
        try:
            session.chunk_duration_sec = max(0.1, min(3.0, float(chunk_dur)))
        except (TypeError, ValueError):
            pass

    # 解码音频
    fmt = data.get("format", "pcm_float")
    audio_b64 = data.get("data", "")
    if not audio_b64:
        return None

    try:
        raw = base64.b64decode(audio_b64)
        if fmt == "pcm_float":
            pcm = float32_pcm_to_int16(raw)
            sample_rate = int(data.get("sample_rate", 16000))
        else:
            pcm = raw
            sample_rate = int(data.get("sample_rate", 16000))

        session.sample_rate = sample_rate
        session.append_chunk(pcm)
        save_session(session)
    except Exception as e:
        return {"type": "error", "error": f"audio decode failed: {e}"}

    segment = session.flush_all() if flush else session.take_segment()
    if not segment:
        return None

    from .utils.circuit_breaker import get_asr_breaker, get_llm_breaker
    if get_asr_breaker().is_open() or get_llm_breaker().is_open():
        return {"type": "error", "error": "service temporarily unavailable"}

    result = process_audio_segment(
        segment,
        session.sample_rate,
        session.history,
        source_lang=source_lang,
        segment_start_sec=session.processed_duration_sec,
        segment_duration_sec=len(segment) / (session.sample_rate * 2),
        bgm_info=session.bgm_info or None,
        glossary=session.glossary or None,
        ppt_context=session.ppt_context or "",
        compress_speech_enabled=compress_speech_enabled,
        speaker_tracker=session.speaker_tracker,
        sentence_buffer=session.sentence_buffer,
        sentence_start_sec=session.sentence_start_sec,
    )

    # 更新 session 的句子缓冲状态
    if result.get("sentence_flushed"):
        session.sentence_buffer = ""
        session.sentence_start_sec = 0.0
    else:
        session.sentence_buffer = result.get("sentence_buffer", "")
        if not session.sentence_start_sec:
            session.sentence_start_sec = session.processed_duration_sec

    seg_dur = len(segment) / (session.sample_rate * 2)
    session.advance_duration(segment)
    session.last_asr_end_sec = session.processed_duration_sec + seg_dur
    result["session_id"] = session_id
    if session.bgm_info:
        result["bgm"] = session.bgm_info
    result["chunk_duration_ms"] = int(session.effective_chunk_duration() * 1000)
    save_session(session)

    # 返回结果，由调用方通过 WebSocket 发送（不走 channel layer 避免重复）
    result["type"] = "chunk_result"
    return result
