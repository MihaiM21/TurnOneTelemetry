"""Cache-Control policy of ``ETagMiddleware``.

Historical, populated responses are immutable for a day. Anything that can
still change -- an empty answer ("not processed yet"), the current or a later
season, the stored-data ``/availability`` report -- is short-lived and
revalidated, and the ETag / 304 behaviour is untouched either way.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.http_cache import (
    immutable_cache_control,
    is_empty_json_payload,
    references_current_or_future_year,
    volatile_cache_control,
)
from src.api.middleware.etag import ETagMiddleware

CURRENT_YEAR = datetime.now(timezone.utc).year
IMMUTABLE = immutable_cache_control()
VOLATILE = volatile_cache_control()


@pytest.fixture()
def client():
    app = FastAPI()
    app.add_middleware(ETagMiddleware, path_prefixes=("/api/v2/",))

    @app.get("/api/v2/empty-list")
    async def empty_list():
        return []

    @app.get("/api/v2/empty-dict")
    async def empty_dict():
        return {}

    @app.get("/api/v2/null")
    async def null():
        return None

    @app.get("/api/v2/empty-wrapper")
    async def empty_wrapper():
        return {"year": 2023, "standings": []}

    @app.get("/api/v2/full")
    async def full():
        return [{"driver": "NOR", "lap": 1}]

    @app.get("/api/v2/scalars")
    async def scalars():
        return {"ok": True, "count": 0}

    @app.get("/api/v2/by-year")
    async def by_year(year: int = 2023):
        return [{"year": year}]

    @app.get("/api/v2/seasons/{year}/schedule")
    async def season(year: int):
        return [{"round": 1}]

    @app.get("/api/v2/{year}/{gp}/{session}/data")
    async def session_data(year: int, gp: str, session: str):
        return [{"gp": gp}]

    @app.get("/api/v2/sessions/{year}/{gp}/{session}/availability")
    async def availability(year: int, gp: str, session: str):
        return {"stored": [{"feature": "top_speed"}], "missing": []}

    return TestClient(app)


def _cache_control(client, url):
    resp = client.get(url)
    assert resp.status_code == 200
    return resp.headers["cache-control"]


@pytest.mark.parametrize("url", [
    "/api/v2/empty-list", "/api/v2/empty-dict", "/api/v2/null", "/api/v2/empty-wrapper",
])
def test_empty_payload_is_not_immutable(client, url):
    assert _cache_control(client, url) == VOLATILE


def test_populated_payload_is_immutable(client):
    assert _cache_control(client, "/api/v2/full") == IMMUTABLE


def test_object_without_containers_is_data(client):
    assert _cache_control(client, "/api/v2/scalars") == IMMUTABLE


def test_populated_historical_year_query_is_immutable(client):
    assert _cache_control(client, "/api/v2/by-year?year=2023") == IMMUTABLE


def test_current_year_query_is_volatile(client):
    assert _cache_control(client, f"/api/v2/by-year?year={CURRENT_YEAR}") == VOLATILE


def test_future_year_query_is_volatile(client):
    assert _cache_control(client, f"/api/v2/by-year?year={CURRENT_YEAR + 1}") == VOLATILE


def test_seasons_path_year(client):
    assert _cache_control(client, "/api/v2/seasons/2023/schedule") == IMMUTABLE
    assert _cache_control(client, f"/api/v2/seasons/{CURRENT_YEAR}/schedule") == VOLATILE


def test_year_path_segment(client):
    assert _cache_control(client, "/api/v2/2023/monza/R/data") == IMMUTABLE
    assert _cache_control(client, f"/api/v2/{CURRENT_YEAR}/monza/R/data") == VOLATILE


def test_availability_is_volatile_even_for_historical_years(client):
    assert _cache_control(client, "/api/v2/sessions/2023/monza/R/availability") == VOLATILE


def test_etag_and_304_still_work(client):
    first = client.get("/api/v2/full")
    etag = first.headers["etag"]
    assert etag.startswith('W/"')

    second = client.get("/api/v2/full", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.headers["etag"] == etag


def test_etag_and_304_still_work_for_volatile_responses(client):
    first = client.get("/api/v2/empty-list")
    etag = first.headers["etag"]
    assert first.json() == []

    second = client.get("/api/v2/empty-list", headers={"If-None-Match": etag})
    assert second.status_code == 304


class TestHelpers:
    @pytest.mark.parametrize("body", [b"[]", b"{}", b"null", b'{"standings": []}', b'{"a": [], "b": {}, "n": 1}'])
    def test_empty(self, body):
        assert is_empty_json_payload(body)

    @pytest.mark.parametrize("body", [b"[1]", b'{"a": 1}', b'{"a": [], "b": [1]}', b"0", b"not json", b'"x"'])
    def test_not_empty(self, body):
        assert not is_empty_json_payload(body)

    def test_large_body_is_never_scanned(self):
        assert not is_empty_json_payload(b"[" + b" " * 20000 + b"]")

    def test_year_reference_uses_injected_clock(self):
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        assert references_current_or_future_year("/api/v2/seasons/2026/x", None, now)
        assert not references_current_or_future_year("/api/v2/seasons/2025/x", None, now)
        assert references_current_or_future_year("/api/v2/x", {"year": "2027"}, now)
        assert not references_current_or_future_year("/api/v2/x", {"year": "abc"}, now)
        assert not references_current_or_future_year("/api/v2/x", {}, now)
