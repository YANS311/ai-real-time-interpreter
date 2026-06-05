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

from .session_store import (
    cleanup_stale_sessions,
    delete_session,
    list_sessions,
    persist_session,
    redis_sessions_enabled,
)
from .speaker import SpeakerTracker

# 全局会话缓冲（未配置 REDIS_URL 时使用）
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
    # 服务端预处理的完整文件 PCM（视频人声分离后）
    processed_file_pcm: bytes = b""
    processed_read_offset: int = 0
    total_pcm_bytes: int = 0
    processed_duration_sec: float = 0.0
    paused: bool = False
    bgm_info: dict = field(default_factory=dict)
    glossary: dict = field(default_factory=dict)
    ppt_context: str = ""
    ppt_context_meta: dict = field(default_factory=dict)
    chunk_duration_sec: float | None = None
    speaker_enabled: bool = True
    speaker_tracker: SpeakerTracker = field(default_factory=SpeakerTracker)

    def effective_chunk_duration(self) -> float:
        if self.chunk_duration_sec is not None:
            return self.chunk_duration_sec
        return getattr(settings, "AUDIO_CHUNK_DURATION_SEC", 0.3)

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
            min_duration_sec = self.effective_chunk_duration()
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

    def load_processed_file(self, pcm: bytes, sample_rate: int, bgm_info: dict) -> None:
        """载入预处理后的完整文件 PCM（视频 BGM 分离后）。"""
        self.processed_file_pcm = pcm
        self.processed_read_offset = 0
        self.total_pcm_bytes = len(pcm)
        self.sample_rate = sample_rate
        self.bgm_info = bgm_info or {}
        self.processed_duration_sec = 0.0
        self.paused = False
        self.pcm_buffer.clear()
        self.touch()

    def _processed_remaining(self) -> int:
        return max(0, len(self.processed_file_pcm) - self.processed_read_offset)

    def take_processed_segment(self, min_duration_sec: float | None = None) -> Optional[bytes]:
        """从预处理文件缓冲取分片。"""
        if self.paused or not self.processed_file_pcm:
            return None
        if min_duration_sec is None:
            min_duration_sec = self.effective_chunk_duration()
        bytes_per_sec = self.sample_rate * 2
        min_bytes = int(bytes_per_sec * min_duration_sec)
        remaining = self._processed_remaining()
        if remaining < min_bytes:
            return None
        end = self.processed_read_offset + min_bytes
        segment = self.processed_file_pcm[self.processed_read_offset : end]
        self.processed_read_offset = end
        self.touch()
        return segment

    def flush_processed(self) -> Optional[bytes]:
        """取出预处理文件剩余缓冲。"""
        if self.paused or not self.processed_file_pcm:
            return None
        remaining = self._processed_remaining()
        if remaining <= 1600:
            return None
        segment = self.processed_file_pcm[self.processed_read_offset :]
        self.processed_read_offset = len(self.processed_file_pcm)
        self.touch()
        return segment

    def seek_processed(self, ratio: float) -> float:
        """跳转到预处理文件的指定进度（0~1），并截断跳转点之后的字幕历史。"""
        if not self.total_pcm_bytes:
            return 0.0
        ratio = max(0.0, min(1.0, ratio))
        self.processed_read_offset = int(self.total_pcm_bytes * ratio)
        self.processed_duration_sec = self.processed_read_offset / (self.sample_rate * 2)
        target_sec = self.processed_duration_sec
        self.history = [
            h
            for h in self.history
            if (h.get("end_sec") or 0) <= target_sec + 0.05
        ]
        self._sync_speaker_tracker_from_history()
        self.touch()
        return target_sec

    def _sync_speaker_tracker_from_history(self) -> None:
        """根据保留的字幕历史重建说话人分段状态（seek 后使用）。"""
        self.speaker_tracker.reset()
        for h in self.history:
            speaker = h.get("speaker")
            end_sec = h.get("end_sec")
            if speaker and end_sec is not None:
                self.speaker_tracker.current = speaker
                self.speaker_tracker.last_speech_end_sec = float(end_sec)

    def playback_progress(self) -> dict:
        """视频/文件同传播放进度。"""
        total = self.total_pcm_bytes
        if not total:
            return {
                "percent": 0.0,
                "processed_sec": 0.0,
                "total_sec": 0.0,
                "paused": self.paused,
                "has_file": False,
            }
        bps = self.sample_rate * 2
        return {
            "percent": round(100 * self.processed_read_offset / total, 1),
            "processed_sec": round(self.processed_duration_sec, 2),
            "total_sec": round(total / bps, 2),
            "paused": self.paused,
            "has_file": True,
        }

    def advance_duration(self, segment: bytes) -> None:
        """推进已处理时长（用于字幕时间轴）。"""
        self.processed_duration_sec += len(segment) / (self.sample_rate * 2)

    def reset_buffer(self) -> None:
        """清空音频缓冲与历史，保留会话配置（glossary / ppt_context）。"""
        self.pcm_buffer.clear()
        self.processed_file_pcm = b""
        self.processed_read_offset = 0
        self.total_pcm_bytes = 0
        self.processed_duration_sec = 0.0
        self.paused = False
        self.history.clear()
        self.bgm_info = {}
        self.speaker_tracker.reset()
        self.touch()


def get_or_create_session(session_id: str) -> AudioStreamSession:
    if redis_sessions_enabled():
        session = load_session_from_store(session_id)
        if session is None:
            session = AudioStreamSession(session_id=session_id)
        session.touch()
        persist_session(session)
        return session

    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = AudioStreamSession(session_id=session_id)
    else:
        _SESSIONS[session_id].touch()
    return _SESSIONS[session_id]


def load_session_from_store(session_id: str) -> Optional[AudioStreamSession]:
    from .session_store import load_session

    return load_session(session_id)


def save_session(session: AudioStreamSession) -> None:
    """将会话写回存储（Redis 或内存）。"""
    if redis_sessions_enabled():
        persist_session(session)
    else:
        _SESSIONS[session.session_id] = session


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
