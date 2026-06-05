"""
中文语音合成（Edge-TTS）。
"""
from __future__ import annotations

import asyncio
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


async def _synthesize_async(text: str, voice: str) -> bytes:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks)


def synthesize_speech(text: str, voice: str | None = None) -> bytes:
    """
    将中文文本合成为 MP3 字节流。
    """
    text = (text or "").strip()
    if not text:
        return b""

    voice = voice or settings.TTS_VOICE
    try:
        return _run_async(_synthesize_async(text, voice))
    except Exception as e:
        logger.exception("TTS failed: %s", e)
        return b""
