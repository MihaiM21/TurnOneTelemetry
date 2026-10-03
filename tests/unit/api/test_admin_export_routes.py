"""Route-level coverage for the dataset-export admin surface (`/api/admin/export/*`).

Mirrors ``test_storage_cleanup_routes.py``'s shape: an admin-key gated JSON API,
weighted toward the refusals (no key, consumer key, low disk, busy exports,
bad confirmation) and a small real export directory for the read paths.
"""
import pytest

from src.api import admin_security
from src.core.config import settings
from src.services.dataset_export import exports
from src.services.dataset_export import manifest as manifest_mod
from src.services.dataset_export.manifest import TierEntry
from src.services.dataset_export.writer import FileStat
from src.workers import dataset_export


@pytest.fixture
def export_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "export_dir", str(tmp_path / "exports"))
    return tmp_path / "exports"


@pytest.fixture
def admin_api_client(app, client, monkeypatch, export_root):
    """A client whose requests satisfy ``require_admin_key``.

    The test env seeds only *consumer* keys, and those must never pass the
    admin gate -- see ``test_export_endpoints_reject_a_consumer_key`` below.
    So the dependency itself is overridden for tests that need an authorised
    caller, rather than smuggling a consumer key past the gate.
    """
    from src.api.routers.admin import require_admin_key

    monkeypatch.setattr(admin_security, "enforce_ip_allowlist", lambda request: None)
    app.dependency_overrides[require_admin_key] = lambda: "admin-test-key"
    yield client
    app.dependency_overrides.pop(require_admin_key, None)


def _make_export(export_id: str, root, *, rel_path="tables/sessions/data.parquet", bytes_=42):
    """Write a real tiny export directory with one recorded file."""
    export_dir = root / export_id
    export_dir.mkdir(parents=True, exist_ok=True)
    file_path = export_dir / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"x" * bytes_)

    manifest = manifest_mod.new_manifest(export_id, {"years": [2025]}, ["tables"])
    stat = FileStat(path=rel_path, rows=3, bytes=bytes_, sha256="deadbeef")
    entry = TierEntry(status="done", files=[stat], rows=3, bytes=bytes_)
    manifest.mark("2025_01_R", "tables", entry, year=2025, round=1, gp_name="Test GP", session="R")
    manifest_mod.save(manifest, export_dir)
    return export_dir


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #

def test_export_endpoints_reject_a_consumer_key(client, export_root):
    resp = client.get("/api/admin/export/exports", headers={"X-API-Key": "test-standard-key"})
    assert resp.status_code in (401, 403)


def test_export_endpoints_reject_no_key(client, export_root):
    resp = client.get("/api/admin/export/exports")
    assert resp.status_code in (401, 403)


# --------------------------------------------------------------------------- #
# Estimate
# --------------------------------------------------------------------------- #

def test_estimate_returns_the_plan_dict(admin_api_client, monkeypatch, export_root):
    sentinel_plan = object()
    estimate_dict = {
        "export_id": "export_x", "tiers": ["tables"], "sessions": 3, "already_done": 0,
        "units_by_tier": {"tables": 3}, "cached_sessions": 1, "cold_sessions": 2,
        "est_bytes": 123, "est_seconds": 45, "free_bytes": 999, "warnings": [], "scope": {},
    }
    monkeypatch.setattr(dataset_export, "build_export_plan", lambda **kw: sentinel_plan)
    monkeypatch.setattr(
        dataset_export, "estimate_export_plan",
        lambda plan: estimate_dict if plan is sentinel_plan else {},
    )

    resp = admin_api_client.post("/api/admin/export/estimate", json={"years": [2025]})
    assert resp.status_code == 200
    assert resp.json() == estimate_dict


def test_estimate_rejects_a_bad_scope_with_400(admin_api_client, monkeypatch, export_root):
    def _boom(**kw):
        raise ValueError("at least one year is required")

    monkeypatch.setattr(dataset_export, "build_export_plan", _boom)
    resp = admin_api_client.post("/api/admin/export/estimate", json={"years": [2025]})
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Start job
# --------------------------------------------------------------------------- #

class _FakeJob:
    def as_dict(self):
        return {"job_id": "abc123def456", "kind": "dataset_export", "export_id": "export_x",
                "status": "queued", "total": 0, "scope": {}, "tiers": ["tables"]}


