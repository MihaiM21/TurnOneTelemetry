"""Route-level coverage for the /admin/circuits derive-from-telemetry console.

Mirrors the fixture style of ``test_admin_ui_routes.py`` (``admin_client``,
``csrf``): those fixtures are defined in that test module rather than a shared
conftest, so they are replicated here rather than imported.
"""

import pytest

from src.api import admin_security
from src.api.routers import admin_ui
from src.workers import plot_inventory


@pytest.fixture
def admin_client(client, monkeypatch):
    """A TestClient carrying a valid admin session cookie."""
    monkeypatch.setattr(admin_security, "enforce_ip_allowlist", lambda request: None)
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    client.cookies.set(admin_ui._COOKIE, admin_security.create_session_token())
    return client


@pytest.fixture
def csrf(admin_client):
    return admin_security.csrf_token_for(admin_client.cookies.get(admin_ui._COOKIE))


@pytest.fixture
def fake_status():
    """A minimal, realistic ``circuit_layout_status`` payload."""
    return {
        "year": 2026,
        "rows": [{
            "round": 16,
            "grand_prix": "Spanish Grand Prix",
            "circuit": "Madring",
            "location": "Madrid",
            "country": "Spain",
            "circuit_key": 153,
            "layout_present": False,
            "source": None,
            "file": None,
            "in_mongo": False,
        }],
        "total": 1,
        "missing": 1,
        "unknown_key": 0,
        "stored_files": 0,
        "error": None,
    }


def _sample_layout_dict():
    """A tiny but schema-valid CircuitLayout: a closed square, 2 corners."""
    return {
        "circuit_id": "153",
        "year": 2026,
        "name": "Madring",
        "country": "Spain",
        "country_code": "ESP",
        "location": "Madrid",
        "rotation": 0.0,
        "round": 16,
        "race_date": "2026-09-13",
        "meeting_name": "Spanish Grand Prix",
        "track_outline": {
            "x": [0, 100, 200, 200, 200, 100, 0, 0],
            "y": [0, 0, 0, 100, 200, 200, 200, 100],
        },
        "corners": [
            {"number": 1, "angle": 90.0, "length": 200.0, "position": {"x": 200, "y": 0}},
            {"number": 2, "angle": 180.0, "length": 600.0, "position": {"x": 200, "y": 200}},
        ],
        "marshal_lights": [],
        "marshal_sectors": [],
        "schema_version": 1,
        "source": "telemetry",
        "source_fetched_at": "2026-09-11T00:00:00+00:00",
        "source_session": "2026/Spanish Grand Prix/Q",
        "source_driver": "VER",
        "source_lap_time_s": 95.123,
    }


def _fake_derive_result(**overrides):
    result = {
        "year": 2026,
        "circuit_id": "153",
        "circuit_name": "Madring",
        "session": "2026/Spanish Grand Prix/Q",
        "dry_run": True,
        "written": False,
        "path": None,
        "existing_source": None,
        "rotation": 0.0,
        "stats": {
            "lap_length_m": 1500.0,
            "n_points": 8,
            "n_corners": 2,
            "closure_gap_m": 1.2,
            "samples": 500,
            "speed_available": True,
            "snapped": 1,
            "rotation": 0.0,
            "auto_rotation": True,
            "source_driver": "VER",
            "source_lap_time_s": 95.123,
            "candidates_tried": 1,
            "rejected": [],
            "corners": [
                {"number": 1, "length_m": 200.0, "turn_deg": 90.0,
                 "direction": "left", "min_speed_kmh": 120.0},
                {"number": 2, "length_m": 600.0, "turn_deg": 180.0,
                 "direction": "right", "min_speed_kmh": 80.0},
            ],
        },
        "corners": [
            {"number": 1, "length_m": 200.0, "turn_deg": 90.0,
             "direction": "left", "min_speed_kmh": 120.0},
            {"number": 2, "length_m": 600.0, "turn_deg": 180.0,
             "direction": "right", "min_speed_kmh": 80.0},
        ],
        "layout": _sample_layout_dict(),
    }
    result.update(overrides)
    return result


# --------------------------------------------------------------------------- #
# Status page
# --------------------------------------------------------------------------- #
def test_circuits_page_renders_rows_and_nav_link(admin_client, monkeypatch, fake_status):
    monkeypatch.setattr(admin_ui, "circuit_layout_status", lambda year: fake_status)
    monkeypatch.setattr(plot_inventory, "available_years", lambda: [2026, 2025])

    response = admin_client.get("/admin/circuits?year=2026")
    assert response.status_code == 200
    body = response.text
    assert "Spanish Grand Prix" in body
    assert "Madring" in body
    # The shared nav must link to this page and mark it active.
    assert 'href="/admin/circuits" class="active"' in body


