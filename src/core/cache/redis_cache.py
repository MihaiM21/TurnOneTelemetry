"""Async Redis response cache with graceful degradation.

Redis is *optional*: if the server can't be reached or
``settings.enable_response_cache`` is False, every method becomes a silent
no-op so the API behaves exactly as before. Callers don't need to special-case
the absence of Redis.

Cache key prefixes follow ``t1api:v2:<scope>:...`` so a single ``KEYS`` /
``SCAN`` operation can invalidate a whole scope from
:mod:`src.workers.processor` after a fresh ingest.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Iterable, Optional

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

_CACHE: Optional["RedisCache"] = None
_LOCK = asyncio.Lock()


def _stable_hash(value: Any) -> str:
    """Deterministic, short hash for cache-key suffixes."""
    blob = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]


def make_event_key(year: int, identifier: Any) -> str:
    return f"t1api:v2:event:{year}:{str(identifier).lower()}"


def make_season_key(year: int) -> str:
    return f"t1api:v2:season_events:{year}"


def make_plot_key(
    year: int, gp_id: str, session: str, data_type: str, params: Any = None
) -> str:
    suffix = f":{_stable_hash(params)}" if params else ""
    return f"t1api:v2:plot:{year}:{gp_id}:{session}:{data_type}{suffix}"


def make_latest_session_key() -> str:
    return "t1api:v2:latest_session"


def make_stream_key(year: int, round_nr: Any, session: str, stream_name: str) -> str:
    """Cache key for a single parsed livetiming stream (TimingData, TrackStatus, ...).

    Streams are large and immutable once a session is complete, so they get a
    long TTL (24h). Keeping them under the shared ``t1api:v2:`` prefix lets the
    processor invalidate a whole session scope after a fresh ingest.
    """
    session_slug = str(session).strip().lower().replace(" ", "_")
    return f"t1api:v2:stream:{year}:{round_nr}:{session_slug}:{stream_name}"


class RedisCache:
    """Thin async wrapper. Methods never raise — they return None / False
    when Redis is unavailable so callers can use a single code path."""

    def __init__(self) -> None:
        self._client = None
        self._enabled = bool(settings.enable_response_cache)
        self._ttl = max(1, int(settings.cache_ttl_seconds))

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    async def connect(self) -> bool:
        if not self._enabled:
            logger.info("Redis response cache disabled by settings")
            return False
        try:
            import redis.asyncio as redis_async  # type: ignore
        except ImportError:
            logger.warning("redis package not installed; response cache disabled")
            self._enabled = False
            return False

        try:
            self._client = redis_async.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password or None,
                decode_responses=False,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
                health_check_interval=30,
            )
            await self._client.ping()
            logger.info(
                "Redis response cache connected at %s:%s/%s",
                settings.redis_host, settings.redis_port, settings.redis_db,
            )
            return True
        except Exception as exc:
            logger.warning(
                "Redis unavailable at %s:%s — running without response cache (%s)",
                settings.redis_host, settings.redis_port, exc,
            )
            self._client = None
            return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    async def get_json(self, key: str) -> Optional[Any]:
        if not self.enabled:
            return None
        try:
            raw = await self._client.get(key)  # type: ignore[union-attr]
        except Exception as exc:
            logger.debug("Redis GET failed for %s: %s", key, exc)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    async def set_json(
        self, key: str, value: Any, ttl: Optional[int] = None
    ) -> bool:
        if not self.enabled:
            return False
        try:
            payload = json.dumps(value, default=str).encode("utf-8")
        except (TypeError, ValueError) as exc:
            logger.debug("Redis SET refused unserializable %s: %s", key, exc)
            return False
        try:
            await self._client.set(  # type: ignore[union-attr]
                key, payload, ex=ttl if ttl is not None else self._ttl,
            )
            return True
        except Exception as exc:
            logger.debug("Redis SET failed for %s: %s", key, exc)
            return False

    async def delete(self, *keys: str) -> int:
        if not self.enabled or not keys:
            return 0
        try:
            return int(await self._client.delete(*keys))  # type: ignore[union-attr]
        except Exception as exc:
            logger.debug("Redis DEL failed for %s: %s", keys, exc)
            return 0

    async def delete_pattern(self, pattern: str) -> int:
        """Delete every key matching the glob pattern via non-blocking SCAN."""
        if not self.enabled:
            return 0
        total = 0
        try:
            async for key in self._client.scan_iter(match=pattern, count=200):  # type: ignore[union-attr]
                try:
                    total += int(await self._client.delete(key))  # type: ignore[union-attr]
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("Redis SCAN/DEL failed for pattern %s: %s", pattern, exc)
        return total

    async def health(self) -> bool:
        if not self.enabled:
            return False
        try:
            return bool(await self._client.ping())  # type: ignore[union-attr]
        except Exception:
            return False


async def init_redis_cache() -> RedisCache:
    global _CACHE
    async with _LOCK:
        if _CACHE is None:
            _CACHE = RedisCache()
            await _CACHE.connect()
    return _CACHE


async def close_redis_cache() -> None:
    global _CACHE
    if _CACHE is not None:
        await _CACHE.close()
        _CACHE = None


def get_redis_cache() -> Optional[RedisCache]:
    """Return the initialized cache (or None if startup hasn't run).

    Callers in sync paths can use this to skip Redis entirely without
    needing to ``await`` anything.
    """
    return _CACHE


def get_redis_cache_or_noop() -> RedisCache:
    """Always return a usable cache object; a fresh disabled one if not init'd."""
    if _CACHE is not None:
        return _CACHE
    return RedisCache()  # disabled until .connect() is called


_SYNC_CLIENT = None
_SYNC_LOCK = None  # threading.Lock created lazily so import is cheap


class SyncRedisCache:
    """Sync facade used from threadpool code paths (plot generators, repos).

    Mirrors the no-op-on-failure semantics of :class:`RedisCache`. Holds its
    own connection because the async client cannot be safely shared across
    sync/async contexts.
    """

    def __init__(self) -> None:
        self._client = None
        self._enabled = bool(settings.enable_response_cache)
        self._ttl = max(1, int(settings.cache_ttl_seconds))
        if not self._enabled:
            return
        try:
            import redis  # type: ignore

            self._client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password or None,
                decode_responses=False,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
            )
            self._client.ping()
        except Exception as exc:
            logger.debug("Sync Redis unavailable: %s", exc)
            self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def get_json(self, key: str) -> Optional[Any]:
        if not self.enabled:
            return None
        try:
            raw = self._client.get(key)
        except Exception:
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def set_json(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        if not self.enabled:
            return False
        try:
            payload = json.dumps(value, default=str).encode("utf-8")
            self._client.set(key, payload, ex=ttl if ttl is not None else self._ttl)
            return True
        except Exception:
            return False

    def delete(self, *keys: str) -> int:
        if not self.enabled or not keys:
            return 0
        try:
            return int(self._client.delete(*keys))
        except Exception:
            return 0

    def delete_pattern(self, pattern: str) -> int:
        if not self.enabled:
            return 0
        total = 0
        try:
            for key in self._client.scan_iter(match=pattern, count=200):
                try:
                    total += int(self._client.delete(key))
                except Exception:
                    continue
        except Exception:
            pass
        return total


def get_sync_cache() -> SyncRedisCache:
    """Return the process-wide sync cache (lazy, never raises)."""
    import threading
    global _SYNC_CLIENT, _SYNC_LOCK
    if _SYNC_LOCK is None:
        _SYNC_LOCK = threading.Lock()
    if _SYNC_CLIENT is None:
        with _SYNC_LOCK:
            if _SYNC_CLIENT is None:
                _SYNC_CLIENT = SyncRedisCache()
    return _SYNC_CLIENT


__all__ = [
    "RedisCache",
    "SyncRedisCache",
    "init_redis_cache",
    "close_redis_cache",
    "get_redis_cache",
    "get_redis_cache_or_noop",
    "get_sync_cache",
    "make_event_key",
    "make_season_key",
    "make_plot_key",
    "make_latest_session_key",
    "make_stream_key",
]
