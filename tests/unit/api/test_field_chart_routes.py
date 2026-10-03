"""Offline route tests for the field-wide chart endpoints and the ``format`` retrofit.

New endpoints: energy clipping, corner speed profile, efficiency scatter, field dominance, sector gap
(each ``-plot`` and ``-data``). Retrofit: the optional ``format`` query parameter on eight existing plot
endpoints, which must reach the service as ``fmt=`` when given and must leave the service call untouched
(no ``fmt`` at all) when omitted.

Same harness as ``test_lap_duel_routes.py``: the limiter exists before the routers are imported, the routers
are mounted on a throwaway app that mirrors the global ``ValueError -> 400`` handler, and the service
callables are stubbed where the router imports them. Bad-format tests use the real ``canvas.get_format``.
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

from src.api.routers import analysis_v2, seasonal_v2  # noqa: E402

AUTH = {"X-API-Key": "test-standard-key"}
BASE = {"year": 2026, "gp": 5, "session": "Q"}
_PAYLOAD = {"ok": True}

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

    @property
    def kwargs(self):
        return self.calls[-1][1]


@pytest.fixture()
def client(monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setattr(analysis_v2, "_track", lambda *a, **k: None)
    monkeypatch.setattr(seasonal_v2, "_track", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(analysis_v2.router)
    app.include_router(seasonal_v2.router)
    app.state.limiter = rl._limiter_instance

    @app.exception_handler(ValueError)
    async def _value_error(request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def png_path(tmp_path) -> str:
    p = tmp_path / "chart.png"
    p.write_bytes(_PNG)
    return str(p)


# ---------------------------------------------------------------------------
# New endpoints: (route stem, service-class name pair, extra required query params)
# ---------------------------------------------------------------------------
FEATURES = [
    ("energy-clipping", "EnergyClippingPlot", "EnergyClippingData"),
    ("corner-speed-profile", "CornerSpeedProfilePlot", "CornerSpeedProfileData"),
    ("efficiency-scatter", "EfficiencyScatterPlot", "EfficiencyScatterData"),
    ("field-dominance", "FieldDominancePlot", "FieldDominanceData"),
    ("sector-gap", "SectorGapPlot", "SectorGapData"),
]
IDS = [f[0] for f in FEATURES]


@pytest.mark.parametrize("stem,plot_cls,data_cls", FEATURES, ids=IDS)
def test_data_returns_json_and_passes_the_session(client, monkeypatch, stem, plot_cls, data_cls):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, data_cls, lambda: stub)
    resp = client.get(f"/api/v2/{stem}-data", params=BASE, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == _PAYLOAD
    # A numeric gp arrives as the string "5", exactly as on every neighbouring endpoint.
    assert stub.args[:3] == (2026, "5", "Q")


@pytest.mark.parametrize("stem,plot_cls,data_cls", FEATURES, ids=IDS)
def test_data_requires_an_api_key(client, monkeypatch, stem, plot_cls, data_cls):
    monkeypatch.setattr(analysis_v2, data_cls, lambda: _Stub(_PAYLOAD))
    assert client.get(f"/api/v2/{stem}-data", params=BASE).status_code in (401, 403)


@pytest.mark.parametrize("stem,plot_cls,data_cls", FEATURES, ids=IDS)
def test_plot_requires_an_api_key(client, monkeypatch, png_path, stem, plot_cls, data_cls):
    monkeypatch.setattr(analysis_v2, plot_cls, lambda: _Stub(png_path))
    assert client.get(f"/api/v2/{stem}-plot", params=BASE).status_code in (401, 403)


@pytest.mark.parametrize("stem,plot_cls,data_cls", FEATURES, ids=IDS)
def test_plot_returns_png_and_defaults_format_to_none(client, monkeypatch, png_path, stem, plot_cls, data_cls):
    stub = _Stub(png_path)
    monkeypatch.setattr(analysis_v2, plot_cls, lambda: stub)
    resp = client.get(f"/api/v2/{stem}-plot", params=BASE, headers=AUTH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == _PNG
    assert stub.args[:3] == (2026, "5", "Q")
    assert stub.args[-1] is None                       # format omitted -> the classic image


@pytest.mark.parametrize("stem,plot_cls,data_cls", FEATURES, ids=IDS)
def test_plot_passes_format_through(client, monkeypatch, png_path, stem, plot_cls, data_cls):
    stub = _Stub(png_path)
    monkeypatch.setattr(analysis_v2, plot_cls, lambda: stub)
    resp = client.get(f"/api/v2/{stem}-plot", params={**BASE, "format": "story"}, headers=AUTH)
    assert resp.status_code == 200
    assert stub.args[-1] == "story"


@pytest.mark.parametrize("stem,plot_cls,data_cls", FEATURES, ids=IDS)
def test_plot_bad_format_is_400(client, stem, plot_cls, data_cls):
    """The real service class: the format is validated before anything is fetched."""
    resp = client.get(f"/api/v2/{stem}-plot", params={**BASE, "format": "widescreen"}, headers=AUTH)
    assert resp.status_code == 400
    assert "format" in resp.json()["detail"]


@pytest.mark.parametrize("stem,plot_cls,data_cls", FEATURES, ids=IDS)
def test_plot_missing_file_is_404(client, monkeypatch, stem, plot_cls, data_cls):
    def _boom(*a, **k):
        raise FileNotFoundError("gone")

    monkeypatch.setattr(analysis_v2, plot_cls, lambda: _boom)
    assert client.get(f"/api/v2/{stem}-plot", params=BASE, headers=AUTH).status_code == 404


@pytest.mark.parametrize("stem,plot_cls,data_cls", FEATURES, ids=IDS)
def test_session_is_canonicalised(client, monkeypatch, stem, plot_cls, data_cls):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, data_cls, lambda: stub)
    assert client.get(f"/api/v2/{stem}-data", headers=AUTH, params={**BASE, "session": "q"}).status_code == 200
    assert stub.args[2] == "Q"


@pytest.mark.parametrize("stem,plot_cls,data_cls", FEATURES, ids=IDS)
def test_unsafe_session_is_400(client, monkeypatch, stem, plot_cls, data_cls):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, data_cls, lambda: stub)
    resp = client.get(f"/api/v2/{stem}-data", headers=AUTH, params={**BASE, "session": "../../etc"})
    assert resp.status_code == 400
    assert stub.calls == []


# ---------------------------------------------------------------------------
# Energy clipping: 2026+ gate, driver
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["plot", "data"])
def test_energy_clipping_before_2026_is_404_and_never_reaches_the_service(client, monkeypatch, png_path, kind):
    stub = _Stub(png_path if kind == "plot" else _PAYLOAD)
    monkeypatch.setattr(analysis_v2, "EnergyClippingPlot", lambda: stub)
    monkeypatch.setattr(analysis_v2, "EnergyClippingData", lambda: stub)
    resp = client.get(f"/api/v2/energy-clipping-{kind}", headers=AUTH, params={**BASE, "year": 2025})
    assert resp.status_code == 404
    assert "2026" in resp.json()["detail"]
    assert stub.calls == []


def test_energy_clipping_2026_is_allowed(client, monkeypatch):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, "EnergyClippingData", lambda: stub)
    assert client.get("/api/v2/energy-clipping-data", headers=AUTH, params=BASE).status_code == 200


def test_energy_clipping_plot_passes_driver_uppercased_and_format(client, monkeypatch, png_path):
    stub = _Stub(png_path)
    monkeypatch.setattr(analysis_v2, "EnergyClippingPlot", lambda: stub)
    resp = client.get("/api/v2/energy-clipping-plot", headers=AUTH,
                      params={**BASE, "driver": "ver", "format": "square"})
    assert resp.status_code == 200
    # (year, gp, session, driver, format)
    assert stub.args == (2026, "5", "Q", "VER", "square")


def test_energy_clipping_plot_without_driver_passes_none(client, monkeypatch, png_path):
    stub = _Stub(png_path)
    monkeypatch.setattr(analysis_v2, "EnergyClippingPlot", lambda: stub)
    assert client.get("/api/v2/energy-clipping-plot", headers=AUTH, params=BASE).status_code == 200
    assert stub.args == (2026, "5", "Q", None, None)


def test_energy_clipping_bad_driver_is_400(client, monkeypatch, png_path):
    stub = _Stub(png_path)
    monkeypatch.setattr(analysis_v2, "EnergyClippingPlot", lambda: stub)
    resp = client.get("/api/v2/energy-clipping-plot", headers=AUTH, params={**BASE, "driver": "V3R"})
    assert resp.status_code == 400
    assert stub.calls == []


# ---------------------------------------------------------------------------
# Field dominance: mode / top_n
# ---------------------------------------------------------------------------
def test_field_dominance_data_defaults(client, monkeypatch):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, "FieldDominanceData", lambda: stub)
    assert client.get("/api/v2/field-dominance-data", headers=AUTH, params=BASE).status_code == 200
    assert stub.args == (2026, "5", "Q", "team", None)


def test_field_dominance_data_passes_mode_and_top_n(client, monkeypatch):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, "FieldDominanceData", lambda: stub)
    resp = client.get("/api/v2/field-dominance-data", headers=AUTH, params={**BASE, "mode": "driver", "top_n": 6})
    assert resp.status_code == 200
    assert stub.args == (2026, "5", "Q", "driver", 6)


def test_field_dominance_plot_passes_mode_top_n_and_format(client, monkeypatch, png_path):
    stub = _Stub(png_path)
    monkeypatch.setattr(analysis_v2, "FieldDominancePlot", lambda: stub)
    resp = client.get("/api/v2/field-dominance-plot", headers=AUTH,
                      params={**BASE, "mode": "driver", "top_n": 4, "format": "portrait"})
    assert resp.status_code == 200
    assert stub.args == (2026, "5", "Q", "driver", 4, "portrait")


@pytest.mark.parametrize("kind", ["plot", "data"])
def test_field_dominance_bad_mode_is_400(client, kind):
    resp = client.get(f"/api/v2/field-dominance-{kind}", headers=AUTH, params={**BASE, "mode": "constructor"})
    assert resp.status_code == 400
    assert "mode" in resp.json()["detail"]


@pytest.mark.parametrize("kind", ["plot", "data"])
@pytest.mark.parametrize("top_n", [1, 11, 0, -3])
def test_field_dominance_top_n_out_of_range_is_422(client, monkeypatch, kind, top_n):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, "FieldDominanceData", lambda: stub)
    monkeypatch.setattr(analysis_v2, "FieldDominancePlot", lambda: stub)
    resp = client.get(f"/api/v2/field-dominance-{kind}", headers=AUTH, params={**BASE, "top_n": top_n})
    assert resp.status_code == 422
    assert stub.calls == []


@pytest.mark.parametrize("top_n", [2, 10])
def test_field_dominance_top_n_edges_are_accepted(client, monkeypatch, top_n):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, "FieldDominanceData", lambda: stub)
    assert client.get("/api/v2/field-dominance-data", headers=AUTH,
                      params={**BASE, "top_n": top_n}).status_code == 200
    assert stub.args[-1] == top_n


# ---------------------------------------------------------------------------
# Sector gap: Q / SQ only
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["plot", "data"])
@pytest.mark.parametrize("session", ["R", "FP1", "S"])
def test_sector_gap_other_sessions_are_404_and_never_reach_the_service(client, monkeypatch, png_path, kind, session):
    stub = _Stub(png_path if kind == "plot" else _PAYLOAD)
    monkeypatch.setattr(analysis_v2, "SectorGapPlot", lambda: stub)
    monkeypatch.setattr(analysis_v2, "SectorGapData", lambda: stub)
    resp = client.get(f"/api/v2/sector-gap-{kind}", headers=AUTH, params={**BASE, "session": session})
    assert resp.status_code == 404
    assert stub.calls == []


@pytest.mark.parametrize("session", ["Q", "SQ"])
def test_sector_gap_accepts_quali_sessions(client, monkeypatch, session):
    stub = _Stub(_PAYLOAD)
    monkeypatch.setattr(analysis_v2, "SectorGapData", lambda: stub)
    assert client.get("/api/v2/sector-gap-data", headers=AUTH, params={**BASE, "session": session}).status_code == 200
    assert stub.args[2] == session


# ---------------------------------------------------------------------------
# Documentation contract
# ---------------------------------------------------------------------------
def test_openapi_documents_the_new_routes_without_response_model():
    app = FastAPI()
    app.include_router(analysis_v2.router)
    schema: Dict[str, Any] = app.openapi()
    expected_model = {
        "energy-clipping": "EnergyClippingResponse",
        "corner-speed-profile": "CornerSpeedProfileResponse",
        "efficiency-scatter": "EfficiencyScatterResponse",
        "field-dominance": "FieldDominanceResponse",
        "sector-gap": "SectorGapResponse",
    }
    for stem, model in expected_model.items():
        data = schema["paths"][f"/api/v2/{stem}-data"]["get"]
        plot = schema["paths"][f"/api/v2/{stem}-plot"]["get"]
        snake = stem.replace("-", "_")
        assert data["operationId"] == f"v2_{snake}_data"
        assert plot["operationId"] == f"v2_{snake}_plot"
        assert data["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(f"/{model}")
        assert "image/png" in plot["responses"]["200"]["content"]
        assert "format" in {p["name"] for p in plot["parameters"]}
        assert "format" not in {p["name"] for p in data["parameters"]}
    clip = schema["paths"]["/api/v2/energy-clipping-data"]["get"]
    assert "2026+ only" in clip["description"] and "estimated" in clip["description"]
    fd_params = {p["name"] for p in schema["paths"]["/api/v2/field-dominance-plot"]["get"]["parameters"]}
    assert {"mode", "top_n"} <= fd_params
    assert "driver" in {p["name"] for p in schema["paths"]["/api/v2/energy-clipping-plot"]["get"]["parameters"]}


# ---------------------------------------------------------------------------
# `format` retrofit on existing plot endpoints
# ---------------------------------------------------------------------------
# (path, extra params, router module, attribute, is_class) -- is_class: the router calls ``Attr()`` first.
RETROFIT = [
    ("/api/v2/qualifying-results-plot", {}, analysis_v2, "QualiResultsPlot", False),
    ("/api/v2/theoretical-best-plot", {}, analysis_v2, "TheoreticalBestPlot", True),
    ("/api/v2/track-comparison-plot", {"d1": "VER", "d2": "NOR"}, analysis_v2, "TrackComparisonPlot", False),
    ("/api/v2/top-speed-telemetry-plot", {}, analysis_v2, "TopSpeedPlot_Telemetry", False),
    ("/api/v2/top-speed-st-plot", {}, analysis_v2, "TopSpeedPlot_SpeedTrap", False),
    ("/api/v2/race-story-plot", {"session": "R"}, analysis_v2, "RaceStoryPlot", True),
    ("/api/v2/position-changes-plot", {"session": "R"}, analysis_v2, "PositionChangesPlot", True),
    ("/api/v2/pit-strategy-plot", {"session": "R"}, analysis_v2, "PitStrategyPlot", True),
    ("/api/v2/seasons/2025/teammate-battle-plot", None, seasonal_v2, "TeammateBattlePlot", True),
]
RETROFIT_IDS = [r[0].rsplit("/", 1)[-1] for r in RETROFIT]


def _install(monkeypatch, module, attr: str, is_class: bool, result: Any) -> _Stub:
    stub = _Stub(result)
    monkeypatch.setattr(module, attr, (lambda: stub) if is_class else stub)
    return stub


def _params(extra):
    return {} if extra is None else {**BASE, "year": 2025, **extra}


@pytest.mark.parametrize("path,extra,module,attr,is_class", RETROFIT, ids=RETROFIT_IDS)
def test_retrofit_omitted_format_leaves_the_service_call_untouched(
        client, monkeypatch, png_path, path, extra, module, attr, is_class):
    stub = _install(monkeypatch, module, attr, is_class, png_path)
    resp = client.get(path, params=_params(extra), headers=AUTH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert "fmt" not in stub.kwargs
    assert "story" not in stub.args


@pytest.mark.parametrize("path,extra,module,attr,is_class", RETROFIT, ids=RETROFIT_IDS)
def test_retrofit_format_reaches_the_service_as_fmt(
        client, monkeypatch, png_path, path, extra, module, attr, is_class):
    stub = _install(monkeypatch, module, attr, is_class, png_path)
    resp = client.get(path, params={**_params(extra), "format": "story"}, headers=AUTH)
    assert resp.status_code == 200
    assert stub.kwargs == {"fmt": "story"}


@pytest.mark.parametrize("path,extra,module,attr,is_class", RETROFIT, ids=RETROFIT_IDS)
def test_retrofit_bad_format_is_400_before_the_service_runs(
        client, monkeypatch, png_path, path, extra, module, attr, is_class):
    stub = _install(monkeypatch, module, attr, is_class, png_path)
    resp = client.get(path, params={**_params(extra), "format": "widescreen"}, headers=AUTH)
    assert resp.status_code == 400
    assert "format" in resp.json()["detail"]
    assert stub.calls == []


@pytest.mark.parametrize("path,extra,module,attr,is_class", RETROFIT, ids=RETROFIT_IDS)
def test_retrofit_positional_arguments_are_unchanged(
        client, monkeypatch, png_path, path, extra, module, attr, is_class):
    """The leading positional arguments are exactly what they were before ``format`` existed."""
    stub = _install(monkeypatch, module, attr, is_class, png_path)
    client.get(path, params={**_params(extra), "format": "square"}, headers=AUTH)
    if extra is None:
        assert stub.args == (2025,)
    else:
        session = extra.get("session", "Q")
        tail = (extra["d1"], extra["d2"]) if "d1" in extra else ()
        assert stub.args == (2025, "5", session, *tail)


def test_retrofit_openapi_documents_format_on_every_endpoint():
    app = FastAPI()
    app.include_router(analysis_v2.router)
    app.include_router(seasonal_v2.router)
    schema = app.openapi()
    for path, *_ in RETROFIT:
        path = path.replace("/seasons/2025/", "/seasons/{year}/")
        params = {p["name"]: p for p in schema["paths"][path]["get"]["parameters"]}
        assert "format" in params, path
        assert params["format"]["required"] is False
        assert "story" in params["format"]["description"]
