"""Route-level coverage for the circuit-layout admin surfaces.

``/api/admin/circuits/status`` and ``/derive`` sit behind the same admin API
key gate as the rest of ``/api/admin``, so the consumer-key rejection here
mirrors ``test_storage_cleanup_routes.py``. The derive endpoint is the more
interesting surface: it is a Pydantic body on a route decorated with
``@apply_tiered_limit``, which is exactly the shape the PEP 563 trap documented
in ``CLAUDE.md`` breaks (the body silently degrades into a required query
parameter). ``test_derive_accepts_a_json_body`` exists specifically to catch
that regression.
"""
import pytest

from src.api import admin_security
from src.core.exceptions import SessionNotFoundError
from src.services.circuit_derivation import CircuitDerivationError, LayoutExistsError


def _fake_derive(calls):
    """A ``derive_and_store`` stand-in that records its kwargs and returns a
    realistic result, ``layout`` key included (the router is responsible for
    stripping it)."""

    def _inner(year, gp, session, options, *, overwrite=False, dry_run=False):
        calls["year"] = year
        calls["gp"] = gp
        calls["session"] = session
        calls["options"] = options
        calls["overwrite"] = overwrite
        calls["dry_run"] = dry_run
        return {
            "year": year,
            "circuit_id": "153",
            "circuit_name": "Madring",
            "session": f"{year}/Spanish Grand Prix/{session}",
            "dry_run": dry_run,
            "written": not dry_run,
            "path": None if dry_run else "circuits/2026/153.json",
            "existing_source": None,
            "rotation": 42.0,
            "stats": {
                "lap_length_m": 5245.0,
                "n_points": 750,
                "n_corners": 14,
                "closure_gap_m": 1.2,
                "samples": 3000,
                "speed_available": True,
                "snapped": 10,
                "rotation": 42.0,
                "auto_rotation": True,
                "source_driver": "VER",
                "source_lap_time_s": 78.123,
                "candidates_tried": 1,
                "rejected": [],
            },
            "corners": [
                {"number": 1, "length_m": 120.0, "turn_deg": 90.0,
                 "direction": "left", "min_speed_kmh": 80.0},
            ],
            "layout": {"circuit_id": "153", "year": year},
        }

    return _inner


def _fake_raising(exc):
    def _inner(*args, **kwargs):
        raise exc
    return _inner


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def admin_api_client(app, client, monkeypatch):
    """A client whose requests satisfy ``require_admin_key``.

    Mirrors ``test_storage_cleanup_routes.py::admin_api_client`` -- the test
    env seeds only *consumer* keys, and those must never pass the admin gate,
    so the dependency itself is overridden rather than smuggling a consumer
    key past it.
    """
    from src.api.routers.admin import require_admin_key

    monkeypatch.setattr(admin_security, "enforce_ip_allowlist", lambda request: None)
    app.dependency_overrides[require_admin_key] = lambda: "admin-test-key"
    yield client
    app.dependency_overrides.pop(require_admin_key, None)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #

def test_circuits_endpoints_reject_a_consumer_key(client):
    """Consumer keys must not reach an admin surface."""
    calls = (
        ("get", "/api/admin/circuits/status?year=2026", {}),
        ("post", "/api/admin/circuits/derive", {"json": {"year": 2026, "gp": "Madrid"}}),
    )
    for method, path, kwargs in calls:
        resp = getattr(client, method)(path, headers={"X-API-Key": "test-standard-key"}, **kwargs)
        assert resp.status_code in (401, 403), f"{path} accepted a consumer key"


def test_circuits_endpoints_reject_no_key(client):
    resp = client.get("/api/admin/circuits/status?year=2026")
    assert resp.status_code in (401, 403)

    resp = client.post("/api/admin/circuits/derive", json={"year": 2026, "gp": "Madrid"})
    assert resp.status_code in (401, 403)


# --------------------------------------------------------------------------- #
# GET /status
# --------------------------------------------------------------------------- #

def test_status_returns_the_stubbed_payload(admin_api_client, monkeypatch):
    stub = {
        "year": 2026,
        "rows": [{
            "round": 1, "grand_prix": "Madrid Grand Prix", "circuit": "Madring",
            "location": "Madrid", "country": "Spain", "circuit_key": 153,
            "layout_present": False, "source": None, "file": None, "in_mongo": False,
        }],
        "total": 1, "missing": 1, "unknown_key": 0, "stored_files": 0, "error": None,
    }
    monkeypatch.setattr("src.api.routers.admin_circuits.circuit_layout_status", lambda year: stub)

    resp = admin_api_client.get("/api/admin/circuits/status?year=2026")

    assert resp.status_code == 200
    assert resp.json() == stub


def test_status_rejects_an_out_of_range_year(admin_api_client):
    resp = admin_api_client.get("/api/admin/circuits/status?year=1999")
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# POST /derive
# --------------------------------------------------------------------------- #

def test_derive_accepts_a_json_body(admin_api_client, monkeypatch):
    """Regression guard for the PEP 563 trap: `from __future__ import
    annotations` plus @apply_tiered_limit silently turns a Pydantic body into
    a required *query* parameter, and the endpoint starts 422-ing with
    `loc: ['query', 'body']`."""
    calls = {}
    monkeypatch.setattr("src.api.routers.admin_circuits.derive_and_store", _fake_derive(calls))

    resp = admin_api_client.post("/api/admin/circuits/derive", json={"year": 2026, "gp": "Madrid"})

    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert "layout" not in body
    assert body["stats"]["n_corners"]
    assert calls["year"] == 2026
    assert calls["gp"] == "Madrid"
    assert calls["session"] == "Q"
    assert calls["dry_run"] is True
    assert calls["overwrite"] is False


def test_derive_existing_layout_returns_409(admin_api_client, monkeypatch):
    exc = LayoutExistsError(2026, "153", "x.json", "telemetry")
    monkeypatch.setattr("src.api.routers.admin_circuits.derive_and_store", _fake_raising(exc))

    resp = admin_api_client.post("/api/admin/circuits/derive", json={"year": 2026, "gp": "Madrid"})

    assert resp.status_code == 409


def test_derive_no_usable_lap_returns_422(admin_api_client, monkeypatch):
    exc = CircuitDerivationError("no lap")
    monkeypatch.setattr("src.api.routers.admin_circuits.derive_and_store", _fake_raising(exc))

    resp = admin_api_client.post("/api/admin/circuits/derive", json={"year": 2026, "gp": "Madrid"})

    assert resp.status_code == 422


def test_derive_session_not_found_returns_404(admin_api_client, monkeypatch):
    exc = SessionNotFoundError(year=2026, gp="Nowhere", session="Q", reason="nope")
    monkeypatch.setattr("src.api.routers.admin_circuits.derive_and_store", _fake_raising(exc))

    resp = admin_api_client.post("/api/admin/circuits/derive", json={"year": 2026, "gp": "Nowhere"})

    assert resp.status_code == 404


def test_derive_rejects_an_invalid_session(admin_api_client):
    resp = admin_api_client.post(
        "/api/admin/circuits/derive", json={"year": 2026, "gp": "Madrid", "session": "XX"},
    )
    assert resp.status_code == 400
