"""Unit tests for src.core.config.Settings parsing."""
import pytest

from src.core.config import Settings


def _settings(**overrides) -> Settings:
    base = {"mongodb_password": "x"}
    base.update(overrides)
    return Settings(**base)


def test_allowed_api_keys_empty():
    s = _settings(allowed_api_keys="")
    assert s.allowed_api_keys_list == []


def test_allowed_api_keys_single():
    s = _settings(allowed_api_keys="a")
    assert s.allowed_api_keys_list == ["a"]


def test_allowed_api_keys_strips_whitespace():
    s = _settings(allowed_api_keys=" a , b ,c ")
    assert s.allowed_api_keys_list == ["a", "b", "c"]


def test_allowed_api_keys_skips_empty_segments():
    s = _settings(allowed_api_keys="a,,b,   ,c")
    assert s.allowed_api_keys_list == ["a", "b", "c"]


def test_premium_api_keys_parses_like_allowed():
    s = _settings(premium_api_keys="p1, p2")
    assert s.premium_api_keys_list == ["p1", "p2"]


def test_cors_origins_default():
    s = _settings()
    assert "http://localhost:3000" in s.cors_origins_list
    assert "http://localhost:8000" in s.cors_origins_list


def test_cors_methods_list():
    s = _settings(cors_allow_methods="GET, POST, OPTIONS")
    assert s.cors_methods_list == ["GET", "POST", "OPTIONS"]


def test_valid_sessions_list_includes_known_codes():
    s = _settings()
    sessions = s.valid_sessions_list
    assert "Q" in sessions
    assert "R" in sessions
    assert "Race" in sessions


def test_environment_can_be_set():
    s = _settings(environment="staging")
    assert s.environment == "staging"


def test_rate_limit_defaults_are_tiered():
    s = _settings()
    assert s.rate_limit_public_per_minute < s.rate_limit_standard_per_minute
    assert s.rate_limit_standard_per_minute < s.rate_limit_premium_per_minute
