"""Route-level coverage for the social-pack admin API (`/api/admin/social/*`).

Mirrors ``test_admin_export_routes.py``: an admin-key gated JSON API, weighted
toward the refusals (no key, consumer key, conflicts, bad scope, traversal) with
the worker stubbed so nothing renders.
"""
import pytest

from src.api import admin_security
from src.core.config import settings
from src.workers import social_pack

JOB_ID = "a" * 12


@pytest.fixture(autouse=True)
def social_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "social_dir", str(tmp_path / "social"))
    with social_pack._JOBS_LOCK:
        social_pack._JOBS.clear()
    yield tmp_path / "social"
    with social_pack._JOBS_LOCK:
        social_pack._JOBS.clear()


@pytest.fixture
def admin_api_client(app, client, monkeypatch):
    """A client whose requests satisfy ``require_admin_key`` (consumer keys must not; see below)."""
    from src.api.routers.admin import require_admin_key

    monkeypatch.setattr(admin_security, "enforce_ip_allowlist", lambda request: None)
    app.dependency_overrides[require_admin_key] = lambda: "admin-test-key"
    yield client
    app.dependency_overrides.pop(require_admin_key, None)


class _FakeJob:
    def as_dict(self):
        return {"job_id": JOB_ID, "kind": "social_pack", "status": "queued", "total": 3,
                "scope": {"year": 2026}, "formats": ["portrait"]}


BODY = {"year": 2026, "gp": "Italian Grand Prix", "session": "R"}


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/social/jobs"),
    ("get", f"/api/admin/social/jobs/{JOB_ID}"),
    ("get", f"/api/admin/social/jobs/{JOB_ID}/download"),
    ("post", f"/api/admin/social/jobs/{JOB_ID}/cancel"),
])
def test_endpoints_reject_no_key_and_consumer_keys(client, method, path):
    assert getattr(client, method)(path).status_code in (401, 403)
    resp = getattr(client, method)(path, headers={"X-API-Key": "test-standard-key"})
    assert resp.status_code in (401, 403)


def test_generate_rejects_a_consumer_key(client):
    resp = client.post("/api/admin/social/generate", json=BODY, headers={"X-API-Key": "test-standard-key"})
    assert resp.status_code in (401, 403)


