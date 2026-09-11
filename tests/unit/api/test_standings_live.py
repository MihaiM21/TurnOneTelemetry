"""The /live standings endpoints bypass every cache layer."""
from __future__ import annotations

import pytest

from src.core.security import rate_limiting as rl

# `@apply_tiered_limit` binds to the limiter singleton at import time; make sure
# it exists before standings_v2 is first imported (mirrors tests/unit/api/test_discovery.py).
if rl._limiter_instance is None:
    rl.init_limiter()

from src.api.routers import standings_v2  # noqa: E402

pytestmark = pytest.mark.usefixtures("disable_rate_limit")

_FAKE = {
    "year": 2026,
    "round": None,
    "source": "livetiming",
    "standings": [
        {"position": 1, "points": 25.0, "wins": 1, "driver_code": "VER",
         "driver_name": "Max Verstappen", "team": "Red Bull Racing", "nationality": "NED"},
    ],
}


def test_live_drivers_standings_calls_uncached_path(client, monkeypatch, auth_headers):
    calls = {"live": 0, "cached": 0}

    def _live(year, round_nr=None):
        calls["live"] += 1
        return _FAKE

    def _cached(*a, **k):  # pragma: no cover - must not be hit
        calls["cached"] += 1
        raise AssertionError("live endpoint must not touch the cached generator")

    monkeypatch.setattr(standings_v2, "get_drivers_standings_live", _live)
    monkeypatch.setattr(standings_v2, "get_drivers_standings", _cached)

    resp = client.get("/api/v2/standings/drivers/live", headers=auth_headers())
    assert resp.status_code == 200
    assert resp.json()["standings"][0]["driver_code"] == "VER"
    assert calls == {"live": 1, "cached": 0}


def test_live_constructors_standings_calls_uncached_path(client, monkeypatch, auth_headers):
    seen = []
    monkeypatch.setattr(
        standings_v2, "get_constructors_standings_live",
        lambda year, round_nr=None: seen.append(year) or {
            "year": year, "round": None, "source": "livetiming",
            "standings": [{"position": 1, "points": 25.0, "wins": 1,
                           "team": "Red Bull Racing", "nationality": "NED"}],
        },
    )
    resp = client.get("/api/v2/standings/constructors/live", headers=auth_headers())
    assert resp.status_code == 200
    assert seen  # the current year was attempted


def test_live_standings_require_api_key(client):
    assert client.get("/api/v2/standings/drivers/live").status_code in (401, 403)