# --------------------------------------------------------------------------- #
# Preview
# --------------------------------------------------------------------------- #
def test_preview_requires_a_valid_csrf_token(admin_client):
    response = admin_client.post(
        "/admin/circuits/preview",
        data={"year": 2026, "gp": "Spanish Grand Prix", "session": "Q", "csrf_token": "forged"},
    )
    assert response.status_code == 403


def test_preview_renders_svg_and_stats(admin_client, monkeypatch, csrf, fake_status):
    monkeypatch.setattr(admin_ui, "circuit_layout_status", lambda year: fake_status)
    monkeypatch.setattr(plot_inventory, "available_years", lambda: [2026])
    monkeypatch.setattr(admin_ui, "derive_and_store", lambda *a, **kw: _fake_derive_result())

    response = admin_client.post(
        "/admin/circuits/preview",
        data={
            "year": 2026, "gp": "Spanish Grand Prix", "session": "Q",
            "driver": "", "rotation": "", "min_turn_deg": 25, "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    body = response.text
    assert "<svg" in body
    # Corner numbers rendered inside the SVG <text> labels.
    assert ">1</text>" in body
    assert ">2</text>" in body
    # The source driver's TLA and the save action.
    assert "VER" in body
    assert "Save layout" in body


def test_preview_shows_a_derivation_error_instead_of_500(admin_client, monkeypatch, csrf, fake_status):
    monkeypatch.setattr(admin_ui, "circuit_layout_status", lambda year: fake_status)
    monkeypatch.setattr(plot_inventory, "available_years", lambda: [2026])

    def _raise(*args, **kwargs):
        raise admin_ui.CircuitDerivationError("lap does not close")

    monkeypatch.setattr(admin_ui, "derive_and_store", _raise)

    response = admin_client.post(
        "/admin/circuits/preview",
        data={"year": 2026, "gp": "Spanish Grand Prix", "session": "Q", "csrf_token": csrf},
    )
    assert response.status_code == 200
    assert "lap does not close" in response.text


# --------------------------------------------------------------------------- #
# Save
# --------------------------------------------------------------------------- #
def test_save_writes_and_redirects_with_saved_circuit(admin_client, monkeypatch, csrf):
    calls = {}

    def _fake_save(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return _fake_derive_result(dry_run=False, written=True, existing_source=None)

    monkeypatch.setattr(admin_ui, "derive_and_store", _fake_save)

    response = admin_client.post(
        "/admin/circuits/save",
        data={
            "year": 2026, "gp": "Spanish Grand Prix", "session": "Q",
            "driver": "", "rotation": "", "min_turn_deg": 25, "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/admin/circuits?year=2026&saved=153"
    assert calls["kwargs"]["dry_run"] is False


def test_save_existing_layout_redirects_with_error(admin_client, monkeypatch, csrf):
    def _raise(*args, **kwargs):
        raise admin_ui.LayoutExistsError(2026, "153", "x", "telemetry")

    monkeypatch.setattr(admin_ui, "derive_and_store", _raise)

    response = admin_client.post(
        "/admin/circuits/save",
        data={"year": 2026, "gp": "Spanish Grand Prix", "session": "Q", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("/admin/circuits?year=2026&error=")


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def test_download_serves_the_stored_file(admin_client, monkeypatch, tmp_path):
    layout_file = tmp_path / "153_madring.json"
    layout_file.write_text('{"circuit_id": "153"}', encoding="utf-8")
    monkeypatch.setattr(admin_ui, "find_circuit_file", lambda year, circuit_id: layout_file)

    response = admin_client.get("/admin/circuits/2026/153/download")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


def test_download_404_when_no_file_exists(admin_client, monkeypatch):
    monkeypatch.setattr(admin_ui, "find_circuit_file", lambda year, circuit_id: None)
    response = admin_client.get("/admin/circuits/2026/153/download")
    assert response.status_code == 404


def test_download_404_for_a_non_numeric_circuit_id(admin_client, monkeypatch):
    called = []
    monkeypatch.setattr(
        admin_ui, "find_circuit_file",
        lambda year, circuit_id: called.append(circuit_id) or None,
    )
    response = admin_client.get("/admin/circuits/2026/..%2Fx/download")
    assert response.status_code == 404
    # The regex guard must refuse before ever touching the filesystem lookup.
    assert called == []
