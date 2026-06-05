"""
非阻塞流式处理管线：解码 → ASR → 翻译纠错，带耗时统计与熔断。
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from django.conf import settings

from .asr import transcribe_stream_chunk
from .circuit_breaker import get_asr_breaker, get_llm_breaker
from .translate import apply_corrections_to_history, translate_with_correction

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4)


def _run_asr(segment: bytes, sample_rate: int, language: Optional[str]) -> dict:
    breaker = get_asr_breaker()
    if breaker.is_open():
        return {"text": "", "language": "", "is_partial": True, "circuit_open": True}
    try:
        result = transcribe_stream_chunk(segment, sample_rate, language=language)
        if result.get("error"):
            breaker.record_failure()
        else:
            breaker.record_success()
        return result
    except Exception as e:
        breaker.record_failure()
        logger.exception("ASR pipeline error: %s", e)
        return {"text": "", "error": str(e), "circuit_open": breaker.is_open()}


def _run_translate(source_text: str, history: list, source_lang: str) -> dict:
    breaker = get_llm_breaker()
    if breaker.is_open():
        return {
            "translation": "",
            "corrections": [],
            "fallback": True,
            "circuit_open": True,
        }
    try:
        result = translate_with_correction(
            source_text, history, source_lang=source_lang
        )
        if result.get("error"):
            breaker.record_failure()
        else:
            breaker.record_success()
        return result
    except Exception as e:
        breaker.record_failure()
        logger.exception("Translate pipeline error: %s", e)
        return {
            "translation": f"[待翻译] {source_text}",
            "corrections": [],
            "fallback": True,
            "error": str(e),
            "circuit_open": breaker.is_open(),
        }


def process_audio_segment(
    segment: bytes,
    sample_rate: int,
    history: list,
    source_lang: str = "auto",
    blocking: bool = True,
) -> dict[str, Any]:
    """
    处理一段 PCM 音频，返回 ASR + 翻译 + 纠错 + 延迟指标。

    blocking=False 时通过线程池异步执行（适用于仅预热场景）。
    """
    started = time.perf_counter()

    def _work() -> dict[str, Any]:
        asr = _run_asr(segment, sample_rate, None if source_lang == "auto" else source_lang)
        source_text = (asr.get("text") or "").strip()

        result: dict[str, Any] = {
            "asr": asr,
            "translation": "",
            "corrections": [],
            "subtitle": None,
            "fallback": False,
        }

        if not source_text:
            result["latency_ms"] = int((time.perf_counter() - started) * 1000)
            return result

        tr = _run_translate(source_text, history, source_lang)
        result["translation"] = tr.get("translation", "")
        result["corrections"] = tr.get("corrections", [])
        result["fallback"] = tr.get("fallback", False)

        apply_corrections_to_history(history, result["corrections"])

        result["subtitle"] = {
            "source": source_text,
            "target": result["translation"],
            "is_partial": asr.get("is_partial", False),
        }
        history.append(
            {"source": source_text, "target": result["translation"]}
        )
        result["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return result

    if blocking:
        return _work()

    future = _executor.submit(_work)
    return future.result(timeout=settings.PIPELINE_TIMEOUT_SEC)
