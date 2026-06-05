"""
Redis 连接池与可用性检测。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


def redis_enabled() -> bool:
    return bool(getattr(settings, "REDIS_URL", "").strip())


@lru_cache(maxsize=1)
def get_redis():
    """返回带连接池的 Redis 客户端（进程内单例）。"""
    import redis

    url = settings.REDIS_URL.strip()
    pool = redis.ConnectionPool.from_url(
        url,
        max_connections=getattr(settings, "REDIS_MAX_CONNECTIONS", 20),
        socket_timeout=getattr(settings, "REDIS_SOCKET_TIMEOUT", 5),
        socket_connect_timeout=getattr(settings, "REDIS_CONNECT_TIMEOUT", 5),
        decode_responses=False,
    )
    return redis.Redis(connection_pool=pool)


def ping_redis() -> bool:
    if not redis_enabled():
        return False
    try:
        return bool(get_redis().ping())
    except Exception as e:
        logger.debug("Redis ping failed: %s", e)
        return False


def redis_status() -> dict[str, Any]:
    enabled = redis_enabled()
    connected = ping_redis() if enabled else False
    info: dict[str, Any] = {
        "enabled": enabled,
        "connected": connected,
        "url_configured": enabled,
    }
    if connected:
        try:
            client = get_redis()
            info["used_memory_human"] = (
                client.info("memory").get("used_memory_human", "")
            )
            info["connected_clients"] = client.info("clients").get(
                "connected_clients", 0
            )
        except Exception:
            pass
    return info
