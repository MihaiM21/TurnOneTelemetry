"""Route-level coverage for the admin dataset-export console (offline).

Mirrors ``test_admin_ui_routes.py``'s fixtures (a real signed session cookie,
IP allowlist bypassed) since the export page is gated the same way. Tests stub
``admin_ui.dataset_export`` (the worker) directly rather than hitting real
threads/Mongo, and point ``settings.export_dir`` at a tmp_path so the file
listing/download/delete tests exercise real files without touching the repo.
"""

from types import SimpleNamespace

import pytest

from src.api import admin_security
from src.api.routers import admin_ui
from src.core.config import settings
from src.services.dataset_export import exports as exports_mod
from src.services.dataset_export import manifest as manifest_mod


@pytest.fixture
def admin_client(client, monkeypatch):
    monkeypatch.setattr(admin_security, "enforce_ip_allowlist", lambda request: None)
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    client.cookies.set(admin_ui._COOKIE, admin_security.create_session_token())
    return client


@pytest.fixture
def csrf(admin_client):
    return admin_security.csrf_token_for(admin_client.cookies.get(admin_ui._COOKIE))


@pytest.fixture(autouse=True)
def export_dir(tmp_path, monkeypatch):
    path = tmp_path / "exports"
    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "export_dir", str(path))
    return path


@pytest.fixture
def offline_exports(monkeypatch):
    """No exports/jobs on disk — enough for the page to render."""
    monkeypatch.setattr(admin_ui.dataset_exports, "list_exports", lambda: [])
    monkeypatch.setattr(admin_ui.dataset_export, "list_jobs", lambda *a, **kw: [])


