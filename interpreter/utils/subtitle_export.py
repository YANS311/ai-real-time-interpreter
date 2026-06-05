"""
字幕导出模块（SRT / TXT），供 API 与前端下载使用。
"""
from __future__ import annotations

from .export import subtitles_to_srt, subtitles_to_txt

__all__ = ["subtitles_to_srt", "subtitles_to_txt", "export_subtitles"]


def export_subtitles(items: list[dict], fmt: str = "srt") -> str:
    """统一导出入口。fmt: srt | txt"""
    if fmt == "txt":
        return subtitles_to_txt(items)
    return subtitles_to_srt(items)
