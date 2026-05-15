"""Rate limiting utilities for API endpoints"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from src.core.config import settings

# This will be initialized by server.py
_limiter_instance = None

def get_rate_limit_key(request):
    """
    Generate rate limit key based on IP and API tier
    This allows different rate limits for different authentication levels
    """
    api_key = request.headers.get("X-API-Key")
    ip_address = get_remote_address(request)
    
    if not api_key:
        return f"public:{ip_address}"
    
    if api_key in settings.premium_api_keys_list:
        return f"premium:{api_key}"
    elif api_key in settings.allowed_api_keys_list:
        return f"standard:{api_key}"
    else:
        return f"public:{ip_address}"

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

def apply_tiered_limit(endpoint_type: str = "standard"):
    """
    Apply tiered rate limiting based on endpoint type
    
    endpoint_type can be:
    - "public": Health checks, docs (30/min, 500/hour for unauthenticated)
    - "standard": Regular API endpoints (100/min standard, 300/min premium)
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
        # For standard endpoints, we use multiple limits based on tier
        # Premium keys get 300/min, standard gets 100/min, public gets 30/min
        return limiter.limit(
            f"{settings.rate_limit_premium_per_minute}/minute;"
            f"{settings.rate_limit_premium_per_hour}/hour"
        )
