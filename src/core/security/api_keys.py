"""API key authentication.

Two key sources coexist:

* **Env-supplied keys** (``ALLOWED_API_KEYS``, ``PREMIUM_API_KEYS``) — the
  original mechanism, used by internal tools and the website. These take
  precedence and never hit the database.
* **User-generated keys** stored in MongoDB and self-served via
  ``/api/keys``. Lookups are cached in Redis (short TTL) so the per-request
  cost is a single Redis ``GET`` after the first hit.
"""
from __future__ import annotations

from typing import Optional, Tuple

from fastapi import HTTPException, Request, Security, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import APIKeyHeader

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

api_key_header = APIKeyHeader(name=settings.api_key_name, auto_error=False)

# Redis namespace for the cached {key_hash -> resolution} mapping.
_CACHE_PREFIX = "t1api:auth:key:"
_NEG_TTL = 30


def _cache_get(key_hash: str) -> Optional[dict]:
    from src.core.cache.redis_cache import get_sync_cache

    return get_sync_cache().get_json(f"{_CACHE_PREFIX}{key_hash}")


def _cache_set(key_hash: str, value: dict, ttl: int) -> None:
    from src.core.cache.redis_cache import get_sync_cache

    get_sync_cache().set_json(f"{_CACHE_PREFIX}{key_hash}", value, ttl=ttl)


def invalidate_key_cache(key_hash: str) -> None:
    from src.core.cache.redis_cache import get_sync_cache

    get_sync_cache().delete(f"{_CACHE_PREFIX}{key_hash}")


def _resolve_env_tier(api_key: str) -> Optional[str]:
    if api_key in settings.premium_api_keys_list:
        return "premium"
    if settings.allowed_api_keys_list and api_key in settings.allowed_api_keys_list:
        return "standard"
    return None


def _resolve_db_sync(api_key: str) -> dict:
    """Look up a user-generated key. Returns ``{"valid": bool, ...}``.

    Hits Redis first; on miss queries Mongo and primes the cache. Mongo
    failures fall through as ``invalid`` so a DB outage cannot crash the
    auth middleware.
    """
    from src.repositories.api_keys import find_active_by_hash, hash_key

    key_hash = hash_key(api_key)
    cached = _cache_get(key_hash)
    if cached is not None:
        return cached

    try:
        doc = find_active_by_hash(key_hash)
    except Exception as exc:
        logger.warning("api_keys DB lookup failed for prefix %s...: %s", api_key[:11], exc)
        return {"valid": False}

    if not doc:
        logger.warning("api_keys lookup miss: key prefix %s... not found in DB", api_key[:11])
        result = {"valid": False}
        _cache_set(key_hash, result, ttl=_NEG_TTL)
        return result

    result = {
        "valid": True,
        "key_hash": key_hash,
        "tier": doc.get("tier", "standard"),
        "owner_id": str(doc.get("owner_id")),
        "key_prefix": doc.get("key_prefix", api_key[:11]),
    }
    _cache_set(key_hash, result, ttl=settings.api_key_cache_ttl_seconds)
    return result


def _stash_resolution(
    request: Optional[Request],
    *,
    tier: str,
    key_hash: Optional[str],
    key_prefix: Optional[str],
) -> None:
    """Stash the resolved key info on request.state so the monitoring
    middleware can record per-key usage without re-querying Redis. Critical
    for environments where Redis is disabled/unavailable — without this the
    middleware's sync resolve falls through to ``public`` and usage writes
    are skipped."""
    if request is None:
        return
    request.state.api_key_resolution = {
        "tier": tier,
        "key_hash": key_hash,
        "key_prefix": key_prefix,
    }


async def verify_api_key(
    api_key: Optional[str] = Security(api_key_header),
    request: Optional[Request] = None,
) -> str:
    """Validate an API key from the request header.

    Resolution order: dev bypass → env keys → DB keys (via Redis).
    """
    if settings.environment == "development" and not settings.allowed_api_keys_list:
        logger.debug("Development mode: API key check bypassed")
        _stash_resolution(request, tier="standard", key_hash=None, key_prefix="dev")
        return "dev-key"

    if not api_key:
        logger.warning("API key missing from request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    env_tier = _resolve_env_tier(api_key)
    if env_tier:
        _stash_resolution(
            request, tier=env_tier, key_hash=None, key_prefix=f"env:{api_key[:6]}"
        )
        return api_key

    resolved = await run_in_threadpool(_resolve_db_sync, api_key)
    if resolved.get("valid"):
        _stash_resolution(
            request,
            tier=resolved.get("tier", "standard"),
            key_hash=resolved.get("key_hash"),
            key_prefix=resolved.get("key_prefix"),
        )
        return api_key

    logger.warning("Invalid API key attempted: %s...", api_key[:8])
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid API key",
    )


def get_optional_api_key(api_key: Optional[str] = Security(api_key_header)) -> Optional[str]:
    """Loose check — returns the key if it looks valid (env or cached DB), else None.

    Does not query MongoDB; DB-backed keys are recognised only once they've
    been cached by a prior ``verify_api_key`` call.
    """
    if not api_key:
        return None
    if _resolve_env_tier(api_key):
        return api_key
    from src.repositories.api_keys import hash_key

    cached = _cache_get(hash_key(api_key))
    if cached and cached.get("valid"):
        return api_key
    return None


async def get_api_key_tier(
    api_key: Optional[str] = Security(api_key_header),
    request: Optional[Request] = None,
) -> Tuple[Optional[str], str]:
    """Return ``(api_key, tier)`` for rate limiting / metrics."""
    if not api_key:
        return None, "public"

    if settings.environment == "development" and not settings.allowed_api_keys_list:
        _stash_resolution(request, tier="standard", key_hash=None, key_prefix="dev")
        return "dev-key", "standard"

    env_tier = _resolve_env_tier(api_key)
    if env_tier:
        _stash_resolution(
            request, tier=env_tier, key_hash=None, key_prefix=f"env:{api_key[:6]}"
        )
        return api_key, env_tier

    resolved = await run_in_threadpool(_resolve_db_sync, api_key)
    if resolved.get("valid"):
        _stash_resolution(
            request,
            tier=resolved.get("tier", "standard"),
            key_hash=resolved.get("key_hash"),
            key_prefix=resolved.get("key_prefix"),
        )
        return api_key, resolved["tier"]

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid API key",
    )


def resolve_tier_sync(api_key: Optional[str]) -> Tuple[str, Optional[str], Optional[str]]:
    """Sync helper for the rate-limit key function and request middleware.

    Returns ``(tier, key_hash, key_prefix)``. Uses only env lookups + Redis;
    never queries Mongo from the sync path. Unknown keys fall through to
    ``"public"`` so the limiter still bills them.
    """
    if not api_key:
        return "public", None, None
    env_tier = _resolve_env_tier(api_key)
    if env_tier:
        return env_tier, None, f"env:{api_key[:6]}"
    from src.repositories.api_keys import hash_key

    cached = _cache_get(hash_key(api_key))
    if cached and cached.get("valid"):
        return cached["tier"], cached["key_hash"], cached.get("key_prefix")
    return "public", None, None
