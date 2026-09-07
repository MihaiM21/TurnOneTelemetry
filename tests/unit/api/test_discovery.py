"""Offline tests for the V2 discovery endpoints.

``discovery_v2.router`` is not yet wired into ``create_app()`` (a concurrent
change owns that wiring), so these tests mount it on a throwaway ``FastAPI()``
app rather than going through ``src.api.app.create_app()``. This is
self-contained: it does not depend on, or race, that other change.

Because ``@apply_tiered_limit`` binds to the process-wide slowapi ``Limiter``
singleton at import time (see ``src/core/security/rate_limiting.py``), the
limiter must be initialized *before* ``discovery_v2`` is first imported --
otherwise the decorator raises ``RuntimeError``. This module owns that
sequencing itself instead of relying on ``tests/conftest.py``'s ``app``
fixture (which rebuilds the whole app via ``create_app()``).
"""
from __future__ import annotations

from typing import Any, Dict, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.security import rate_limiting as rl

# Initialize the limiter (if not already) and only then import the router, so
# its `@apply_tiered_limit` decorators bind successfully regardless of test
# collection order across the suite.
if rl._limiter_instance is None:
    rl.init_limiter()
rl._limiter_instance.enabled = False  # never actually enforce limits in tests

from src.api.routers import discovery_v2  # noqa: E402


