"""Unit tests for src.core.security.api_keys."""
import pytest
from fastapi import HTTPException

from src.core.security import api_keys as ak
from src.core.config import settings


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
