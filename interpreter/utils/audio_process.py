"""
音频预处理：Spleeter 人声/BGM 分离 + noisereduce 环境降噪。

Spleeter 为可选依赖（见 requirements-spleeter.txt），未安装时自动降级为快速分离链路。
与 video_extract 提取的纯人声音轨配合，送入流式 ASR 识别。
"""
from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydub import AudioSegment
from pydub.effects import compress_dynamic_range, high_pass_filter, low_pass_filter

logger = logging.getLogger(__name__)

SeparationMethod = Literal["auto", "spleeter", "fast"]


def is_spleeter_available() -> bool:
    try:
        import spleeter.separator  # noqa: F401

        return True
    except ImportError:
        return False


def _segment_to_wav_bytes(segment: AudioSegment) -> bytes:
    buf = io.BytesIO()
    segment.export(buf, format="wav")
    return buf.getvalue()


def _wav_bytes_to_segment(data: bytes) -> AudioSegment:
    return AudioSegment.from_file(io.BytesIO(data), format="wav")


def reduce_noise(
    segment: AudioSegment,
    strength: float = 0.55,
    non_stationary: bool = False,
) -> AudioSegment:
    """环境音/风声降噪（noisereduce）。"""
    try:
        import noisereduce as nr
    except ImportError:
        logger.info("noisereduce not installed")
        return segment

    samples = np.array(segment.get_array_of_samples(), dtype=np.float32)
    if segment.sample_width == 2:
        samples /= 32768.0
    if segment.channels > 1:
        samples = samples.reshape((-1, segment.channels)).mean(axis=1)

    reduced = nr.reduce_noise(
        y=samples,
        sr=segment.frame_rate,
        stationary=not non_stationary,
        prop_decrease=min(1.0, max(0.0, strength)),
    )
    reduced = np.clip(reduced, -1.0, 1.0)
    int_samples = (reduced * 32767).astype(np.int16)
    raw = int_samples.tobytes()
    # 直接构造 AudioSegment，避免 _spawn 继承原声道数导致帧不对齐
    return AudioSegment(
        data=raw,
        sample_width=2,
        frame_rate=segment.frame_rate,
        channels=1,
    )


def separate_vocals_fast(segment: AudioSegment) -> AudioSegment:
    """快速人声增强（无 GPU / 无 Spleeter 时使用）。"""
    if segment.channels >= 2:
        left, right = segment.split_to_mono()
        vocal = left.overlay(right)
    else:
        vocal = segment
    vocal = high_pass_filter(vocal, 120)
    vocal = low_pass_filter(vocal, 4500)
    return compress_dynamic_range(vocal, threshold=-22.0, ratio=3.0, attack=5.0)


def separate_vocals_spleeter(segment: AudioSegment) -> AudioSegment:
    """
    Spleeter 2 stems 分离：vocals + accompaniment。
    仅保留人声轨用于 ASR。
    """
    from spleeter.separator import Separator

    separator = Separator("spleeter:2stems")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        inp = tmp_path / "input.wav"
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        inp.write_bytes(_segment_to_wav_bytes(segment))
        separator.separate_to_file(str(inp), str(out_dir))
        vocal_wav = out_dir / "input" / "vocals.wav"
        if not vocal_wav.exists():
            raise FileNotFoundError("spleeter vocals output missing")
        return _wav_bytes_to_segment(vocal_wav.read_bytes())


def separate_vocals(
    segment: AudioSegment,
    method: SeparationMethod = "auto",
    denoise: bool = True,
    denoise_strength: float = 0.55,
) -> tuple[AudioSegment, dict[str, Any]]:
    """
    人声与 BGM 分离，返回纯人声 AudioSegment 与处理元信息。
    """
    meta: dict[str, Any] = {
        "method": "fast",
        "spleeter_used": False,
        "denoise_applied": False,
    }

    use_spleeter = method == "spleeter" or (
        method == "auto" and is_spleeter_available()
    )

    if use_spleeter:
        try:
            segment = separate_vocals_spleeter(segment)
            meta["method"] = "spleeter"
            meta["spleeter_used"] = True
        except Exception as e:
            logger.warning("Spleeter failed, fallback to fast: %s", e)
            segment = separate_vocals_fast(segment)
            meta["method"] = "fast"
            meta["fallback_reason"] = str(e)
    else:
        segment = separate_vocals_fast(segment)
        meta["method"] = "fast"

    if denoise:
        segment = reduce_noise(segment, strength=denoise_strength)
        meta["denoise_applied"] = True

    return segment.set_channels(1), meta
