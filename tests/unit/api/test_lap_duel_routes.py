"""Offline route tests for ``/api/v2/lap-duel-plot`` and ``/api/v2/lap-duel-data``.

Same shape as ``test_telemetry_v2.py``: the slowapi limiter has to exist before
``analysis_v2`` is imported (its ``@apply_tiered_limit`` decorators bind at
import time), and the router is mounted on a throwaway app that mirrors the
global ``ValueError -> 400`` handler from ``src/api/app.py``.

The service classes are stubbed where the router imports them. The two
validation tests deliberately use the *real* classes: they raise ``ValueError``
before anything is fetched, so they prove the ValueError -> 400 path end to end
without the network.
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, List

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.core.security import rate_limiting as rl

if rl._limiter_instance is None:
    rl.init_limiter()
rl._limiter_instance.enabled = False  # never actually enforce limits in tests

from src.api.routers import analysis_v2  # noqa: E402

AUTH = {"X-API-Key": "test-standard-key"}
BASE = {"year": 2025, "gp": 5, "session": "Q", "driver1": "VER", "driver2": "NOR"}

_PAYLOAD = {"a": {"driverCode": "VER"}, "b": {"driverCode": "NOR"}, "distance": [0.0, 1.0], "delta": [0.0, 0.1]}

# 1x1 PNG
_PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")


class _Stub:
    def __init__(self, result: Any):
        self._result = result
        self.calls: List[Any] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._result

    @property
    def args(self):
        return self.calls[-1][0]


@pytest.fixture()
def client(monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setattr(analysis_v2, "_track", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(analysis_v2.router)
    app.state.limiter = rl._limiter_instance

    @app.exception_handler(ValueError)
    async def _value_error(request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def png_path(tmp_path) -> str:
    p = tmp_path / "lap_duel.png"
    p.write_bytes(_PNG)
    return str(p)


# ---------------------------------------------------------------------------
# lap-duel-data
# ---------------------------------------------------------------------------
def test_data_returns_json_and_passes_defaults(client, monkeypatch):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, "LapDuelData", lambda: stub)
    resp = client.get("/api/v2/lap-duel-data", params=BASE, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == _PAYLOAD
    # A numeric gp arrives as the string "5" (Union[int, str] keeps str in smart mode), exactly as it does on
    # every neighbouring endpoint; the static client resolves it.
    # Args: (year, gp, session, d1, d2, lap1, lap2, seg1, seg2, year2, gp2, session2, detail)
    assert stub.args == (2025, "5", "Q", "VER", "NOR", None, None, None, None, None, None, None, "standard")


def test_data_passes_every_option_through(client, monkeypatch):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, "LapDuelData", lambda: stub)
    resp = client.get("/api/v2/lap-duel-data", headers=AUTH, params={
        **BASE, "lap1": 12, "lap2": 14, "segment1": "Q3", "segment2": "Q2",
        "year2": 2024, "gp2": "7", "session2": "r", "detail": "full",
    })
    assert resp.status_code == 200
    assert stub.args == (2025, "5", "Q", "VER", "NOR", 12, 14, "Q3", "Q2", 2024, "7", "R", "full")


def test_data_gp2_event_name_is_passed_as_given(client, monkeypatch):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, "LapDuelData", lambda: stub)
    resp = client.get("/api/v2/lap-duel-data", headers=AUTH, params={**BASE, "year2": 2024, "gp2": "Monaco"})
    assert resp.status_code == 200
    assert stub.args[10] == "Monaco"


def test_data_session_is_canonicalised(client, monkeypatch):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, "LapDuelData", lambda: stub)
    assert client.get("/api/v2/lap-duel-data", headers=AUTH, params={**BASE, "session": "q"}).status_code == 200
    assert stub.args[2] == "Q"


def test_data_requires_an_api_key(client, monkeypatch):
    monkeypatch.setattr(analysis_v2, "LapDuelData", lambda: _Stub(_PAYLOAD))
    assert client.get("/api/v2/lap-duel-data", params=BASE).status_code in (401, 403)


def test_data_bad_segment_is_400(client):
    resp = client.get("/api/v2/lap-duel-data", headers=AUTH, params={**BASE, "segment1": "Q4"})
    assert resp.status_code == 400
    assert "segment" in resp.json()["detail"]


def test_data_bad_detail_is_400(client):
    resp = client.get("/api/v2/lap-duel-data", headers=AUTH, params={**BASE, "detail": "everything"})
    assert resp.status_code == 400
    assert "detail" in resp.json()["detail"]


def test_data_identical_laps_are_400(client):
    resp = client.get("/api/v2/lap-duel-data", headers=AUTH, params={**BASE, "driver2": "VER"})
    assert resp.status_code == 400


@pytest.mark.parametrize("extra", [{"lap1": 0}, {"lap2": -3}, {"year2": 2010}, {"year2": 2031}])
def test_data_out_of_range_numbers_are_422(client, monkeypatch, extra):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, "LapDuelData", lambda: stub)
    resp = client.get("/api/v2/lap-duel-data", headers=AUTH, params={**BASE, **extra})
    assert resp.status_code == 422
    assert stub.calls == []


@pytest.mark.parametrize("extra", [{"session2": "../../etc"}, {"session2": "ZZ"}, {"gp2": "../x"}, {"driver2": "V3R"}])
def test_data_unsafe_side_b_addressing_is_400(client, monkeypatch, extra):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, "LapDuelData", lambda: stub)
    resp = client.get("/api/v2/lap-duel-data", headers=AUTH, params={**BASE, "year2": 2024, **extra})
    assert resp.status_code == 400
    assert stub.calls == []


# ---------------------------------------------------------------------------
# lap-duel-plot
# ---------------------------------------------------------------------------
def test_plot_returns_png(client, monkeypatch, png_path):
    stub = _Stub(png_path)
    monkeypatch.setattr(analysis_v2, "LapDuelPlot", lambda: stub)
    resp = client.get("/api/v2/lap-duel-plot", params=BASE, headers=AUTH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == _PNG
    # (... detail, format, hero)
    assert stub.args == (2025, "5", "Q", "VER", "NOR", None, None, None, None, None, None, None, "standard",
                         None, False)


def test_plot_passes_format_hero_and_lap_options(client, monkeypatch, png_path):
    stub = _Stub(png_path)
    monkeypatch.setattr(analysis_v2, "LapDuelPlot", lambda: stub)
    resp = client.get("/api/v2/lap-duel-plot", headers=AUTH, params={
        **BASE, "lap1": 3, "lap2": 4, "format": "story", "hero": "true", "detail": "full",
        "year2": 2026, "gp2": 2, "session2": "R",
    })
    assert resp.status_code == 200
    assert stub.args == (2025, "5", "Q", "VER", "NOR", 3, 4, None, None, 2026, "2", "R", "full", "story", True)


def test_plot_bad_format_is_400(client):
    resp = client.get("/api/v2/lap-duel-plot", headers=AUTH, params={**BASE, "format": "widescreen"})
    assert resp.status_code == 400
    assert "format" in resp.json()["detail"]


def test_plot_bad_segment_is_400(client):
    resp = client.get("/api/v2/lap-duel-plot", headers=AUTH, params={**BASE, "segment1": "Q4"})
    assert resp.status_code == 400
    assert "segment" in resp.json()["detail"]


def test_plot_missing_file_is_404(client, monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("gone")

    monkeypatch.setattr(analysis_v2, "LapDuelPlot", lambda: _boom)
    assert client.get("/api/v2/lap-duel-plot", params=BASE, headers=AUTH).status_code == 404


# ---------------------------------------------------------------------------
# Documentation contract
# ---------------------------------------------------------------------------
def test_openapi_documents_both_routes_without_response_model():
    from src.api.schemas.analysis import LapDuelResponse

    app = FastAPI()
    app.include_router(analysis_v2.router)
    schema: Dict[str, Any] = app.openapi()
    data = schema["paths"]["/api/v2/lap-duel-data"]["get"]
    plot = schema["paths"]["/api/v2/lap-duel-plot"]["get"]
    assert data["operationId"] == "v2_lap_duel_data"
    assert plot["operationId"] == "v2_lap_duel_plot"
    assert data["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/LapDuelResponse")
    assert "image/png" in plot["responses"]["200"]["content"]
    assert "positive means driver2 is behind driver1" in data["description"]
    names = {p["name"] for p in plot["parameters"]}
    assert {"lap1", "lap2", "segment1", "segment2", "year2", "gp2", "session2", "detail", "format", "hero"} <= names
    assert "format" not in {p["name"] for p in data["parameters"]}
    assert LapDuelResponse.model_json_schema()["title"] == "LapDuelResponse"
