"""
字幕多格式打包导出（SRT + TXT + JSON → ZIP）。
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime

from .export import subtitles_to_srt, subtitles_to_txt, subtitles_to_vtt


def build_export_bundle(
    items: list[dict],
    meta: dict | None = None,
) -> bytes:
    """生成 zip 字节流。"""
    meta = meta or {}
    meta.setdefault("exported_at", datetime.now().isoformat(timespec="seconds"))
    meta["line_count"] = len(items)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("subtitles.srt", subtitles_to_srt(items))
        zf.writestr("subtitles.vtt", subtitles_to_vtt(items))
        zf.writestr("subtitles.txt", subtitles_to_txt(items))
        payload = {"meta": meta, "items": items}
        zf.writestr(
            "subtitles.json",
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "README.txt",
            "AI 同声传译助手导出包\n"
            "- subtitles.srt  剪映/PR 字幕\n"
            "- subtitles.vtt  HTML5 视频字幕轨\n"
            "- subtitles.txt  纯中文文本\n"
            "- subtitles.json 完整数据含时间轴\n",
        )
    return buf.getvalue()
