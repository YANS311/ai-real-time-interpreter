"""
视频/音频 BGM 处理：检测背景音乐、人声分离，提升含 BGM 视频的 ASR 准确率。
"""
from __future__ import annotations

import io
import logging
import struct
from typing import Any, Literal

import numpy as np
from pydub import AudioSegment

from .audio_process import (
    is_spleeter_available,
    separate_vocals,
)

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"}

SeparationMethod = Literal["auto", "spleeter", "fast"]


def _extension(filename: str) -> str:
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


def is_video_file(filename: str) -> bool:
    return _extension(filename) in VIDEO_EXTENSIONS


def media_bytes_to_segment(raw: bytes, filename: str) -> AudioSegment:
    """从音视频字节提取 AudioSegment（依赖 ffmpeg，Win/Mac/Linux 通用）。"""
    ext = _extension(filename).lstrip(".") or "mp4"
    return AudioSegment.from_file(io.BytesIO(raw), format=ext)


def _segment_to_mono_pcm(segment: AudioSegment, target_rate: int = 16000) -> bytes:
    seg = segment.set_frame_rate(target_rate).set_channels(1).set_sample_width(2)
    return seg.raw_data


def _pcm_to_numpy(pcm: bytes) -> np.ndarray:
    count = len(pcm) // 2
    if count == 0:
        return np.array([], dtype=np.float32)
    samples = struct.unpack(f"<{count}h", pcm)
    return np.array(samples, dtype=np.float32) / 32768.0


def detect_bgm(pcm: bytes, sample_rate: int = 16000) -> dict[str, Any]:
    """检测音频中是否可能存在背景音乐。"""
    samples = _pcm_to_numpy(pcm)
    if len(samples) < sample_rate:
        return {
            "has_bgm": False,
            "confidence": 0.0,
            "speech_ratio": 1.0,
            "message": "音频过短，无法检测",
        }

    step = max(1, len(samples) // 16000)
    chunk = samples[::step]
    spectrum = np.abs(np.fft.rfft(chunk))
    freqs = np.fft.rfftfreq(len(chunk), d=step / sample_rate)

    total_energy = float(np.sum(spectrum) + 1e-9)
    speech_mask = (freqs >= 300) & (freqs <= 3400)
    speech_energy = float(np.sum(spectrum[speech_mask]))
    low_mask = freqs < 200
    low_energy = float(np.sum(spectrum[low_mask]))

    speech_ratio = speech_energy / total_energy
    low_ratio = low_energy / total_energy
    bgm_score = (1.0 - speech_ratio) * 0.6 + low_ratio * 0.4
    has_bgm = bgm_score > 0.42
    confidence = min(1.0, max(0.0, bgm_score))

    return {
        "has_bgm": has_bgm,
        "confidence": round(confidence, 3),
        "speech_ratio": round(speech_ratio, 3),
        "low_freq_ratio": round(low_ratio, 3),
        "message": "检测到背景音乐" if has_bgm else "未明显检测到背景音乐",
    }


def prepare_media_for_interpretation(
    raw: bytes,
    filename: str,
    separate_bgm: bool = True,
    denoise: bool = True,
    separation_method: SeparationMethod = "auto",
    target_rate: int = 16000,
) -> tuple[bytes, int, dict[str, Any]]:
    """
    完整预处理：提取音轨 → BGM 检测 → 人声分离(Spleeter/快速) → 降噪 → 16kHz PCM。
    """
    audio = media_bytes_to_segment(raw, filename)
    duration_sec = len(audio) / 1000.0

    raw_pcm = _segment_to_mono_pcm(audio, target_rate)
    bgm_info = detect_bgm(raw_pcm, target_rate)
    bgm_info["duration_sec"] = round(duration_sec, 2)
    bgm_info["is_video"] = is_video_file(filename)
    bgm_info["separated"] = False
    bgm_info["spleeter_available"] = is_spleeter_available()

    if separate_bgm and (bgm_info["has_bgm"] or is_video_file(filename)):
        vocal, proc_meta = separate_vocals(
            audio,
            method=separation_method,
            denoise=denoise,
        )
        pcm = _segment_to_mono_pcm(vocal, target_rate)
        bgm_info["separated"] = True
        bgm_info.update(proc_meta)
        method_label = "Spleeter" if proc_meta.get("spleeter_used") else "快速分离"
        denoise_label = " + 降噪" if proc_meta.get("denoise_applied") else ""
        bgm_info["message"] = f"已启用人声分离（{method_label}{denoise_label}）"
    else:
        pcm = raw_pcm
        if denoise and separate_bgm:
            from .audio_process import reduce_noise

            enhanced = reduce_noise(audio)
            pcm = _segment_to_mono_pcm(enhanced, target_rate)
            bgm_info["denoise_applied"] = True
            bgm_info["message"] = "已启用环境降噪"
        elif not separate_bgm:
            bgm_info["message"] = "未启用人声分离"

    return pcm, target_rate, bgm_info