# --------------------------------------------------------------------------- #
# Generate
# --------------------------------------------------------------------------- #
def test_generate_passes_the_body_to_the_worker(admin_api_client, monkeypatch):
    captured = {}

    def fake_start(**kw):
        captured.update(kw)
        return _FakeJob()

    monkeypatch.setattr(social_pack, "start_social_job", fake_start)
    resp = admin_api_client.post("/api/admin/social/generate", json={
        **BODY, "formats": ["story"], "pairs": [["VER", "NOR"]], "include_season": True,
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["job_id"] == JOB_ID
    assert captured == {"year": 2026, "gp": "Italian Grand Prix", "session": "R", "formats": ["story"],
                        "pairs": [["VER", "NOR"]], "include_season": True}


def test_generate_defaults(admin_api_client, monkeypatch):
    captured = {}
    monkeypatch.setattr(social_pack, "start_social_job", lambda **kw: captured.update(kw) or _FakeJob())
    resp = admin_api_client.post("/api/admin/social/generate", json=BODY)
    assert resp.status_code == 200
    assert captured["formats"] == ["portrait", "story", "landscape"]
    assert captured["pairs"] is None and captured["include_season"] is False


def test_generate_body_is_a_body_not_a_query_param(admin_api_client, monkeypatch):
    """Regression guard for the PEP 563 trap documented in CLAUDE.md."""
    monkeypatch.setattr(social_pack, "start_social_job", lambda **kw: _FakeJob())
    resp = admin_api_client.post("/api/admin/social/generate", json=BODY)
    assert resp.status_code != 422, "body degraded into a query parameter"


def test_generate_conflict_when_already_running(admin_api_client, monkeypatch):
    def boom(**kw):
        raise RuntimeError("a social pack job is already running")

    monkeypatch.setattr(social_pack, "start_social_job", boom)
    assert admin_api_client.post("/api/admin/social/generate", json=BODY).status_code == 409


def test_generate_bad_scope_is_400(admin_api_client, monkeypatch):
    def boom(**kw):
        raise ValueError("unknown session 'XX'")

    monkeypatch.setattr(social_pack, "start_social_job", boom)
    resp = admin_api_client.post("/api/admin/social/generate", json={**BODY, "session": "XX"})
    assert resp.status_code == 400
    assert "unknown session" in resp.json()["detail"]


def test_generate_rejects_an_unknown_format_and_a_missing_gp(admin_api_client, monkeypatch):
    monkeypatch.setattr(social_pack, "start_social_job", lambda **kw: _FakeJob())
    assert admin_api_client.post("/api/admin/social/generate",
                                 json={**BODY, "formats": ["huge"]}).status_code == 422
    assert admin_api_client.post("/api/admin/social/generate",
                                 json={"year": 2026, "session": "R"}).status_code == 422
    assert admin_api_client.post("/api/admin/social/generate",
                                 json={**BODY, "formats": []}).status_code == 422


def test_generate_real_worker_validation_end_to_end(admin_api_client):
    """No stub: the worker's own validation maps to 400 without starting a thread."""
    resp = admin_api_client.post("/api/admin/social/generate",
                                 json={**BODY, "pairs": [["VER", "VER"]]})
    assert resp.status_code == 400
    assert social_pack.running_jobs() == []


# --------------------------------------------------------------------------- #
# Jobs list / status / cancel
# --------------------------------------------------------------------------- #
def test_jobs_list(admin_api_client, monkeypatch):
    monkeypatch.setattr(social_pack, "list_jobs", lambda limit, status: [
        {"job_id": JOB_ID, "status": "running", "scope": {}}])
    resp = admin_api_client.get("/api/admin/social/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1 and body["jobs"][0]["job_id"] == JOB_ID


def test_job_status_found_and_missing(admin_api_client, monkeypatch):
    monkeypatch.setattr(social_pack, "get_job_dict",
                        lambda jid: {"job_id": jid, "status": "running"} if jid == JOB_ID else None)
    assert admin_api_client.get(f"/api/admin/social/jobs/{JOB_ID}").json()["status"] == "running"
    assert admin_api_client.get(f"/api/admin/social/jobs/{'b' * 12}").status_code == 404


def test_job_id_must_match_the_id_shape(admin_api_client):
    assert admin_api_client.get("/api/admin/social/jobs/not-a-valid-id").status_code == 404
    assert admin_api_client.post("/api/admin/social/jobs/not-a-valid-id/cancel").status_code == 404
    assert admin_api_client.get("/api/admin/social/jobs/not-a-valid-id/download").status_code == 404


def test_job_cancel_ok_and_conflict(admin_api_client, monkeypatch):
    monkeypatch.setattr(social_pack, "cancel_job", lambda jid: True)
    resp = admin_api_client.post(f"/api/admin/social/jobs/{JOB_ID}/cancel")
    assert resp.status_code == 200
    assert resp.json() == {"job_id": JOB_ID, "cancel_requested": True}

    monkeypatch.setattr(social_pack, "cancel_job", lambda jid: False)
    assert admin_api_client.post(f"/api/admin/social/jobs/{JOB_ID}/cancel").status_code == 409


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def _register(job_id, pack, *, zip_ready=True):
    social_pack._register_job(social_pack.SocialJob(job_id=job_id, pack=pack, zip_ready=zip_ready))


def test_download_serves_the_zip(admin_api_client, social_root):
    base = social_root / "2026" / "ItalianGrandPrix" / "R"
    base.mkdir(parents=True)
    (base / "social_pack.zip").write_bytes(b"PK\x03\x04zip-bytes")
    _register(JOB_ID, "2026/ItalianGrandPrix/R")
    resp = admin_api_client.get(f"/api/admin/social/jobs/{JOB_ID}/download")
    assert resp.status_code == 200
    assert resp.content == b"PK\x03\x04zip-bytes"
    assert resp.headers["content-type"] == "application/zip"


def test_download_404_for_unknown_job_or_missing_zip(admin_api_client):
    assert admin_api_client.get(f"/api/admin/social/jobs/{'c' * 12}/download").status_code == 404
    _register(JOB_ID, "2026/ItalianGrandPrix/R")  # flagged ready, but nothing on disk
    assert admin_api_client.get(f"/api/admin/social/jobs/{JOB_ID}/download").status_code == 404
    _register("d" * 12, "2026/ItalianGrandPrix/R", zip_ready=False)
    assert admin_api_client.get(f"/api/admin/social/jobs/{'d' * 12}/download").status_code == 404


def test_download_rejects_a_pack_path_that_escapes_the_root(admin_api_client, tmp_path, social_root):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "social_pack.zip").write_bytes(b"do-not-serve")
    social_root.mkdir(parents=True, exist_ok=True)
    _register(JOB_ID, "../secret")
    resp = admin_api_client.get(f"/api/admin/social/jobs/{JOB_ID}/download")
    assert resp.status_code == 404
    assert b"do-not-serve" not in resp.content
    _register("e" * 12, "/etc")
    assert admin_api_client.get(f"/api/admin/social/jobs/{'e' * 12}/download").status_code == 404
