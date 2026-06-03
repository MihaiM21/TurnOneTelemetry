"""Unit tests for src.core.security.api_keys."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.core.security import api_keys as ak
from src.core.config import settings


def _fake_request():
    return SimpleNamespace(state=SimpleNamespace())


@pytest.mark.asyncio
async def test_verify_api_key_valid_standard():
    result = await ak.verify_api_key("test-standard-key")
    assert result == "test-standard-key"


@pytest.mark.asyncio
async def test_verify_api_key_valid_premium():
    result = await ak.verify_api_key("test-premium-key")
    assert result == "test-premium-key"


@pytest.mark.asyncio
async def test_verify_api_key_missing_raises_401():
    with pytest.raises(HTTPException) as exc:
        await ak.verify_api_key(None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_api_key_invalid_raises_403():
    with pytest.raises(HTTPException) as exc:
        await ak.verify_api_key("nope-not-a-real-key")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_verify_api_key_dev_bypass(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "allowed_api_keys", "")
    result = await ak.verify_api_key(None)
    assert result == "dev-key"


def test_get_optional_api_key_none_when_missing():
    assert ak.get_optional_api_key(None) is None


def test_get_optional_api_key_none_when_invalid():
    assert ak.get_optional_api_key("bogus") is None


def test_get_optional_api_key_returns_valid():
    assert ak.get_optional_api_key("test-standard-key") == "test-standard-key"


@pytest.mark.asyncio
async def test_get_api_key_tier_public_when_missing():
    key, tier = await ak.get_api_key_tier(None)
    assert key is None
    assert tier == "public"


@pytest.mark.asyncio
async def test_get_api_key_tier_standard():
    key, tier = await ak.get_api_key_tier("test-standard-key")
    assert tier == "standard"


@pytest.mark.asyncio
async def test_get_api_key_tier_premium():
    key, tier = await ak.get_api_key_tier("test-premium-key")
    assert tier == "premium"


@pytest.mark.asyncio
async def test_get_api_key_tier_invalid_raises():
    with pytest.raises(HTTPException) as exc:
        await ak.get_api_key_tier("nope")
    assert exc.value.status_code == 403


def test_stash_resolution_noop_when_request_none():
    # Should not raise when request is missing.
    ak._stash_resolution(None, tier="standard", key_hash=None, key_prefix=None)


def test_stash_resolution_writes_to_state():
    req = _fake_request()
    ak._stash_resolution(req, tier="premium", key_hash="h", key_prefix="p")
    assert req.state.api_key_resolution == {
        "tier": "premium",
        "key_hash": "h",
        "key_prefix": "p",
    }


@pytest.mark.asyncio
async def test_verify_api_key_stashes_env_resolution():
    req = _fake_request()
    await ak.verify_api_key("test-premium-key", request=req)
    res = req.state.api_key_resolution
    assert res["tier"] == "premium"
    assert res["key_hash"] is None
    assert res["key_prefix"].startswith("env:")


@pytest.mark.asyncio
async def test_verify_api_key_dev_bypass_stashes(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "allowed_api_keys", "")
    req = _fake_request()
    result = await ak.verify_api_key(None, request=req)
    assert result == "dev-key"
    assert req.state.api_key_resolution["tier"] == "standard"
    assert req.state.api_key_resolution["key_hash"] is None


@pytest.mark.asyncio
async def test_get_api_key_tier_stashes_env_resolution():
    req = _fake_request()
    key, tier = await ak.get_api_key_tier("test-standard-key", request=req)
    assert tier == "standard"
    assert req.state.api_key_resolution["tier"] == "standard"
    assert req.state.api_key_resolution["key_prefix"].startswith("env:")


@pytest.mark.asyncio
async def test_get_api_key_tier_dev_bypass_stashes(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "allowed_api_keys", "")
    req = _fake_request()
    key, tier = await ak.get_api_key_tier("anything", request=req)
    assert tier == "standard"
    assert req.state.api_key_resolution["key_prefix"] == "dev"
