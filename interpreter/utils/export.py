"""
字幕导出：SRT / TXT。
"""
from __future__ import annotations


def _format_srt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def subtitles_to_srt(items: list[dict], default_duration: float = 3.0) -> str:
    """
    items: [{"source", "target", "start_sec", "end_sec"?}, ...]
    """
    lines = []
    for i, item in enumerate(items, 1):
        target = (item.get("target") or "").strip()
        source = (item.get("source") or "").strip()
        if not target and not source:
            continue
        start = float(item.get("start_sec") or 0)
        end = float(item.get("end_sec") or start + default_duration)
        if end <= start:
            end = start + default_duration
        text = target or source
        if source and target and source != target:
            text = f"{target}\n({source})"
        lines.append(str(i))
        lines.append(f"{_format_srt_time(start)} --> {_format_srt_time(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def subtitles_to_txt(items: list[dict]) -> str:
    lines = []
    for item in items:
        target = (item.get("target") or "").strip()
        if target:
            lines.append(target)
    return "\n".join(lines) + ("\n" if lines else "")
