"""
轻量说话人分段：基于静音间隔交替标注发言人 A/B。
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass


def segment_rms(pcm: bytes) -> float:
    """16-bit mono PCM 的 RMS 振幅。"""
    count = len(pcm) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", pcm)
    mean_sq = sum(s * s for s in samples) / count
    return math.sqrt(mean_sq)


def has_speech(pcm: bytes, threshold: float = 500.0) -> bool:
    """判断片段是否含有效语音。"""
    return segment_rms(pcm) >= threshold


@dataclass
class SpeakerTracker:
    """按静音间隔在发言人 A/B 间切换。"""

    current: str = "A"
    last_speech_end_sec: float = 0.0
    gap_threshold_sec: float = 1.2
    enabled: bool = True

    def assign(
        self,
        pcm: bytes,
        start_sec: float,
        end_sec: float,
    ) -> str | None:
        """
        为当前片段分配说话人标签。
        静音段返回 None；有语音则根据与上一段的间隔决定是否切换发言人。
        """
        if not self.enabled:
            return None
        if not has_speech(pcm):
            return None

        if self.last_speech_end_sec > 0:
            gap = start_sec - self.last_speech_end_sec
            if gap >= self.gap_threshold_sec:
                self.current = "B" if self.current == "A" else "A"

        self.last_speech_end_sec = end_sec
        return self.current

    def reset(self) -> None:
        self.current = "A"
        self.last_speech_end_sec = 0.0
