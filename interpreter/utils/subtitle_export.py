"""
字幕导出模块（SRT / VTT / TXT），供 API 与前端下载使用。
"""
from __future__ import annotations

from .export import subtitles_to_srt, subtitles_to_txt, subtitles_to_vtt

__all__ = ["subtitles_to_srt", "subtitles_to_vtt", "subtitles_to_txt", "export_subtitles"]


def export_subtitles(items: list[dict], fmt: str = "srt") -> str:
    """统一导出入口。fmt: srt | vtt | txt"""
    if fmt == "txt":
        return subtitles_to_txt(items)
    if fmt == "vtt":
        return subtitles_to_vtt(items)
    return subtitles_to_srt(items)
