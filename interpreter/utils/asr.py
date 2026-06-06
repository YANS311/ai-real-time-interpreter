"""
Whisper 流式语音识别（基于 faster-whisper，按音频分片增量识别）。
"""
from __future__ import annotations

import io
import logging
import threading
from typing import Optional

from django.conf import settings

from .stream import pcm_to_wav_bytes

logger = logging.getLogger(__name__)

_model = None
_model_load_attempted = False
_model_lock = threading.Lock()


def _get_model():
    """懒加载 Whisper 模型（线程安全）。"""
    global _model, _model_load_attempted
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel
        from pathlib import Path

        model_path = getattr(settings, "WHISPER_MODEL_PATH", "")
        if model_path and Path(model_path).is_dir():
            logger.info("Loading Whisper model from local path: %s", model_path)
            _model = WhisperModel(
                model_path,
                device=settings.WHISPER_DEVICE,
                compute_type=settings.WHISPER_COMPUTE_TYPE,
            )
            _model_load_attempted = True
            return _model

        if _model_load_attempted:
            return None
        _model_load_attempted = True
        logger.info(
            "Loading Whisper model=%s device=%s",
            settings.WHISPER_MODEL,
            settings.WHISPER_DEVICE,
        )
        _model = WhisperModel(
            settings.WHISPER_MODEL,
            device=settings.WHISPER_DEVICE,
            compute_type=settings.WHISPER_COMPUTE_TYPE,
        )
        return _model


def warm_up_whisper() -> bool:
    """预加载 Whisper 模型，降低首包延迟。"""
    if _model is not None:
        return True
    try:
        _get_model()
        if _model is not None:
            logger.info("Whisper model warmed up")
            return True
        return False
    except Exception as e:
        logger.warning("Whisper warm-up failed: %s", e)
        return False


def transcribe_pcm(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
    language: Optional[str] = None,
) -> dict:
    """
    对一段 PCM 音频做识别，返回文本与是否偏「最终结果」。

    Returns:
        {
            "text": str,
            "language": str,
            "is_partial": bool,  # 短片段时可能仍会变
            "segments": list,
        }
    """
    if not pcm_bytes or len(pcm_bytes) < 3200:  # < 0.1s
        return {"text": "", "language": "", "is_partial": True, "segments": []}

    wav = pcm_to_wav_bytes(pcm_bytes, sample_rate)
    model = _get_model()
    if model is None:
        return {"text": "", "language": "", "is_partial": True, "segments": [], "error": "whisper model not available"}

    segments_iter, info = model.transcribe(
        io.BytesIO(wav),
        language=language,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=300,
            speech_pad_ms=200,
        ),
        beam_size=1,
        best_of=1,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4,
    )

    parts = []
    seg_list = []
    for seg in segments_iter:
        t = seg.text.strip()
        if t:
            parts.append(t)
            seg_list.append(
                {
                    "start": seg.start,
                    "end": seg.end,
                    "text": t,
                    "avg_logprob": getattr(seg, "avg_logprob", None),
                    "no_speech_prob": getattr(seg, "no_speech_prob", None),
                }
            )

    text = " ".join(parts).strip()
    duration_sec = len(pcm_bytes) / (sample_rate * 2)
    is_partial = duration_sec < 2.0 and len(text) < 3

    return {
        "text": text,
        "language": info.language or "",
        "is_partial": is_partial,
        "segments": seg_list,
        "language_probability": getattr(info, "language_probability", None),
    }


def transcribe_stream_chunk(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
    language: Optional[str] = None,
) -> dict:
    """
    流式分片入口：对当前缓冲片段识别。
    空音频返回空结果，不抛错。
    """
    try:
        return transcribe_pcm(pcm_bytes, sample_rate, language=language)
    except Exception as e:
        logger.exception("ASR failed: %s", e)
        return {
            "text": "",
            "language": "",
            "is_partial": True,
            "segments": [],
            "error": str(e),
        }
