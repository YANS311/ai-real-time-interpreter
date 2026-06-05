"""
会话持久化：Redis 存储 AudioStreamSession（多 worker / 重启恢复）。
"""
from __future__ import annotations

import logging
import pickle
import time
from typing import TYPE_CHECKING

from django.conf import settings

from .redis_client import get_redis, redis_enabled

if TYPE_CHECKING:
    from .stream import AudioStreamSession

logger = logging.getLogger(__name__)

SESSION_KEY_PREFIX = "interpreter:session:"


def session_key(session_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}{session_id}"


def redis_sessions_enabled() -> bool:
    return redis_enabled() and getattr(settings, "REDIS_SESSIONS_ENABLED", True)


def persist_session(session: "AudioStreamSession") -> None:
    if not redis_sessions_enabled():
        return
    try:
        client = get_redis()
        client.setex(
            session_key(session.session_id),
            settings.SESSION_TTL,
            pickle.dumps(session, protocol=pickle.HIGHEST_PROTOCOL),
        )
    except Exception as e:
        logger.warning("Failed to persist session %s: %s", session.session_id, e)


def load_session(session_id: str) -> "AudioStreamSession | None":
    if not redis_sessions_enabled():
        return None
    try:
        raw = get_redis().get(session_key(session_id))
        if not raw:
            return None
        return pickle.loads(raw)
    except Exception as e:
        logger.warning("Failed to load session %s: %s", session_id, e)
        return None


def delete_session(session_id: str) -> bool:
    if redis_sessions_enabled():
        try:
            deleted = get_redis().delete(session_key(session_id))
            return deleted > 0
        except Exception as e:
            logger.warning("Failed to delete session %s: %s", session_id, e)
            return False
    from .stream import _SESSIONS

    if session_id in _SESSIONS:
        del _SESSIONS[session_id]
        return True
    return False


def list_session_ids(limit: int = 500) -> list[str]:
    if not redis_sessions_enabled():
        from .stream import _SESSIONS

        return list(_SESSIONS.keys())[:limit]
    try:
        client = get_redis()
        ids: list[str] = []
        for key in client.scan_iter(match=f"{SESSION_KEY_PREFIX}*", count=100):
            if isinstance(key, bytes):
                key = key.decode("utf-8", errors="ignore")
            ids.append(key.replace(SESSION_KEY_PREFIX, "", 1))
            if len(ids) >= limit:
                break
        return ids
    except Exception as e:
        logger.debug("list_session_ids failed: %s", e)
        return []


def list_sessions(limit: int = 500) -> list["AudioStreamSession"]:
    if not redis_sessions_enabled():
        from .stream import _SESSIONS

        return list(_SESSIONS.values())[:limit]
    sessions: list[AudioStreamSession] = []
    for sid in list_session_ids(limit=limit):
        session = load_session(sid)
        if session:
            sessions.append(session)
    return sessions


def cleanup_stale_sessions(ttl: int | None = None) -> int:
    """清理过期会话；Redis 模式依赖 key TTL，仅清理内存 fallback。"""
    if redis_sessions_enabled():
        return 0
    from .stream import _SESSIONS

    ttl = ttl or settings.SESSION_TTL
    now = time.time()
    stale = [k for k, v in _SESSIONS.items() if now - v.last_active > ttl]
    for k in stale:
        del _SESSIONS[k]
    return len(stale)
