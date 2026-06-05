"""
音频流处理：分片接收、格式转换、缓冲与分段。
"""
from __future__ import annotations

import io
import struct
import time
import wave
from dataclasses import dataclass, field
from typing import Dict, Optional

from django.conf import settings

# 全局会话缓冲（生产环境可换 Redis）
_SESSIONS: Dict[str, "AudioStreamSession"] = {}


@dataclass
class AudioStreamSession:
    """单个客户端的音频流会话。"""

    session_id: str
    pcm_buffer: bytearray = field(default_factory=bytearray)
    sample_rate: int = 16000
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    # 已确认的识别/翻译行（用于纠错上下文）
    history: list = field(default_factory=list)

    def touch(self) -> None:
        self.last_active = time.time()

    def append_chunk(self, pcm_bytes: bytes) -> None:
        """追加 16-bit PCM 单声道数据。"""
        self.pcm_buffer.extend(pcm_bytes)
        self.touch()

    def take_segment(self, min_duration_sec: float | None = None) -> Optional[bytes]:
        """
        当缓冲达到最小时长时，取出一段 PCM 并清空已取部分。
        用于流式 ASR 的低延迟分片。
        """
        if min_duration_sec is None:
            min_duration_sec = getattr(settings, "AUDIO_CHUNK_DURATION_SEC", 0.3)
        bytes_per_sec = self.sample_rate * 2  # 16-bit mono
        min_bytes = int(bytes_per_sec * min_duration_sec)
        if len(self.pcm_buffer) < min_bytes:
            return None
        segment = bytes(self.pcm_buffer[:min_bytes])
        del self.pcm_buffer[:min_bytes]
        return segment

    def flush_all(self) -> Optional[bytes]:
        """取出剩余全部缓冲（如停止录音时）。"""
        if not self.pcm_buffer:
            return None
        data = bytes(self.pcm_buffer)
        self.pcm_buffer.clear()
        return data if len(data) > 1600 else None  # 至少 0.05s


def get_or_create_session(session_id: str) -> AudioStreamSession:
    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = AudioStreamSession(session_id=session_id)
    else:
        _SESSIONS[session_id].touch()
    return _SESSIONS[session_id]


def cleanup_stale_sessions(ttl: int = 3600) -> None:
    now = time.time()
    stale = [k for k, v in _SESSIONS.items() if now - v.last_active > ttl]
    for k in stale:
        del _SESSIONS[k]


def webm_to_pcm(webm_bytes: bytes, target_rate: int = 16000) -> bytes:
    """
    将浏览器 MediaRecorder 输出的 webm/opus 转为 16kHz PCM。
    需要 pydub + ffmpeg。
    """
    from pydub import AudioSegment

    audio = AudioSegment.from_file(io.BytesIO(webm_bytes))
    audio = audio.set_frame_rate(target_rate).set_channels(1).set_sample_width(2)
    return audio.raw_data


def wav_bytes_to_pcm(wav_bytes: bytes) -> tuple[bytes, int]:
    """解析 WAV 文件，返回 PCM 与采样率。"""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if channels > 1 or width != 2:
        from pydub import AudioSegment

        seg = AudioSegment(
            frames,
            sample_width=width,
            frame_rate=rate,
            channels=channels,
        )
        seg = seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        return seg.raw_data, 16000
    if rate != 16000:
        from pydub import AudioSegment

        seg = AudioSegment(
            frames, sample_width=2, frame_rate=rate, channels=1
        ).set_frame_rate(16000)
        return seg.raw_data, 16000
    return frames, rate


def pcm_to_wav_bytes(pcm: bytes, sample_rate: int = 16000) -> bytes:
    """PCM 转 WAV，供 Whisper 读取。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def float32_pcm_to_int16(pcm_float: bytes) -> bytes:
    """前端 ScriptProcessor 可能发送 float32，转为 int16。"""
    count = len(pcm_float) // 4
    floats = struct.unpack(f"<{count}f", pcm_float)
    ints = [max(-32768, min(32767, int(f * 32767))) for f in floats]
    return struct.pack(f"<{count}h", *ints)
