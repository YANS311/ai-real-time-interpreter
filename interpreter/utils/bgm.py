"""
视频/音频 BGM 处理：检测背景音乐、人声分离，提升含 BGM 视频的 ASR 准确率。
"""
from __future__ import annotations

import io
import logging
import struct
from typing import Any

import numpy as np
from pydub import AudioSegment
from pydub.effects import compress_dynamic_range, high_pass_filter, low_pass_filter

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"}


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
    """
    检测音频中是否可能存在背景音乐。

    原理：对比语音频段 (300–3400Hz) 与全频段能量比，以及低频持续能量。
    """
    samples = _pcm_to_numpy(pcm)
    if len(samples) < sample_rate:
        return {
            "has_bgm": False,
            "confidence": 0.0,
            "speech_ratio": 1.0,
            "message": "音频过短，无法检测",
        }

    # 下采样以加速 FFT
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

    # 语音占比低 + 低频占比高 → 可能有 BGM
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


def separate_vocals(segment: AudioSegment) -> AudioSegment:
    """
    人声增强 / BGM 抑制（无需 GPU，ffmpeg+pydub 链路）。

    1. 立体声 → 提取中置人声 (L+R)/2
    2. 带通滤波保留语音频段
    3. 动态压缩突出人声
    4. 可选 noisereduce 抑制稳态 BGM
    """
    if segment.channels >= 2:
        # 中置声道 ≈ 人声（卡拉 OK 逆运算）
        left = segment.split_to_mono()[0]
        right = segment.split_to_mono()[1]
        vocal = left.overlay(right)
    else:
        vocal = segment

    vocal = high_pass_filter(vocal, 120)
    vocal = low_pass_filter(vocal, 4500)
    vocal = compress_dynamic_range(vocal, threshold=-22.0, ratio=3.0, attack=5.0)

    try:
        import noisereduce as nr

        samples = np.array(vocal.get_array_of_samples(), dtype=np.float32)
        if vocal.sample_width == 2:
            samples /= 32768.0
        reduced = nr.reduce_noise(
            y=samples,
            sr=vocal.frame_rate,
            stationary=True,
            prop_decrease=0.55,
        )
        reduced = np.clip(reduced, -1.0, 1.0)
        int_samples = (reduced * 32767).astype(np.int16)
        vocal = vocal._spawn(int_samples.tobytes())
    except Exception as e:
        logger.info("noisereduce skipped: %s", e)

    return vocal


def prepare_media_for_interpretation(
    raw: bytes,
    filename: str,
    separate_bgm: bool = True,
    target_rate: int = 16000,
) -> tuple[bytes, int, dict[str, Any]]:
    """
    完整预处理：提取音轨 → BGM 检测 → 可选人声分离 → 16kHz PCM。

    Returns:
        (pcm_bytes, sample_rate, meta)
    """
    audio = media_bytes_to_segment(raw, filename)
    duration_sec = len(audio) / 1000.0

    # 先检测原始音轨
    raw_pcm = _segment_to_mono_pcm(audio, target_rate)
    bgm_info = detect_bgm(raw_pcm, target_rate)
    bgm_info["duration_sec"] = round(duration_sec, 2)
    bgm_info["is_video"] = is_video_file(filename)
    bgm_info["separated"] = False

    if separate_bgm and (bgm_info["has_bgm"] or is_video_file(filename)):
        audio = separate_vocals(audio)
        pcm = _segment_to_mono_pcm(audio, target_rate)
        bgm_info["separated"] = True
        bgm_info["message"] = "已启用人声分离，抑制背景音乐"
    else:
        pcm = raw_pcm
        if not separate_bgm:
            bgm_info["message"] = "未启用人声分离"

    return pcm, target_rate, bgm_info
