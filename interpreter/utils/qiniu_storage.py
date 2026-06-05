"""
七牛云 Kodo 对象存储：音视频上传与访问 URL 生成。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


def is_qiniu_configured() -> bool:
    return bool(
        settings.QINIU_ACCESS_KEY
        and settings.QINIU_SECRET_KEY
        and settings.QINIU_BUCKET
    )


def upload_bytes(
    data: bytes,
    filename: str,
    content_type: str = "application/octet-stream",
) -> Optional[dict]:
    """
    上传字节流到七牛 Kodo。

    Returns:
        {"key": "...", "url": "https://..."} 或 None（未配置/失败）
    """
    if not is_qiniu_configured():
        return None

    try:
        from qiniu import Auth, put_data
    except ImportError:
        logger.warning("qiniu package not installed")
        return None

    ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    date_prefix = datetime.utcnow().strftime("%Y%m%d")
    key = f"interpreter/{date_prefix}/{uuid.uuid4().hex[:12]}.{ext}"

    auth = Auth(settings.QINIU_ACCESS_KEY, settings.QINIU_SECRET_KEY)
    token = auth.upload_token(settings.QINIU_BUCKET, key, 3600)

    ret, info = put_data(token, key, data, mime_type=content_type)
    if info.status_code != 200:
        logger.error("Qiniu upload failed: %s", info)
        return None

    domain = settings.QINIU_DOMAIN.rstrip("/")
    url = f"{domain}/{key}"
    return {"key": key, "url": url, "hash": ret.get("hash")}
