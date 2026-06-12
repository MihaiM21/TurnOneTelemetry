"""Encrypt/decrypt round-trip tests for the backup AES-256-GCM helper."""
from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from src.services.backup.encryption import KEY_BYTES, decrypt_file, encrypt_file, load_key


@pytest.fixture
def key() -> bytes:
    return os.urandom(KEY_BYTES)


@pytest.fixture
def b64_key(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode()


def test_load_key_round_trip(key: bytes, b64_key: str):
    assert load_key(b64_key) == key


def test_load_key_rejects_short_key():
    short = base64.urlsafe_b64encode(b"\x00" * 8).decode()
    with pytest.raises(ValueError, match="32 bytes"):
        load_key(short)


def test_load_key_rejects_empty():
    with pytest.raises(ValueError, match="not configured"):
        load_key("")


def test_encrypt_decrypt_round_trip(tmp_path: Path, key: bytes):
    plaintext = b"hello f1 telemetry " * 5000
    src = tmp_path / "plain.bin"
    src.write_bytes(plaintext)

    enc = tmp_path / "plain.bin.enc"
    written, nonce = encrypt_file(src, enc, key)
    assert written == enc.stat().st_size
    assert len(nonce) == 12
    assert enc.read_bytes() != plaintext  # actually encrypted

    out = tmp_path / "out.bin"
    decrypt_file(enc, out, key)
    assert out.read_bytes() == plaintext


def test_decrypt_with_wrong_key_fails(tmp_path: Path, key: bytes):
    src = tmp_path / "p.bin"
    src.write_bytes(b"secret")
    enc = tmp_path / "p.enc"
    encrypt_file(src, enc, key)

    wrong = os.urandom(KEY_BYTES)
    with pytest.raises(Exception):
        decrypt_file(enc, tmp_path / "out.bin", wrong)


def test_decrypt_truncated_fails(tmp_path: Path, key: bytes):
    bad = tmp_path / "bad.enc"
    bad.write_bytes(b"\x00" * 4)
    with pytest.raises(ValueError, match="too short"):
        decrypt_file(bad, tmp_path / "out.bin", key)
