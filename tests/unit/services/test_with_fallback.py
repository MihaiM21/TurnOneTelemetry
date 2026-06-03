"""Tests for the bidirectional fallback helper in src/services/analysis/base.py."""
from __future__ import annotations

import pytest

from src.core.exceptions import (
    DataNotAvailableError,
    SessionNotFoundError,
    UpstreamUnavailableError,
)
from src.services.analysis.base import with_fallback


def _call(name, value, exc=None):
    def fn():
        if exc is not None:
            raise exc
        return value
    fn.__name__ = name
    return fn


def test_primary_success_returns_primary_result():
    primary = _call("primary", "v1-result")
    secondary = _call("secondary", "v2-result")

    result = with_fallback(primary, secondary, year=2026, gp=5, session="R", data_type="top_speed")

    assert result == "v1-result"


def test_primary_data_not_available_falls_back_to_secondary():
    primary = _call("primary", None, DataNotAvailableError(year=2026, source="fastf1", reason="lag"))
    secondary = _call("secondary", "v2-result")

    result = with_fallback(primary, secondary, year=2026, gp=5, session="R", data_type="top_speed")

    assert result == "v2-result"


def test_primary_upstream_unavailable_falls_back():
    primary = _call("primary", None, UpstreamUnavailableError(source="fastf1", reason="timeout"))
    secondary = _call("secondary", "v2-result")

    assert with_fallback(primary, secondary, data_type="top_speed") == "v2-result"


def test_session_not_found_does_not_fall_back():
    primary = _call("primary", None, SessionNotFoundError(year=2030, gp=99))
    called = {"secondary": False}

    def secondary():
        called["secondary"] = True
        return "v2-result"

    with pytest.raises(SessionNotFoundError):
        with_fallback(primary, secondary, year=2030, gp=99, session="R", data_type="top_speed")

    assert called["secondary"] is False


def test_both_sources_fail_raises_combined_error():
    primary = _call("primary", None, DataNotAvailableError(year=2026, source="fastf1", reason="lag"))
    secondary = _call("secondary", None, UpstreamUnavailableError(source="livetiming", reason="503"))

    with pytest.raises(DataNotAvailableError) as exc_info:
        with_fallback(
            primary, secondary,
            primary_source="fastf1", secondary_source="livetiming",
            year=2026, gp=5, session="R", data_type="top_speed",
        )

    assert "fastf1" in exc_info.value.sources_tried
    assert "livetiming" in exc_info.value.sources_tried


def test_no_secondary_propagates_primary_failure():
    primary = _call("primary", None, DataNotAvailableError(year=2026, source="fastf1", reason="lag"))

    with pytest.raises(DataNotAvailableError) as exc_info:
        with_fallback(primary, None, primary_source="fastf1", year=2026, data_type="top_speed")

    assert exc_info.value.sources_tried == ["fastf1"]


def test_unrelated_exception_is_not_caught():
    primary = _call("primary", None, RuntimeError("unrelated bug"))
    secondary = _call("secondary", "v2-result")

    with pytest.raises(RuntimeError):
        with_fallback(primary, secondary, data_type="top_speed")
