"""Password hashing helpers (bcrypt)."""
from __future__ import annotations

import hashlib

import bcrypt

from src.core.config import settings


def _prehash(password: str) -> bytes:
    # SHA-256 hex digest = 64 ASCII bytes, always under bcrypt's 72-byte limit.
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("utf-8")


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    return bcrypt.hashpw(_prehash(password), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("utf-8"))
    except Exception:
        return False
