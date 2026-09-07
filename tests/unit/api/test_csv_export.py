"""CSV rendering of JSON analysis payloads (``?format=csv``).

Opt-in only: without the parameter a response must be byte-identical to before,
because every existing client depends on the JSON shape.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.csv_export import CSVExportMiddleware, _render_csv, _to_rows

FLAT = [
    {"Driver": "NOR", "Team": "McLaren", "LapTime": "1:11.383"},
    {"Driver": "VER", "Team": "Red Bull Racing", "LapTime": "1:11.403"},
]
NESTED = [
    {"driver": "SAI", "team": "Ferrari",
     "laps": [{"lap": 2, "gap_s": 0.809}, {"lap": 3, "gap_s": 1.477}]},
]
NOT_TABULAR = {"session": {"year": 2024}, "note": "aggregate"}


@pytest.fixture()
def client():
    app = FastAPI()
    app.add_middleware(CSVExportMiddleware, path_prefixes=("/api/v2/",))

    @app.get("/api/v2/flat")
    async def flat():
        return FLAT

    @app.get("/api/v2/nested")
    async def nested():
        return NESTED

    @app.get("/api/v2/blob")
    async def blob():
        return NOT_TABULAR

    return TestClient(app)


def test_json_is_untouched_without_the_parameter(client):
    resp = client.get("/api/v2/flat")
    assert resp.status_code == 200
    assert "json" in resp.headers["content-type"]
    assert json.loads(resp.content) == FLAT


def test_flat_payload_renders_as_csv(client):
    resp = client.get("/api/v2/flat?format=csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    lines = resp.text.strip().splitlines()
    assert lines[0] == "Driver,Team,LapTime"
    assert lines[1].startswith("NOR,McLaren,")


def test_nested_series_is_exploded_to_long_format(client):
    """One row per series element, parent columns repeated."""
    resp = client.get("/api/v2/nested?format=csv")
    assert resp.status_code == 200
    lines = resp.text.strip().splitlines()
    assert lines[0] == "driver,team,lap,gap_s"
    assert len(lines) == 3  # header + 2 laps


def test_non_tabular_payload_is_refused_not_mangled(client):
    """Better a clear 400 than a misleading half-flattened file."""
    resp = client.get("/api/v2/blob?format=csv")
    assert resp.status_code == 400
    assert "not tabular" in resp.json()["detail"]


def test_unrelated_paths_are_ignored():
    app = FastAPI()
    app.add_middleware(CSVExportMiddleware, path_prefixes=("/api/v2/",))

    @app.get("/other")
    async def other():
        return FLAT

    resp = TestClient(app).get("/other?format=csv")
    assert "json" in resp.headers["content-type"]


@pytest.mark.parametrize("payload", [
    [{"a": 1, "b": {"c": 2}}],          # nested dict, no series
    {"x": [{"a": 1}], "y": [{"b": 2}]},  # ambiguous: two candidate lists
    "plain string",
])
def test_to_rows_rejects_untabular_shapes(payload):
    assert _to_rows(payload) is None


def test_render_csv_unions_columns_across_rows():
    out = _render_csv([{"a": 1}, {"a": 2, "b": 3}])
    assert out.splitlines()[0] == "a,b"


def test_etag_covers_the_csv_bytes_not_the_json():
    """Middleware order matters: ETag must wrap CSV, not the other way round.

    If the ETag were computed over the JSON, a cache could serve the JSON
    validator against a CSV body (or vice versa). Distinct ETags per
    representation is what keeps conditional GET honest.
    """
    from src.api.middleware.etag import ETagMiddleware

    app = FastAPI()
    # Same order as create_app(): CSV added first, so ETag ends up outermost.
    app.add_middleware(CSVExportMiddleware, path_prefixes=("/api/v2/",))
    app.add_middleware(ETagMiddleware, path_prefixes=("/api/v2/",))

    @app.get("/api/v2/flat")
    async def flat():
        return FLAT

    client = TestClient(app)
    as_json = client.get("/api/v2/flat")
    as_csv = client.get("/api/v2/flat?format=csv")

    assert as_json.headers["etag"] != as_csv.headers["etag"]

    revalidated = client.get(
        "/api/v2/flat?format=csv",
        headers={"If-None-Match": as_csv.headers["etag"]},
    )
    assert revalidated.status_code == 304