AUTH = {"X-API-Key": "test-standard-key"}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(discovery_v2.router)
    app.state.limiter = rl._limiter_instance
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# --------------------------------------------------------------------------- #
# GET /api/v2/features
# --------------------------------------------------------------------------- #
def test_feature_catalog_returns_entries(client):
    resp = client.get("/api/v2/features", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()

    assert body["count"] == len(body["features"])
    assert body["count"] > 0
    keys = {f["key"] for f in body["features"]}
    assert "driver_pace" in keys  # an all-session singleton
    assert "teammate_battle" in keys  # a season-scope feature
    assert isinstance(body["groups"], list) and body["groups"]
    # every feature object has the documented shape
    sample = body["features"][0]
    for field in ("key", "label", "kind", "group", "applies_to", "cost", "session_scoped"):
        assert field in sample


def test_feature_catalog_session_filter_excludes_race_only(client):
    resp = client.get("/api/v2/features", params={"session": "Q"}, headers=AUTH)
    assert resp.status_code == 200
    keys = {f["key"] for f in resp.json()["features"]}

    # Quali-applicable singleton stays in.
    assert "qualifying_results" in keys
    # Race-only singletons are filtered out for a Q session.
    assert "position_changes" not in keys
    assert "pit_strategy" not in keys
    # Season-scope features are not session-scoped at all, so a session
    # filter drops them entirely.
    assert "teammate_battle" not in keys


def test_feature_catalog_accepts_long_form_session_name(client):
    """`simplify_session_name` should normalize 'Qualifying' the same as 'Q'."""
    resp_long = client.get("/api/v2/features", params={"session": "Qualifying"}, headers=AUTH)
    resp_short = client.get("/api/v2/features", params={"session": "Q"}, headers=AUTH)
    assert resp_long.status_code == resp_short.status_code == 200
    keys_long = {f["key"] for f in resp_long.json()["features"]}
    keys_short = {f["key"] for f in resp_short.json()["features"]}
    assert keys_long == keys_short


def test_feature_catalog_kind_filter(client):
    resp = client.get("/api/v2/features", params={"kind": "season"}, headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] > 0
    assert all(f["kind"] == "season" for f in body["features"])
    assert any(f["key"] == "teammate_battle" for f in body["features"])


def test_feature_catalog_invalid_kind_is_400(client):
    resp = client.get("/api/v2/features", params={"kind": "not-a-kind"}, headers=AUTH)
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# GET /api/v2/sessions/{year}/{gp}/{session}/availability
# --------------------------------------------------------------------------- #
def _fake_inventory(
    expected, present, missing, labels, *, event_name="Bahrain Grand Prix", round_nr=1
) -> Dict[str, Any]:
    return {
        "version": "v2",
        "scope": {"year": 2025, "gp": 1, "session": "Q"},
        "years_scanned": [2025],
        "total_sessions": 1,
        "total_expected_plots": len(expected),
        "total_missing_plots": len(missing),
        "total_extra_plots": 0,
        "grand_prix": [
            {
                "year": 2025,
                "round_nr": round_nr,
                "event_name": event_name,
                "sessions": [
                    {
                        "session_type": "Q",
                        "expected": expected,
                        "present": present,
                        "missing": missing,
                        "extra_groups": [],
                        "extra_count": 0,
                        "labels": labels,
                    }
                ],
            }
        ],
    }


def test_availability_reports_stored_vs_missing(client, monkeypatch):
    fake = _fake_inventory(
        expected=["top_speed_telemetry", "driver_pace"],
        present=["top_speed_telemetry"],
        missing=["driver_pace"],
        labels={"top_speed_telemetry": "Top Speed (telemetry)", "driver_pace": "Driver Pace"},
    )
    monkeypatch.setattr(discovery_v2, "compute_inventory", lambda **kwargs: fake)

    resp = client.get("/api/v2/sessions/2025/1/Q/availability", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()

    assert body["year"] == 2025
    assert body["gp"] == "Bahrain Grand Prix"
    assert body["round_nr"] == 1
    assert body["session"] == "Q"
    assert body["available"] == ["top_speed_telemetry"]
    assert body["missing"] == ["driver_pace"]
    assert body["drivers"] is None  # include_drivers defaults to false

    by_key = {f["data_type"]: f for f in body["features"]}
    assert by_key["top_speed_telemetry"]["available"] is True
    assert by_key["top_speed_telemetry"]["label"] == "Top Speed (telemetry)"
    assert by_key["driver_pace"]["available"] is False


def test_availability_invalid_session_is_400(client, monkeypatch):
    called = False

    def _boom(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("compute_inventory should not run for an invalid session")

    monkeypatch.setattr(discovery_v2, "compute_inventory", _boom)

    resp = client.get("/api/v2/sessions/2025/1/NotASession/availability", headers=AUTH)
    assert resp.status_code == 400
    assert not called


def test_availability_unknown_event_is_404(client, monkeypatch):
    monkeypatch.setattr(discovery_v2, "compute_inventory", lambda **kwargs: {"grand_prix": []})

    resp = client.get("/api/v2/sessions/2025/999/Q/availability", headers=AUTH)
    assert resp.status_code == 404


def test_availability_session_not_scheduled_is_404(client, monkeypatch):
    fake = {
        "grand_prix": [
            {"year": 2025, "round_nr": 1, "event_name": "Monaco Grand Prix", "sessions": []}
        ]
    }
    monkeypatch.setattr(discovery_v2, "compute_inventory", lambda **kwargs: fake)

    # A sprint session requested for an event whose schedule has none.
    resp = client.get("/api/v2/sessions/2025/1/S/availability", headers=AUTH)
    assert resp.status_code == 404


def test_availability_include_drivers_resolves_and_returns_list(client, monkeypatch):
    fake = _fake_inventory(
        expected=["top_speed_telemetry"],
        present=["top_speed_telemetry"],
        missing=[],
        labels={"top_speed_telemetry": "Top Speed (telemetry)"},
    )
    monkeypatch.setattr(discovery_v2, "compute_inventory", lambda **kwargs: fake)
    monkeypatch.setattr(discovery_v2, "session_drivers", lambda y, ident, e: ["VER", "NOR"])

    resp = client.get(
        "/api/v2/sessions/2025/1/Q/availability",
        params={"include_drivers": "true"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["drivers"] == ["VER", "NOR"]


def test_availability_include_drivers_failure_is_tolerated(client, monkeypatch):
    fake = _fake_inventory(
        expected=["top_speed_telemetry"],
        present=[],
        missing=["top_speed_telemetry"],
        labels={"top_speed_telemetry": "Top Speed (telemetry)"},
    )
    monkeypatch.setattr(discovery_v2, "compute_inventory", lambda **kwargs: fake)

    def _fail(y, ident, e):
        raise RuntimeError("livetiming unavailable")

    monkeypatch.setattr(discovery_v2, "session_drivers", _fail)

    resp = client.get(
        "/api/v2/sessions/2025/1/Q/availability",
        params={"include_drivers": "true"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["drivers"] == []
