"""
同传历史记录持久化（JSON 文件，无需数据库）。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings

HISTORY_DIR = Path(settings.BASE_DIR) / "data" / "history"


def _ensure_dir() -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return HISTORY_DIR


def save_record(
    session_id: str,
    items: list[dict],
    meta: dict | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """保存一条同传记录。"""
    _ensure_dir()
    record_id = str(uuid.uuid4())
    now = time.time()
    record = {
        "id": record_id,
        "session_id": session_id,
        "title": title or f"同传记录 {time.strftime('%m-%d %H:%M')}",
        "created_at": now,
        "created_at_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "line_count": len(items),
        "items": items,
        "meta": meta or {},
    }
    path = HISTORY_DIR / f"{record_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "id": record_id,
        "title": record["title"],
        "created_at_iso": record["created_at_iso"],
        "line_count": record["line_count"],
    }


def list_records(limit: int = 30) -> list[dict]:
    """列出最近的历史记录（摘要）。"""
    _ensure_dir()
    files = sorted(HISTORY_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    results = []
    for path in files[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append(
                {
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "created_at_iso": data.get("created_at_iso"),
                    "line_count": data.get("line_count", 0),
                    "session_id": data.get("session_id"),
                    "meta": data.get("meta", {}),
                }
            )
        except (json.JSONDecodeError, OSError):
            continue
    return results


def get_record(record_id: str) -> dict | None:
    path = HISTORY_DIR / f"{record_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def delete_record(record_id: str) -> bool:
    path = HISTORY_DIR / f"{record_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def items_to_plain_text(items: list[dict]) -> str:
    """合并全文（便于复制）。"""
    lines = []
    for item in items:
        target = (item.get("target") or "").strip()
        source = (item.get("source") or "").strip()
        if target:
            lines.append(target)
        elif source:
            lines.append(source)
    return "\n".join(lines)
