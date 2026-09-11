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

import hashlib
import hmac
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


def _match_constant_time(candidate: str, allowed: list) -> bool:
    """Check membership without short-circuiting on the first differing byte.

    Plain ``in`` list membership uses per-string equality, which returns as
    soon as a mismatch is found -- letting response timing leak how many
    leading characters of a guess were correct. This keeps iterating over the
    whole list regardless of an early match so the timing is independent of
    where (or whether) the candidate matches.
    """
    found = False
    for k in allowed:
        if hmac.compare_digest(candidate, k):
            found = True  # keep iterating
    return found


def _key_fingerprint(api_key: str, length: int = 8) -> str:
    """Non-reversible, stable-per-key identifier for logs and metric labels.

    Never expose raw key bytes outside this module -- a log line or a
    Prometheus label is a much wider blast radius than the auth check itself.
    A SHA-256 digest is stable for a given key (so per-key metrics still
    aggregate correctly across requests) but reveals nothing usable about the
    actual secret, unlike a raw prefix/suffix slice.
    """
    return hashlib.sha256(api_key.encode()).hexdigest()[:length]


def _resolve_env_tier(api_key: str) -> Optional[str]:
    # Precedence: premium wins over standard, same as before.
    if _match_constant_time(api_key, settings.premium_api_keys_list):
        return "premium"
    if settings.allowed_api_keys_list and _match_constant_time(api_key, settings.allowed_api_keys_list):
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
        logger.warning("api_keys DB lookup failed for key %s: %s", _key_fingerprint(api_key), exc)
        return {"valid": False}

    if not doc:
        logger.warning("api_keys lookup miss: key %s not found in DB", _key_fingerprint(api_key))
        result = {"valid": False}
        _cache_set(key_hash, result, ttl=_NEG_TTL)
        return result

    result = {
        "valid": True,
        "key_hash": key_hash,
        "tier": doc.get("tier", "standard"),
        "owner_id": str(doc.get("owner_id")),
        "key_prefix": doc.get("key_prefix") or f"db:{_key_fingerprint(api_key)}",
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
    request: Request = None,  # type: ignore[assignment]
) -> str:
    """Validate an API key from the request header.

    Resolution order: dev bypass → env keys → DB keys (via Redis).
    """
    if (
        settings.environment == "development"
        and not settings.allowed_api_keys_list
        and settings.allow_insecure_dev_auth
    ):
        logger.warning(
            "Development mode: API key check bypassed (ALLOW_INSECURE_DEV_AUTH=true). "
            "Authentication is disabled for this request."
        )
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
            request, tier=env_tier, key_hash=None, key_prefix=f"env:{_key_fingerprint(api_key)}"
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

    logger.warning("Invalid API key attempted: %s", _key_fingerprint(api_key))
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
    request: Request = None,  # type: ignore[assignment]
) -> Tuple[Optional[str], str]:
    """Return ``(api_key, tier)`` for rate limiting / metrics."""
    if not api_key:
        return None, "public"

    if (
        settings.environment == "development"
        and not settings.allowed_api_keys_list
        and settings.allow_insecure_dev_auth
    ):
        logger.warning(
            "Development mode: API key tier check bypassed (ALLOW_INSECURE_DEV_AUTH=true). "
            "Authentication is disabled for this request."
        )
        _stash_resolution(request, tier="standard", key_hash=None, key_prefix="dev")
        return "dev-key", "standard"

    env_tier = _resolve_env_tier(api_key)
    if env_tier:
        _stash_resolution(
            request, tier=env_tier, key_hash=None, key_prefix=f"env:{_key_fingerprint(api_key)}"
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
        return env_tier, None, f"env:{_key_fingerprint(api_key)}"
    from src.repositories.api_keys import hash_key

    cached = _cache_get(hash_key(api_key))
    if cached and cached.get("valid"):
        return cached["tier"], cached["key_hash"], cached.get("key_prefix")
    return "public", None, None
