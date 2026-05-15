"""
Authentication and authorization utilities
"""

from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader
from typing import Optional, Tuple
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

# API Key authentication
api_key_header = APIKeyHeader(name=settings.api_key_name, auto_error=False)


async def verify_api_key(api_key: Optional[str] = Security(api_key_header)) -> str:
    """
    Verify API key from request header
    
    Args:
        api_key: API key from header
        
    Returns:
        Validated API key
        
    Raises:
        HTTPException: If API key is missing or invalid
    """
    if settings.environment == "development" and not settings.allowed_api_keys_list:
        # In development with no keys configured, allow all requests
        logger.debug("Development mode: API key check bypassed")
        return "dev-key"
    
    if not api_key:
        logger.warning("API key missing from request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    
    if settings.allowed_api_keys_list and api_key not in settings.allowed_api_keys_list:
        logger.warning(f"Invalid API key attempted: {api_key[:8]}...")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key"
        )
    
    logger.debug("API key validated successfully")
    return api_key


def get_optional_api_key(api_key: Optional[str] = Security(api_key_header)) -> Optional[str]:
    """
    Optional API key verification (doesn't raise exception)
    
    Args:
        api_key: API key from header
        
    Returns:
        API key if valid, None otherwise
    """
    if not api_key:
        return None
    
    if settings.allowed_api_keys_list and api_key not in settings.allowed_api_keys_list:
        return None
    
    return api_key


async def get_api_key_tier(api_key: Optional[str] = Security(api_key_header)) -> Tuple[Optional[str], str]:
    """
    Get API key and determine its tier for rate limiting
    
    Args:
        api_key: API key from header
        
    Returns:
        Tuple of (api_key, tier) where tier is 'public', 'standard', or 'premium'
    """
    if not api_key:
        return None, 'public'
    
    # In development with no keys configured, treat as standard
    if settings.environment == "development" and not settings.allowed_api_keys_list:
        return "dev-key", 'standard'
    
    if settings.allowed_api_keys_list and api_key not in settings.allowed_api_keys_list:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key"
        )
    
    # Check if it's a premium key (for website usage)
    if api_key in settings.premium_api_keys_list:
        logger.debug(f"Premium tier API key detected")
        return api_key, 'premium'
    
    logger.debug(f"Standard tier API key detected")
    return api_key, 'standard'
