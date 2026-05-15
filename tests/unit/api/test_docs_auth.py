"""Unit tests for src.api.docs_auth token helpers."""
from freezegun import freeze_time

from src.api.docs_auth import _make_token, _sign, _verify_token, SESSION_TTL


SECRET = "unit-test-secret"


def test_sign_is_deterministic():
    assert _sign("payload", SECRET) == _sign("payload", SECRET)


def test_sign_differs_per_secret():
    assert _sign("payload", "s1") != _sign("payload", "s2")


def test_make_and_verify_token_roundtrip():
    token = _make_token(SECRET)
    assert _verify_token(token, SECRET) is True


def test_verify_token_rejects_wrong_secret():
    token = _make_token(SECRET)
    assert _verify_token(token, "different-secret") is False


def test_verify_token_rejects_tampered_signature():
    token = _make_token(SECRET)
    ts, nonce, _ = token.split(".")
    tampered = f"{ts}.{nonce}.{'0' * 64}"
    assert _verify_token(tampered, SECRET) is False


def test_verify_token_rejects_malformed_token():
    assert _verify_token("not.a.valid", SECRET) is False
    assert _verify_token("only-one-part", SECRET) is False
    assert _verify_token("", SECRET) is False


def test_verify_token_rejects_expired_token():
    with freeze_time("2026-01-01 00:00:00") as frozen:
        token = _make_token(SECRET)
        assert _verify_token(token, SECRET) is True
        frozen.tick(delta=SESSION_TTL + 60)
        assert _verify_token(token, SECRET) is False
