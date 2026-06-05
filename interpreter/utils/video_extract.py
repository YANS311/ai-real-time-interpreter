"""
视频音频提取：MP4/MOV/AVI 等格式 → 16kHz 单声道 PCM/WAV。

依赖 pydub + ffmpeg，Win / macOS / Linux 通用。
"""
from __future__ import annotations

import io
import logging
from typing import Any

from pydub import AudioSegment

logger = logging.getLogger(__name__)

# 计划要求 + 常见格式
SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v",
}
SUPPORTED_AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma",
}


def get_extension(filename: str) -> str:
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


def is_video_file(filename: str) -> bool:
    return get_extension(filename) in SUPPORTED_VIDEO_EXTENSIONS


def is_supported_media(filename: str) -> bool:
    ext = get_extension(filename)
    return ext in SUPPORTED_VIDEO_EXTENSIONS or ext in SUPPORTED_AUDIO_EXTENSIONS


def load_media_segment(raw: bytes, filename: str) -> AudioSegment:
    """从音视频字节加载 AudioSegment。"""
    if not raw:
        raise ValueError("empty file")
    ext = get_extension(filename).lstrip(".") or "mp4"
    if not is_supported_media(filename):
        logger.warning("unknown extension %s, trying ffmpeg auto-decode", ext)
    return AudioSegment.from_file(io.BytesIO(raw), format=ext)


def segment_to_pcm(segment: AudioSegment, sample_rate: int = 16000) -> bytes:
    """标准化为 16kHz 16-bit 单声道 PCM。"""
    seg = segment.set_frame_rate(sample_rate).set_channels(1).set_sample_width(2)
    return seg.raw_data


def segment_to_wav_bytes(segment: AudioSegment, sample_rate: int = 16000) -> bytes:
    """导出 WAV 字节流（供预览或存储）。"""
    seg = segment.set_frame_rate(sample_rate).set_channels(1).set_sample_width(2)
    buf = io.BytesIO()
    seg.export(buf, format="wav")
    return buf.getvalue()


def extract_audio_from_media(
    raw: bytes,
    filename: str,
    target_rate: int = 16000,
) -> tuple[bytes, bytes, int, dict[str, Any]]:
    """
    从视频/音频文件提取音轨。

    Returns:
        (pcm_bytes, wav_bytes, sample_rate, meta)
    """
    segment = load_media_segment(raw, filename)
    duration_sec = round(len(segment) / 1000.0, 2)
    pcm = segment_to_pcm(segment, target_rate)
    wav = segment_to_wav_bytes(segment, target_rate)

    meta = {
        "filename": filename,
        "is_video": is_video_file(filename),
        "format": get_extension(filename).lstrip(".") or "unknown",
        "duration_sec": duration_sec,
        "sample_rate": target_rate,
        "channels": 1,
        "pcm_bytes": len(pcm),
        "message": "音频提取成功",
    }
    return pcm, wav, target_rate, meta
