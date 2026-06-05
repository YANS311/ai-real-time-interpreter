"""
识别质量评估：信噪比、ASR 置信度、综合质量分。
"""
from __future__ import annotations

import math
import struct
from typing import Any


def estimate_snr_db(pcm: bytes) -> float:
    """估算片段信噪比（dB），用于质量面板展示。"""
    if not pcm or len(pcm) < 4:
        return 0.0
    count = len(pcm) // 2
    samples = struct.unpack(f"<{count}h", pcm)
    if not samples:
        return 0.0

    abs_vals = [abs(s) for s in samples]
    abs_vals.sort()
    noise_idx = max(1, int(len(abs_vals) * 0.1))
    noise = sum(abs_vals[:noise_idx]) / noise_idx
    signal = sum(abs_vals[noise_idx:]) / max(1, len(abs_vals) - noise_idx)
    if noise < 1:
        noise = 1.0
    ratio = signal / noise
    return round(20 * math.log10(max(ratio, 1.0)), 1)


def asr_confidence_from_segments(segments: list) -> float:
    """从 Whisper 片段 avg_logprob 估算置信度 0~1。"""
    if not segments:
        return 0.0
    probs = []
    for s in segments:
        lp = s.get("avg_logprob")
        if lp is not None:
            probs.append(math.exp(float(lp)))
    if not probs:
        return 0.5
    return round(min(1.0, max(0.0, sum(probs) / len(probs))), 3)


def compute_quality_score(
    snr_db: float,
    asr_confidence: float,
    latency_ms: int,
    is_partial: bool = False,
) -> int:
    """综合质量分 0~100。"""
    snr_part = min(40.0, max(0.0, snr_db * 2))
    conf_part = asr_confidence * 40
    latency_part = max(0.0, 20 - latency_ms / 50)
    partial_penalty = 10 if is_partial else 0
    score = int(max(0, min(100, snr_part + conf_part + latency_part - partial_penalty)))
    return score


def compute_segment_quality(
    pcm: bytes,
    asr: dict,
    latency_ms: int,
    bgm_info: dict | None = None,
) -> dict[str, Any]:
    """单片段质量指标。"""
    snr = estimate_snr_db(pcm)
    conf = asr_confidence_from_segments(asr.get("segments") or [])
    is_partial = asr.get("is_partial", False)
    score = compute_quality_score(snr, conf, latency_ms, is_partial)

    speech_ratio = None
    if bgm_info:
        speech_ratio = bgm_info.get("speech_ratio")

    return {
        "score": score,
        "snr_db": snr,
        "asr_confidence": conf,
        "latency_ms": latency_ms,
        "is_partial": is_partial,
        "speech_ratio": speech_ratio,
    }
