"""Verify the app's custom-exception handlers produce the expected HTTP responses."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.exceptions import (
    DataNotAvailableError,
    SessionNotFoundError,
    UpstreamUnavailableError,
)


@pytest.fixture
def configured_app(app):
    """app fixture already invokes create_app(), which registers our handlers.
    We mount a few test-only routes to trigger each exception type."""
    @app.get("/_test/data-not-available")
    def _dna():
        raise DataNotAvailableError(year=2026, gp=5, session="R", source="fastf1", reason="lag")

    @app.get("/_test/upstream-unavailable")
    def _uu():
        raise UpstreamUnavailableError(source="livetiming", reason="503")

    @app.get("/_test/session-not-found")
    def _snf():
        raise SessionNotFoundError(year=2030, gp=99, session="R", reason="No such session")

    return app


def test_data_not_available_returns_503(configured_app):
    with TestClient(configured_app) as c:
        resp = c.get("/_test/data-not-available")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "data_not_available"
    assert body["year"] == 2026
    assert body["gp"] == 5
    assert body["session"] == "R"
    assert "fastf1" in body["sources_tried"]
    assert resp.headers.get("Retry-After") == "300"


def test_upstream_unavailable_returns_503(configured_app):
    with TestClient(configured_app) as c:
        resp = c.get("/_test/upstream-unavailable")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "upstream_unavailable"
    assert body["source"] == "livetiming"


def test_session_not_found_returns_404(configured_app):
    with TestClient(configured_app) as c:
        resp = c.get("/_test/session-not-found")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"] == "session_not_found"
    assert body["year"] == 2030