def test_start_job_returns_the_job_dict(admin_api_client, monkeypatch, export_root):
    monkeypatch.setattr(dataset_export, "start_export_job", lambda **kw: _FakeJob())
    resp = admin_api_client.post("/api/admin/export/jobs", json={"years": [2025]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "abc123def456"
    assert body["status"] == "queued"


def test_start_job_conflict_when_already_running(admin_api_client, monkeypatch, export_root):
    def _boom(**kw):
        raise RuntimeError("an export job is already running")

    monkeypatch.setattr(dataset_export, "start_export_job", _boom)
    resp = admin_api_client.post("/api/admin/export/jobs", json={"years": [2025]})
    assert resp.status_code == 409


def test_start_job_conflict_on_low_disk(admin_api_client, monkeypatch, export_root):
    class _Usage:
        free = 1  # essentially zero

    monkeypatch.setattr(
        "src.api.routers.admin_export.shutil.disk_usage", lambda path: _Usage()
    )
    resp = admin_api_client.post("/api/admin/export/jobs", json={"years": [2025]})
    assert resp.status_code == 409
    assert "free" in resp.json()["detail"].lower()


def test_start_job_bad_scope_is_400(admin_api_client, monkeypatch, export_root):
    def _boom(**kw):
        raise ValueError("bad scope")

    monkeypatch.setattr(dataset_export, "start_export_job", _boom)
    resp = admin_api_client.post("/api/admin/export/jobs", json={"years": [2025]})
    assert resp.status_code == 400


def test_start_job_body_is_a_body_not_a_query_param(admin_api_client, monkeypatch, export_root):
    """Regression guard for the PEP 563 trap documented in CLAUDE.md."""
    monkeypatch.setattr(dataset_export, "start_export_job", lambda **kw: _FakeJob())
    resp = admin_api_client.post("/api/admin/export/jobs", json={"years": [2025]})
    assert resp.status_code != 422, "body degraded into a query parameter"


# --------------------------------------------------------------------------- #
# Jobs list / status / cancel
# --------------------------------------------------------------------------- #

def test_jobs_list(admin_api_client, monkeypatch, export_root):
    monkeypatch.setattr(dataset_export, "list_jobs", lambda limit, status: [{"job_id": "a" * 12}])
    resp = admin_api_client.get("/api/admin/export/jobs")
    assert resp.status_code == 200
    assert resp.json() == {"count": 1, "jobs": [{"job_id": "a" * 12}]}


def test_job_status_found(admin_api_client, monkeypatch, export_root):
    job_id = "a" * 12
    monkeypatch.setattr(dataset_export, "get_job_dict", lambda jid: {"job_id": jid, "status": "running"})
    resp = admin_api_client.get(f"/api/admin/export/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


def test_job_status_not_found(admin_api_client, monkeypatch, export_root):
    job_id = "a" * 12
    monkeypatch.setattr(dataset_export, "get_job_dict", lambda jid: None)
    resp = admin_api_client.get(f"/api/admin/export/jobs/{job_id}")
    assert resp.status_code == 404


def test_job_id_must_match_the_id_shape(admin_api_client, export_root):
    resp = admin_api_client.get("/api/admin/export/jobs/not-a-valid-id")
    assert resp.status_code == 404


def test_job_cancel_ok(admin_api_client, monkeypatch, export_root):
    job_id = "a" * 12
    monkeypatch.setattr(dataset_export, "cancel_job", lambda jid: True)
    resp = admin_api_client.post(f"/api/admin/export/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json() == {"job_id": job_id, "cancel_requested": True}


def test_job_cancel_conflict_when_not_cancellable(admin_api_client, monkeypatch, export_root):
    job_id = "a" * 12
    monkeypatch.setattr(dataset_export, "cancel_job", lambda jid: False)
    resp = admin_api_client.post(f"/api/admin/export/jobs/{job_id}/cancel")
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# Exports: list / manifest / files
# --------------------------------------------------------------------------- #

def test_exports_list_against_a_real_export_dir(admin_api_client, export_root):
    _make_export("export_a", export_root)
    resp = admin_api_client.get("/api/admin/export/exports")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["exports"][0]["export_id"] == "export_a"
    assert body["exports"][0]["sessions_total"] == 1


def test_manifest_returns_the_raw_dict(admin_api_client, export_root):
    _make_export("export_b", export_root)
    resp = admin_api_client.get("/api/admin/export/exports/export_b/manifest")
    assert resp.status_code == 200
    assert resp.json()["export_id"] == "export_b"


def test_manifest_404_for_unknown_export(admin_api_client, export_root):
    resp = admin_api_client.get("/api/admin/export/exports/does_not_exist/manifest")
    assert resp.status_code == 404


def test_files_list(admin_api_client, export_root):
    _make_export("export_c", export_root)
    resp = admin_api_client.get("/api/admin/export/exports/export_c/files")
    assert resp.status_code == 200
    body = resp.json()
    assert body["export_id"] == "export_c"
    assert body["count"] >= 1
    paths = [f["path"] for f in body["files"]]
    assert "tables/sessions/data.parquet" in paths


def test_files_404_for_unknown_export(admin_api_client, export_root):
    resp = admin_api_client.get("/api/admin/export/exports/does_not_exist/files")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# File download
# --------------------------------------------------------------------------- #

def test_file_download_returns_the_bytes(admin_api_client, export_root):
    _make_export("export_d", export_root, bytes_=17)
    resp = admin_api_client.get(
        "/api/admin/export/exports/export_d/files/tables/sessions/data.parquet"
    )
    assert resp.status_code == 200
    assert resp.content == b"x" * 17


def test_file_download_rejects_traversal(admin_api_client, export_root):
    _make_export("export_e", export_root)
    resp = admin_api_client.get(
        "/api/admin/export/exports/export_e/files/../manifest.json"
    )
    assert resp.status_code == 404


def test_file_download_rejects_tmp_files(admin_api_client, export_root):
    export_dir = _make_export("export_f", export_root)
    (export_dir / "tables" / "sessions" / "data.parquet.tmp").write_bytes(b"partial")
    resp = admin_api_client.get(
        "/api/admin/export/exports/export_f/files/tables/sessions/data.parquet.tmp"
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Archive
# --------------------------------------------------------------------------- #

def test_archive_start_returns_job_dict(admin_api_client, monkeypatch, export_root):
    _make_export("export_g", export_root)
    monkeypatch.setattr(dataset_export, "start_archive_job", lambda export_id: _FakeJob())
    resp = admin_api_client.post("/api/admin/export/exports/export_g/archive")
    assert resp.status_code == 200
    assert resp.json()["job_id"] == "abc123def456"


def test_archive_start_conflict(admin_api_client, monkeypatch, export_root):
    def _boom(export_id):
        raise RuntimeError("busy")

    monkeypatch.setattr(dataset_export, "start_archive_job", _boom)
    resp = admin_api_client.post("/api/admin/export/exports/export_h/archive")
    assert resp.status_code == 409


def test_archive_download_404_when_not_built(admin_api_client, export_root):
    _make_export("export_i", export_root)
    resp = admin_api_client.get("/api/admin/export/exports/export_i/archive")
    assert resp.status_code == 404


def test_archive_download_returns_the_zip_when_ready(admin_api_client, export_root):
    _make_export("export_j", export_root)
    archive_path = exports.archive_path("export_j")
    archive_path.write_bytes(b"PK\x03\x04zip-bytes")
    resp = admin_api_client.get("/api/admin/export/exports/export_j/archive")
    assert resp.status_code == 200
    assert resp.content == b"PK\x03\x04zip-bytes"


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #

def test_delete_requires_confirm_to_equal_id(admin_api_client, export_root):
    _make_export("export_k", export_root)
    resp = admin_api_client.delete("/api/admin/export/exports/export_k", params={"confirm": "wrong"})
    assert resp.status_code == 400
    assert (export_root / "export_k").exists()


def test_delete_conflict_when_in_use(admin_api_client, monkeypatch, export_root):
    _make_export("export_l", export_root)
    monkeypatch.setattr(dataset_export, "export_in_use", lambda export_id: True)
    resp = admin_api_client.delete("/api/admin/export/exports/export_l", params={"confirm": "export_l"})
    assert resp.status_code == 409
    assert (export_root / "export_l").exists()


def test_delete_succeeds_and_frees_bytes(admin_api_client, monkeypatch, export_root):
    _make_export("export_m", export_root, bytes_=99)
    monkeypatch.setattr(dataset_export, "export_in_use", lambda export_id: False)
    resp = admin_api_client.delete("/api/admin/export/exports/export_m", params={"confirm": "export_m"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["export_id"] == "export_m"
    assert body["bytes_freed"] > 0
    assert not (export_root / "export_m").exists()


def test_delete_404_for_unknown_export(admin_api_client, export_root):
    resp = admin_api_client.delete("/api/admin/export/exports/does_not_exist", params={"confirm": "does_not_exist"})
    assert resp.status_code == 404