def _make_export(export_dir, export_id="export_20260101_abc123"):
    """A minimal real export directory with a manifest and one file."""
    root = export_dir / export_id
    root.mkdir(parents=True, exist_ok=True)
    manifest = manifest_mod.new_manifest(export_id, {"years": [2025]}, ["tables"])
    manifest_mod.save(manifest, root)
    (root / "sessions.parquet").write_bytes(b"hello")
    return root


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
def test_export_page_redirects_when_unauthenticated(client, monkeypatch):
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    response = client.get("/admin/export", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/admin/login"


def test_estimate_401_when_unauthenticated(client, monkeypatch):
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    response = client.post("/admin/export/estimate", json={"csrf_token": "x"})
    assert response.status_code == 401


def test_start_401_when_unauthenticated(client, monkeypatch):
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    response = client.post("/admin/export/start", json={"csrf_token": "x"})
    assert response.status_code == 401


def test_job_status_401_when_unauthenticated(client, monkeypatch):
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    response = client.get("/admin/export/jobs/abc")
    assert response.status_code == 401


def test_job_cancel_401_when_unauthenticated(client, monkeypatch):
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    response = client.post("/admin/export/jobs/abc/cancel", data={"csrf_token": "x"})
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Page render
# --------------------------------------------------------------------------- #
def test_export_page_renders(admin_client, offline_exports):
    response = admin_client.get("/admin/export")
    assert response.status_code == 200
    assert "Export" in response.text
    assert 'class="exp-tier"' in response.text
    assert "tables" in response.text and "corpus" in response.text


def test_export_page_lists_stored_exports(admin_client, export_dir, monkeypatch):
    _make_export(export_dir, "export_20260101_abc123")
    monkeypatch.setattr(admin_ui.dataset_export, "list_jobs", lambda *a, **kw: [])
    response = admin_client.get("/admin/export")
    assert response.status_code == 200
    assert "export_20260101_abc123" in response.text


# --------------------------------------------------------------------------- #
# Estimate / start
# --------------------------------------------------------------------------- #
def test_estimate_round_trips(admin_client, monkeypatch, csrf):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(units=[], tiers=["tables"], warnings=[])

    monkeypatch.setattr(admin_ui.dataset_export, "build_export_plan", fake_build)
    monkeypatch.setattr(
        admin_ui.dataset_export, "estimate_export_plan",
        lambda plan: {"sessions": 3, "units_by_tier": {"tables": 3}, "warnings": []},
    )
    response = admin_client.post(
        "/admin/export/estimate",
        json={"years": [2025], "tiers": ["tables"], "csrf_token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["sessions"] == 3
    assert captured["years"] == [2025]
    assert captured["tiers"] == ["tables"]


def test_estimate_requires_csrf(admin_client):
    response = admin_client.post(
        "/admin/export/estimate", json={"years": [2025], "csrf_token": "bad"},
    )
    assert response.status_code == 403


def test_estimate_bad_scope_is_400(admin_client, monkeypatch, csrf):
    def fake_build(**kwargs):
        raise ValueError("at least one year is required")

    monkeypatch.setattr(admin_ui.dataset_export, "build_export_plan", fake_build)
    response = admin_client.post(
        "/admin/export/estimate", json={"years": [], "csrf_token": csrf},
    )
    assert response.status_code == 400
    assert "error" in response.json()


def test_start_returns_job_dict(admin_client, monkeypatch, csrf):
    job = SimpleNamespace(as_dict=lambda: {"job_id": "job1", "status": "queued"})
    monkeypatch.setattr(admin_ui.dataset_export, "start_export_job", lambda **kw: job)
    response = admin_client.post(
        "/admin/export/start",
        json={"years": [2025], "tiers": ["tables"], "concurrency": 2, "csrf_token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["job_id"] == "job1"


def test_start_conflict_returns_409(admin_client, monkeypatch, csrf):
    def fake_start(**kwargs):
        raise RuntimeError("an export job is already running")

    monkeypatch.setattr(admin_ui.dataset_export, "start_export_job", fake_start)
    response = admin_client.post(
        "/admin/export/start", json={"years": [2025], "csrf_token": csrf},
    )
    assert response.status_code == 409
    assert "error" in response.json()


# --------------------------------------------------------------------------- #
# Job status / cancel
# --------------------------------------------------------------------------- #
def test_job_status_404_for_unknown_job(admin_client, monkeypatch):
    monkeypatch.setattr(admin_ui.dataset_export, "get_job_dict", lambda job_id: None)
    response = admin_client.get("/admin/export/jobs/does-not-exist")
    assert response.status_code == 404


def test_job_status_returns_job(admin_client, monkeypatch):
    monkeypatch.setattr(
        admin_ui.dataset_export, "get_job_dict",
        lambda job_id: {"job_id": job_id, "status": "running"},
    )
    response = admin_client.get("/admin/export/jobs/job1")
    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_cancel_requires_csrf(admin_client):
    response = admin_client.post("/admin/export/jobs/job1/cancel", data={"csrf_token": "bad"})
    assert response.status_code == 403


def test_cancel_flags_the_job(admin_client, monkeypatch, csrf):
    monkeypatch.setattr(admin_ui.dataset_export, "cancel_job", lambda job_id: True)
    response = admin_client.post("/admin/export/jobs/job1/cancel", data={"csrf_token": csrf})
    assert response.status_code == 200
    assert response.json() == {"job_id": "job1", "cancel_requested": True}


# --------------------------------------------------------------------------- #
# Archive
# --------------------------------------------------------------------------- #
def test_archive_requires_csrf(admin_client):
    response = admin_client.post("/admin/export/some_id/archive", data={"csrf_token": "bad"})
    assert response.status_code == 403


def test_archive_starts_job_and_redirects(admin_client, monkeypatch, csrf):
    monkeypatch.setattr(admin_ui.dataset_export, "start_archive_job", lambda export_id: None)
    response = admin_client.post(
        "/admin/export/some_id/archive", data={"csrf_token": csrf}, follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/admin/export"


def test_archive_busy_redirects_with_error(admin_client, monkeypatch, csrf):
    def fake_start(export_id):
        raise RuntimeError("archive for some_id is already being built")

    monkeypatch.setattr(admin_ui.dataset_export, "start_archive_job", fake_start)
    response = admin_client.post(
        "/admin/export/some_id/archive", data={"csrf_token": csrf}, follow_redirects=False,
    )
    assert response.status_code == 302
    assert "error=" in response.headers["location"]


def test_download_archive_404_when_not_ready(admin_client, monkeypatch):
    monkeypatch.setattr(
        admin_ui.dataset_exports, "archive_status", lambda export_id: {"status": "none", "bytes": 0}
    )
    response = admin_client.get("/admin/export/some_id/archive")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #
def test_delete_requires_matching_confirmation(admin_client, csrf):
    response = admin_client.post(
        "/admin/export/export_x/delete",
        data={"confirm": "not-the-id", "csrf_token": csrf},
    )
    assert response.status_code == 400


def test_delete_conflicts_when_in_use(admin_client, monkeypatch, csrf):
    monkeypatch.setattr(admin_ui.dataset_export, "export_in_use", lambda export_id: True)

    def fake_delete(export_id, in_use=None):
        if in_use and in_use(export_id):
            raise exports_mod.ExportBusy(f"export {export_id} has a running job")
        return 0

    monkeypatch.setattr(admin_ui.dataset_exports, "delete_export", fake_delete)
    response = admin_client.post(
        "/admin/export/export_x/delete",
        data={"confirm": "export_x", "csrf_token": csrf},
    )
    assert response.status_code == 409


def test_delete_removes_a_real_export(admin_client, export_dir, csrf):
    _make_export(export_dir, "export_to_delete")
    response = admin_client.post(
        "/admin/export/export_to_delete/delete",
        data={"confirm": "export_to_delete", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert not (export_dir / "export_to_delete").exists()


# --------------------------------------------------------------------------- #
# File download
# --------------------------------------------------------------------------- #
def test_file_download_serves_bytes(admin_client, export_dir):
    _make_export(export_dir, "export_files")
    response = admin_client.get("/admin/export/export_files/files/sessions.parquet")
    assert response.status_code == 200
    assert response.content == b"hello"


def test_file_download_rejects_traversal(admin_client, export_dir):
    _make_export(export_dir, "export_files2")
    response = admin_client.get("/admin/export/export_files2/files/../../etc/passwd")
    assert response.status_code == 404


def test_manifest_route_returns_json(admin_client, export_dir):
    _make_export(export_dir, "export_manifest")
    response = admin_client.get("/admin/export/export_manifest/manifest")
    assert response.status_code == 200
    assert response.json()["export_id"] == "export_manifest"


def test_manifest_route_404_for_missing_export(admin_client):
    response = admin_client.get("/admin/export/does-not-exist/manifest")
    assert response.status_code == 404
