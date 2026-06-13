"""AES-256-GCM streaming encryption for backup artifacts.

Format on disk: ``[12-byte nonce][ciphertext][16-byte GCM tag]``.

The key comes from ``settings.backup_encryption_key`` (base64-encoded 32 bytes).
Each artifact gets a fresh random nonce. The key is never logged.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
KEY_BYTES = 32
CHUNK = 1024 * 1024  # 1 MiB


def load_key(b64_key: str) -> bytes:
    if not b64_key:
        raise ValueError("backup_encryption_key is not configured")
    try:
        # Accept both urlsafe and standard base64.
        raw = base64.urlsafe_b64decode(b64_key + "=" * (-len(b64_key) % 4))
    except Exception:
        raw = base64.b64decode(b64_key)
    if len(raw) != KEY_BYTES:
        raise ValueError(f"backup_encryption_key must decode to {KEY_BYTES} bytes, got {len(raw)}")
    return raw


def encrypt_file(src: Path, dst: Path, key: bytes) -> Tuple[int, bytes]:
    """Encrypt ``src`` into ``dst``. Returns (ciphertext_bytes_written, nonce).

    AESGCM has no native streaming API in ``cryptography`` — for backup
    archives (single-pass writers) we read the file fully. Backups are
    bounded by Mongo/SQLite size and are tractable; if archives grow beyond
    ~2 GiB we should switch to a chunked AEAD (e.g. miscreant STREAM).
    """
    nonce = os.urandom(NONCE_BYTES)
    aes = AESGCM(key)
    plaintext = src.read_bytes()
    ciphertext = aes.encrypt(nonce, plaintext, associated_data=None)
    with dst.open("wb") as fh:
        fh.write(nonce)
        fh.write(ciphertext)
    return len(ciphertext) + NONCE_BYTES, nonce


def decrypt_file(src: Path, dst: Path, key: bytes) -> int:
    """Decrypt ``src`` into ``dst``. Returns plaintext bytes written."""
    blob = src.read_bytes()
    if len(blob) < NONCE_BYTES + 16:
        raise ValueError(f"encrypted file too short: {src}")
    nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    aes = AESGCM(key)
    plaintext = aes.decrypt(nonce, ciphertext, associated_data=None)
    dst.write_bytes(plaintext)
    return len(plaintext)
