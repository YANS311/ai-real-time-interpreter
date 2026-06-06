"""
非阻塞流式处理管线：解码 → ASR → 口语压缩 → 句子断句 → 翻译纠错，带耗时统计与熔断。
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from django.conf import settings

from .asr import transcribe_stream_chunk
from .circuit_breaker import get_asr_breaker, get_llm_breaker
from .quality import compute_segment_quality
from .segmentation import should_flush
from .speaker import SpeakerTracker
from .speech_compressor import compress_speech
from .correction import history_payload
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


def _run_translate(
    source_text: str,
    history: list,
    source_lang: str,
    glossary: dict | None = None,
    ppt_context: str = "",
) -> dict:
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
            source_text,
            history,
            source_lang=source_lang,
            glossary=glossary,
            ppt_context=ppt_context,
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
    segment_start_sec: float = 0.0,
    segment_duration_sec: float | None = None,
    bgm_info: dict | None = None,
    glossary: dict | None = None,
    ppt_context: str = "",
    compress_speech_enabled: bool = True,
    speaker_tracker: SpeakerTracker | None = None,
    sentence_buffer: str = "",
    sentence_start_sec: float = 0.0,
) -> dict[str, Any]:
    """
    处理一段 PCM 音频，返回 ASR + 翻译 + 纠错 + 延迟指标。

    句子级断句：ASR 文本先攒到 sentence_buffer，
    检测到句子边界才送翻译，否则只返回原文预览。

    blocking=False 时通过线程池异步执行（适用于仅预热场景）。
    """
    started = time.perf_counter()

    def _work() -> dict[str, Any]:
        nonlocal segment_duration_sec
        asr = _run_asr(segment, sample_rate, None if source_lang == "auto" else source_lang)
        source_text = (asr.get("text") or "").strip()

        result: dict[str, Any] = {
            "asr": asr,
            "translation": "",
            "corrections": [],
            "subtitle": None,
            "fallback": False,
            "sentence_flushed": False,
            "sentence_buffer": sentence_buffer,
        }

        if not source_text:
            latency = int((time.perf_counter() - started) * 1000)
            result["latency_ms"] = latency
            result["quality"] = compute_segment_quality(
                segment, asr, latency, bgm_info=bgm_info
            )
            return result

        compression = {"text": source_text, "compressed": False, "skipped": True, "reason": "disabled"}
        translate_source = source_text
        if compress_speech_enabled:
            compression = compress_speech(source_text, source_lang=source_lang)
            translate_source = compression.get("text") or source_text
        result["speech_compression"] = compression
        if compression.get("compressed"):
            asr = {**asr, "raw_text": source_text, "text": translate_source}

        # --- 句子级断句：攒够一句再翻译 ---
        combined = (sentence_buffer + " " + translate_source).strip() if sentence_buffer else translate_source
        buffer_sec = (segment_start_sec - sentence_start_sec) if sentence_start_sec > 0 else 0.0

        if not should_flush(combined, buffer_sec):
            # 未到句子边界 → 只返回原文预览，不翻译
            result["sentence_buffer"] = combined
            result["subtitle"] = {
                "source": combined,
                "source_raw": "",
                "target": "",
                "is_partial": True,
                "start_sec": round(sentence_start_sec or segment_start_sec, 3),
                "end_sec": round(segment_start_sec + (len(segment) / (sample_rate * 2)), 3),
                "speaker": None,
            }
            latency = int((time.perf_counter() - started) * 1000)
            result["latency_ms"] = latency
            return result

        # 到达句子边界 → 翻译完整句子
        result["sentence_flushed"] = True
        result["sentence_buffer"] = ""

        tr = _run_translate(
            combined,
            history,
            source_lang,
            glossary,
            ppt_context=ppt_context,
        )
        result["translation"] = tr.get("translation", "")
        result["corrections"] = tr.get("corrections", [])
        result["fallback"] = tr.get("fallback", False)

        apply_corrections_to_history(history, result["corrections"])

        if segment_duration_sec is None:
            segment_duration_sec = len(segment) / (sample_rate * 2)
        end_sec = segment_start_sec + segment_duration_sec

        speaker = None
        if speaker_tracker:
            speaker = speaker_tracker.assign(
                segment, segment_start_sec, end_sec
            )

        result["subtitle"] = {
            "source": combined,
            "source_raw": source_text if translate_source != source_text else "",
            "target": result["translation"],
            "is_partial": False,
            "start_sec": round(sentence_start_sec or segment_start_sec, 3),
            "end_sec": round(end_sec, 3),
            "speaker": speaker,
        }
        history.append(
            {
                "source": combined,
                "source_raw": source_text if translate_source != source_text else "",
                "target": result["translation"],
                "start_sec": round(sentence_start_sec or segment_start_sec, 3),
                "end_sec": round(end_sec, 3),
                "speaker": speaker,
            }
        )
        result["history"] = history_payload(history)
        latency = int((time.perf_counter() - started) * 1000)
        result["latency_ms"] = latency
        result["quality"] = compute_segment_quality(
            segment, asr, latency, bgm_info=bgm_info
        )
        return result

    if blocking:
        return _work()

    future = _executor.submit(_work)
    return future.result(timeout=settings.PIPELINE_TIMEOUT_SEC)
