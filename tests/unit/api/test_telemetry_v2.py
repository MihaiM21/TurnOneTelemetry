"""Offline tests for the V2 telemetry endpoints.

Mirrors ``tests/unit/api/test_discovery.py``: ``@apply_tiered_limit`` binds to
the process-wide slowapi ``Limiter`` singleton at import time (see
``src/core/security/rate_limiting.py``), so the limiter must be initialized
*before* ``telemetry_v2`` is first imported -- otherwise the decorator raises
``RuntimeError``. This module owns that sequencing itself rather than relying
on ``tests/conftest.py``'s ``app`` fixture (which rebuilds the whole app via
``create_app()``), and mounts the router on a throwaway ``FastAPI()`` app.

``telemetry_v2.py`` lets ``ValueError`` propagate to a global handler
registered in ``src/api/app.py`` (around line 598, returning 400 with
``{"detail": str(exc)}``). A throwaway app does not have that handler, so it
is mirrored here.
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, List

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.core.security import rate_limiting as rl

# Initialize the limiter (if not already) and only then import the router, so
# its `@apply_tiered_limit` decorators bind successfully regardless of test
# collection order across the suite.
if rl._limiter_instance is None:
    rl.init_limiter()
rl._limiter_instance.enabled = False  # never actually enforce limits in tests

from src.api.routers import telemetry_v2  # noqa: E402


AUTH = {"X-API-Key": "test-standard-key"}


class _RecordingStub:
    """Callable stub that records the args/kwargs it was invoked with."""

    def __init__(self, payload: Dict[str, Any]):
        self._payload = payload
        self.calls: List[Any] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._payload

    @property
    def last_args(self):
        return self.calls[-1][0]

    @property
    def last_kwargs(self):
        return self.calls[-1][1]


class _RaisingStub:
    """Callable stub that raises a canned exception when invoked."""

    def __init__(self, exc: Exception):
        self._exc = exc
        self.calls: List[Any] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise self._exc


_LAPS_PAYLOAD = {
    "session_info": {"year": 2025, "event_name": "Bahrain Grand Prix", "session_name": "R", "round": 1},
    "range": {
        "lap_from": 10, "lap_to": 12, "hz": 10, "format": "frames",
        "precision": 2, "frame_count": 60, "driver_laps": 2,
    },
    "drivers": [],
    "track": None,
}

_TRACK_PAYLOAD = {
    "session_info": {"year": 2025, "event_name": "Bahrain Grand Prix", "session_name": "R", "round": 1},
    "track": {"rotation": 0, "corners": [], "outline": []},
}


@pytest.fixture()
def app() -> FastAPI:
    fastapi_app = FastAPI()
    fastapi_app.include_router(telemetry_v2.router)
    fastapi_app.state.limiter = rl._limiter_instance

    @fastapi_app.exception_handler(ValueError)
    async def _value_error_handler(request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return fastapi_app


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# --------------------------------------------------------------------------- #
# GET /api/v2/telemetry/laps-data
# --------------------------------------------------------------------------- #
def test_laps_data_returns_200_and_passes_normalized_drivers_and_laps(client, monkeypatch):
    stub = _RecordingStub(_LAPS_PAYLOAD)
    monkeypatch.setattr(telemetry_v2, "LapsData", lambda: stub)

    resp = client.get(
        "/api/v2/telemetry/laps-data",
        params={"drivers": "VER,HAM", "lap_from": 10, "lap_to": 12},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json() == _LAPS_PAYLOAD

    args = stub.last_args
    # LapsData()(year, gp, session, driver_list, lap_from, lap_to, hz, format, precision, include_track)
    assert args[3] == ["VER", "HAM"]
    assert args[4] == 10
    assert args[5] == 12


def test_laps_data_normalizes_lowercase_and_spaced_driver_list(client, monkeypatch):
    stub = _RecordingStub(_LAPS_PAYLOAD)
    monkeypatch.setattr(telemetry_v2, "LapsData", lambda: stub)

    resp = client.get(
        "/api/v2/telemetry/laps-data",
        params={"drivers": "ver, ham", "lap_from": 1},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert stub.last_args[3] == ["VER", "HAM"]


def test_laps_data_defaults_lap_to_to_lap_from_when_omitted(client, monkeypatch):
    stub = _RecordingStub(_LAPS_PAYLOAD)
    monkeypatch.setattr(telemetry_v2, "LapsData", lambda: stub)

    resp = client.get(
        "/api/v2/telemetry/laps-data",
        params={"drivers": "VER", "lap_from": 5},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert stub.last_args[4] == 5
    assert stub.last_args[5] == 5


def test_laps_data_lap_to_less_than_lap_from_is_400(client, monkeypatch):
    stub = _RecordingStub(_LAPS_PAYLOAD)
    monkeypatch.setattr(telemetry_v2, "LapsData", lambda: stub)

    resp = client.get(
        "/api/v2/telemetry/laps-data",
        params={"drivers": "VER", "lap_from": 10, "lap_to": 5},
        headers=AUTH,
    )
    assert resp.status_code == 400
    assert stub.calls == []


def test_laps_data_format_columnar_is_passed_through(client, monkeypatch):
    stub = _RecordingStub(_LAPS_PAYLOAD)
    monkeypatch.setattr(telemetry_v2, "LapsData", lambda: stub)

    resp = client.get(
        "/api/v2/telemetry/laps-data",
        params={"drivers": "VER", "lap_from": 1, "format": "columnar"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert stub.last_args[7] == "columnar"


def test_laps_data_unknown_format_is_400_not_silently_defaulted(client, monkeypatch):
    stub = _RecordingStub(_LAPS_PAYLOAD)
    monkeypatch.setattr(telemetry_v2, "LapsData", lambda: stub)

    resp = client.get(
        "/api/v2/telemetry/laps-data",
        params={"drivers": "VER", "lap_from": 1, "format": "xml"},
        headers=AUTH,
    )
    assert resp.status_code == 400
    assert stub.calls == []


def test_laps_data_empty_drivers_is_400(client, monkeypatch):
    stub = _RecordingStub(_LAPS_PAYLOAD)
    monkeypatch.setattr(telemetry_v2, "LapsData", lambda: stub)

    resp = client.get(
        "/api/v2/telemetry/laps-data",
        params={"drivers": "", "lap_from": 1},
        headers=AUTH,
    )
    assert resp.status_code == 400
    assert stub.calls == []


def test_laps_data_only_commas_drivers_is_400(client, monkeypatch):
    stub = _RecordingStub(_LAPS_PAYLOAD)
    monkeypatch.setattr(telemetry_v2, "LapsData", lambda: stub)

    resp = client.get(
        "/api/v2/telemetry/laps-data",
        params={"drivers": ",,,", "lap_from": 1},
        headers=AUTH,
    )
    assert resp.status_code == 400
    assert stub.calls == []


def test_laps_data_missing_api_key_is_401(client, monkeypatch):
    stub = _RecordingStub(_LAPS_PAYLOAD)
    monkeypatch.setattr(telemetry_v2, "LapsData", lambda: stub)

    resp = client.get(
        "/api/v2/telemetry/laps-data",
        params={"drivers": "VER", "lap_from": 1},
    )
    assert resp.status_code == 401
    assert stub.calls == []


def test_laps_data_hz_out_of_range_is_422(client, monkeypatch):
    stub = _RecordingStub(_LAPS_PAYLOAD)
    monkeypatch.setattr(telemetry_v2, "LapsData", lambda: stub)

    resp = client.get(
        "/api/v2/telemetry/laps-data",
        params={"drivers": "VER", "lap_from": 1, "hz": 21},
        headers=AUTH,
    )
    assert resp.status_code == 422
    assert stub.calls == []


def test_laps_data_service_value_error_becomes_400_with_message(client, monkeypatch):
    stub = _RaisingStub(ValueError("42 driver-laps x 600 frames, over the limit of 20000."))
    monkeypatch.setattr(telemetry_v2, "LapsData", lambda: stub)

    resp = client.get(
        "/api/v2/telemetry/laps-data",
        params={"drivers": "VER,HAM", "lap_from": 1, "lap_to": 50},
        headers=AUTH,
    )
    assert resp.status_code == 400
    assert "over the limit" in resp.json()["detail"]


# --------------------------------------------------------------------------- #
# GET /api/v2/telemetry/track-map
# --------------------------------------------------------------------------- #
def test_track_map_returns_200_and_calls_service_with_year_gp_session(client, monkeypatch):
    stub = _RecordingStub(_TRACK_PAYLOAD)
    monkeypatch.setattr(telemetry_v2, "TrackMapData", lambda: stub)

    resp = client.get(
        "/api/v2/telemetry/track-map",
        params={"year": 2025, "gp": 1, "session": "R"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json() == _TRACK_PAYLOAD
    # `gp` is typed `Union[int, str]`; FastAPI resolves a plain digit string to
    # the `str` branch rather than coercing it to `int`.
    assert stub.last_args == (2025, "1", "R")


def test_laps_data_include_track_true_is_passed_through_as_boolean(client, monkeypatch):
    stub = _RecordingStub(_LAPS_PAYLOAD)
    monkeypatch.setattr(telemetry_v2, "LapsData", lambda: stub)

    resp = client.get(
        "/api/v2/telemetry/laps-data",
        params={"drivers": "VER", "lap_from": 1, "include_track": "true"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert stub.last_args[9] is True
