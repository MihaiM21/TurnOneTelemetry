"""Unit tests for src.core.security.rate_limiting."""
from unittest.mock import MagicMock

import pytest

from src.core.security import rate_limiting as rl


def _fake_request(api_key=None, ip="1.2.3.4"):
    req = MagicMock()
    req.headers = {"X-API-Key": api_key} if api_key else {}
    req.client = MagicMock()
    req.client.host = ip
    # slowapi.util.get_remote_address looks at request.client.host
    return req


def test_get_rate_limit_key_public_when_no_api_key():
    key = rl.get_rate_limit_key(_fake_request())
    assert key.startswith("public:")


def test_get_rate_limit_key_premium():
    key = rl.get_rate_limit_key(_fake_request(api_key="test-premium-key"))
    assert key == "premium:test-premium-key"


def test_get_rate_limit_key_standard():
    key = rl.get_rate_limit_key(_fake_request(api_key="test-standard-key"))
    assert key == "standard:test-standard-key"


def test_get_rate_limit_key_unknown_falls_back_to_public():
    key = rl.get_rate_limit_key(_fake_request(api_key="bogus-key"))
    assert key.startswith("public:")


def test_get_limiter_raises_before_init(monkeypatch):
    monkeypatch.setattr(rl, "_limiter_instance", None)
    with pytest.raises(RuntimeError):
        rl.get_limiter()


def test_init_limiter_idempotent_returns_instance(monkeypatch):
    monkeypatch.setattr(rl, "_limiter_instance", None)
    limiter = rl.init_limiter()
    assert limiter is not None
    assert rl.get_limiter() is limiter


def test_apply_tiered_limit_returns_callable(monkeypatch):
    monkeypatch.setattr(rl, "_limiter_instance", None)
    rl.init_limiter()
    assert callable(rl.apply_tiered_limit("public"))
    assert callable(rl.apply_tiered_limit("standard"))
    assert callable(rl.apply_tiered_limit("data"))
