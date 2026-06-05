"""内置样例字幕脚本，用于离线演示。"""
from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings

_DEMO_PATH = (
    Path(settings.BASE_DIR) / "interpreter" / "static" / "demo" / "subtitles.json"
)


def load_demo_script() -> dict:
    """加载演示字幕脚本。"""
    if not _DEMO_PATH.exists():
        return {"title": "演示", "lines": []}
    return json.loads(_DEMO_PATH.read_text(encoding="utf-8"))
