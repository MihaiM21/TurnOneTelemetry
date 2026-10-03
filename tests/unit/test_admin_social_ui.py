"""Route-level coverage for the admin social-pack console (offline).

Mirrors ``test_admin_export_ui.py``: a real signed session cookie, IP allowlist
bypassed, and the worker (``admin_ui.social_pack``) stubbed rather than run.
"""

from types import SimpleNamespace

import pytest

from src.api import admin_security
from src.api.routers import admin_ui
from src.core.config import settings
from src.workers import social_pack

JOB_ID = "a" * 12


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
def social_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "social_dir", str(tmp_path / "social"))
    with social_pack._JOBS_LOCK:
        social_pack._JOBS.clear()
    yield tmp_path / "social"
    with social_pack._JOBS_LOCK:
        social_pack._JOBS.clear()


@pytest.fixture
def offline_jobs(monkeypatch):
    monkeypatch.setattr(admin_ui.social_pack, "list_jobs", lambda *a, **kw: [])


def _body(csrf, **extra):
    return {"year": 2026, "gp": "16", "session": "R", "formats": ["portrait"], "csrf_token": csrf, **extra}


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
def test_page_redirects_when_unauthenticated(client, monkeypatch):
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    response = client.get("/admin/social", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/admin/login"


def test_start_401_when_unauthenticated(client, monkeypatch):
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    assert client.post("/admin/social/start", json=_body("x")).status_code == 401


def test_job_status_401_when_unauthenticated(client, monkeypatch):
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    assert client.get(f"/admin/social/jobs/{JOB_ID}").status_code == 401


def test_cancel_401_when_unauthenticated(client, monkeypatch):
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    assert client.post(f"/admin/social/jobs/{JOB_ID}/cancel", data={"csrf_token": "x"}).status_code == 401


def test_download_redirects_when_unauthenticated(client, monkeypatch):
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    response = client.get(f"/admin/social/jobs/{JOB_ID}/download", follow_redirects=False)
    assert response.status_code == 302


# --------------------------------------------------------------------------- #
# Page render
# --------------------------------------------------------------------------- #
def test_page_renders_the_form(admin_client, offline_jobs):
    response = admin_client.get("/admin/social")
    assert response.status_code == 200
    text = response.text
    assert "Social pack" in text
    assert 'class="soc-format"' in text
    for fmt in ("landscape", "square", "portrait", "story"):
        assert f'value="{fmt}"' in text
    assert 'id="socPairs"' in text and 'id="socSeason"' in text
    assert "<style" not in text.split("</head>")[1]  # no per-page style blocks
    assert "No social packs yet." in text


def test_page_lists_jobs_with_a_download_link(admin_client, monkeypatch):
    monkeypatch.setattr(admin_ui.social_pack, "list_jobs", lambda *a, **kw: [
        {"job_id": JOB_ID, "status": "completed", "scope": {"year": 2026, "gp": 16, "session": "R"},
         "formats": ["portrait", "story"], "pairs": [["VER", "NOR"]], "done": 5, "total": 5,
         "success": 5, "failed": 0, "zip_ready": True, "started_at": "2026-09-13T15:00:00+00:00"},
        {"job_id": "b" * 12, "status": "failed", "scope": {"year": 2026, "gp": 16, "session": "Q"},
         "done": 0, "total": 0, "success": 0, "failed": 0, "zip_ready": False},
    ])
    text = admin_client.get("/admin/social").text
    assert f"/admin/social/jobs/{JOB_ID}/download" in text
    assert f"/admin/social/jobs/{'b' * 12}/download" not in text
    assert "VER-NOR" in text


def test_nav_links_to_the_page(admin_client, offline_jobs):
    assert 'href="/admin/social"' in admin_client.get("/admin/social").text


# --------------------------------------------------------------------------- #
# Start
# --------------------------------------------------------------------------- #
def test_start_requires_csrf(admin_client, monkeypatch):
    monkeypatch.setattr(admin_ui.social_pack, "start_social_job",
                        lambda **kw: pytest.fail("must not start without a valid CSRF token"))
    response = admin_client.post("/admin/social/start", json=_body("bad"))
    assert response.status_code == 403


def test_start_parses_the_pairs_textarea_and_returns_the_job(admin_client, monkeypatch, csrf):
    captured = {}

    def fake_start(**kw):
        captured.update(kw)
        return SimpleNamespace(as_dict=lambda: {"job_id": JOB_ID, "status": "queued"})

    monkeypatch.setattr(admin_ui.social_pack, "start_social_job", fake_start)
    response = admin_client.post(
        "/admin/social/start", json=_body(csrf, pairs="ver,nor\nLEC HAM\n", include_season=True))
    assert response.status_code == 200
    assert response.json()["job_id"] == JOB_ID
    assert captured == {"year": 2026, "gp": "16", "session": "R", "formats": ["portrait"],
                        "pairs": [("VER", "NOR"), ("LEC", "HAM")], "include_season": True}


def test_start_blank_pairs_means_derive(admin_client, monkeypatch, csrf):
    captured = {}
    monkeypatch.setattr(admin_ui.social_pack, "start_social_job",
                        lambda **kw: captured.update(kw) or SimpleNamespace(as_dict=lambda: {"job_id": JOB_ID}))
    assert admin_client.post("/admin/social/start", json=_body(csrf)).status_code == 200
    assert captured["pairs"] is None


def test_start_conflict_is_409(admin_client, monkeypatch, csrf):
    def boom(**kw):
        raise RuntimeError("a social pack job is already running")

    monkeypatch.setattr(admin_ui.social_pack, "start_social_job", boom)
    response = admin_client.post("/admin/social/start", json=_body(csrf))
    assert response.status_code == 409 and "error" in response.json()


def test_start_bad_scope_is_400(admin_client, csrf):
    response = admin_client.post("/admin/social/start", json=_body(csrf, session="nonsense"))
    assert response.status_code == 400 and "error" in response.json()
    response = admin_client.post("/admin/social/start", json=_body(csrf, pairs="VER"))
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Status / cancel / download
# --------------------------------------------------------------------------- #
def test_job_status_404_and_found(admin_client, monkeypatch):
    monkeypatch.setattr(admin_ui.social_pack, "get_job_dict", lambda job_id: None)
    assert admin_client.get(f"/admin/social/jobs/{JOB_ID}").status_code == 404
    monkeypatch.setattr(admin_ui.social_pack, "get_job_dict", lambda job_id: {"job_id": job_id, "status": "running"})
    response = admin_client.get(f"/admin/social/jobs/{JOB_ID}")
    assert response.status_code == 200 and response.json()["status"] == "running"


def test_cancel_requires_csrf(admin_client):
    response = admin_client.post(f"/admin/social/jobs/{JOB_ID}/cancel", data={"csrf_token": "bad"})
    assert response.status_code == 403


def test_cancel_flags_the_job(admin_client, monkeypatch, csrf):
    monkeypatch.setattr(admin_ui.social_pack, "cancel_job", lambda job_id: True)
    response = admin_client.post(f"/admin/social/jobs/{JOB_ID}/cancel", data={"csrf_token": csrf})
    assert response.status_code == 200
    assert response.json() == {"job_id": JOB_ID, "cancel_requested": True}


def test_download_serves_the_zip(admin_client, social_root):
    base = social_root / "2026" / "X" / "R"
    base.mkdir(parents=True)
    (base / "social_pack.zip").write_bytes(b"PK-zip")
    social_pack._register_job(social_pack.SocialJob(job_id=JOB_ID, pack="2026/X/R", zip_ready=True))
    response = admin_client.get(f"/admin/social/jobs/{JOB_ID}/download")
    assert response.status_code == 200 and response.content == b"PK-zip"


def test_download_404_when_missing_or_escaping(admin_client, tmp_path, social_root):
    assert admin_client.get(f"/admin/social/jobs/{JOB_ID}/download").status_code == 404
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "social_pack.zip").write_bytes(b"do-not-serve")
    social_root.mkdir(parents=True, exist_ok=True)
    social_pack._register_job(social_pack.SocialJob(job_id=JOB_ID, pack="../secret", zip_ready=True))
    response = admin_client.get(f"/admin/social/jobs/{JOB_ID}/download")
    assert response.status_code == 404
    assert b"do-not-serve" not in response.content
