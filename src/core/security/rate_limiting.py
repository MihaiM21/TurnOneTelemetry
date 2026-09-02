"""Rate limiting utilities for API endpoints"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from src.core.config import settings

# This will be initialized by server.py
_limiter_instance = None


def get_rate_limit_key(request):
    """
    Generate rate limit key based on IP and API tier.

    Recognises env-supplied keys *and* user-generated DB keys (via the Redis
    cache primed by ``verify_api_key``). Unknown / uncached keys are billed
    against the caller's IP at the public tier.
    """
    from src.core.security.api_keys import resolve_tier_sync

    api_key = request.headers.get(settings.api_key_name)
    ip_address = get_remote_address(request)
    tier, _key_hash, _prefix = resolve_tier_sync(api_key)
    if tier == "public":
        return f"public:{ip_address}"
    return f"{tier}:{api_key}"


def init_limiter():
    """Initialize the rate limiter - called from server.py"""
    global _limiter_instance
    _limiter_instance = Limiter(key_func=get_rate_limit_key)
    return _limiter_instance


def get_limiter():
    """Get the initialized rate limiter instance"""
    if _limiter_instance is None:
        raise RuntimeError("Rate limiter not initialized. Call init_limiter() first.")
    return _limiter_instance


def limits_for_tier(tier: str) -> dict:
    """Return the per-minute / per-hour / monthly-quota limits for a tier.

    Used by dashboard endpoints to surface "what your tier gets you" without
    duplicating the numbers from ``settings``.
    """
    if tier == "premium":
        return {
            "per_minute": settings.rate_limit_premium_per_minute,
            "per_hour": settings.rate_limit_premium_per_hour,
            "monthly_quota": settings.quota_premium_monthly,
        }
    if tier == "standard":
        return {
            "per_minute": settings.rate_limit_standard_per_minute,
            "per_hour": settings.rate_limit_standard_per_hour,
            "monthly_quota": settings.quota_standard_monthly,
        }
    return {
        "per_minute": settings.rate_limit_public_per_minute,
        "per_hour": settings.rate_limit_public_per_hour,
        "monthly_quota": settings.quota_public_monthly,
    }


def _tiered_limit_value(key: str) -> str:
    """Resolve the standard-endpoint rate-limit string for the caller's tier.

    slowapi calls a `limit_value` callable that declares a ``key`` parameter
    with the string produced by the route's key function (``get_rate_limit_key``
    by default -- see ``Limiter(key_func=...)`` in ``init_limiter``), which is
    always ``"{tier}:{identifier}"``. The tier is therefore just the prefix, so
    this reuses ``get_rate_limit_key``'s resolution instead of re-deriving it,
    and reuses ``limits_for_tier`` instead of duplicating the numbers.
    """
    tier = key.split(":", 1)[0]
    limits = limits_for_tier(tier)
    return f"{limits['per_minute']}/minute;{limits['per_hour']}/hour"


def apply_tiered_limit(endpoint_type: str = "standard"):
    """
    Apply tiered rate limiting based on endpoint type

    endpoint_type can be:
    - "public": Health checks, docs (30/min, 500/hour for unauthenticated)
    - "standard": Regular API endpoints -- limit actually varies by the
      caller's tier (public 30/min, standard 100/min, premium 300/min)
    - "data": Data-intensive endpoints (60/min for all, but separate counter)
    """
    limiter = get_limiter()

    if endpoint_type == "public":
        return limiter.limit(
            f"{settings.rate_limit_public_per_minute}/minute;"
            f"{settings.rate_limit_public_per_hour}/hour"
        )
    elif endpoint_type == "data":
        return limiter.limit(
            f"{settings.rate_limit_data_per_minute}/minute;"
            f"{settings.rate_limit_data_per_hour}/hour",
            key_func=lambda request: f"data:{get_rate_limit_key(request)}"
        )
    else:  # standard
        # Dynamic limit: slowapi re-evaluates this callable per request using
        # the route's key (see _tiered_limit_value docstring), so public,
        # standard and premium callers each get their own tier's ceiling
        # instead of everyone getting the premium one.
        return limiter.limit(_tiered_limit_value)
