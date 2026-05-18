"""JWT issuance / decoding using python-jose (HS256)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import JWTError, jwt

from src.core.config import settings


def _secret() -> str:
    secret = settings.jwt_secret_key or settings.api_secret_key
    if not secret:
        raise RuntimeError("jwt_secret_key (or api_secret_key) must be configured")
    return secret


def create_access_token(subject: str, extra: Optional[Dict[str, Any]] = None) -> str:
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_token_expire_minutes)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, _secret(), algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, _secret(), algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
