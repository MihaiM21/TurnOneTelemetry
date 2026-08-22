"""Route-level coverage for the admin console (offline).

Before this file there was no test that actually hit an ``/admin`` route — the
existing ``test_admin_plots_ui.py`` only exercised helper functions. That left
the cookie-session gate, the CSRF check, template rendering and the JSON
endpoints the progress bar depends on completely unverified.
"""

import pytest

from src.api import admin_security
from src.api.routers import admin_ui
from src.workers import plot_inventory


@pytest.fixture
def admin_client(client, monkeypatch):
    """A TestClient carrying a valid admin session cookie.

    The cookie value is derived from the JWT/API secret (see
    ``admin_ui._expected_token``), and the IP allowlist is bypassed because the
    TestClient's synthetic client host is not in any configured allowlist.
    """
    monkeypatch.setattr(admin_security, "enforce_ip_allowlist", lambda request: None)
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    client.cookies.set(admin_ui._COOKIE, admin_ui._expected_token())
    return client


@pytest.fixture
def csrf(admin_client):
    return admin_security.csrf_token_for(admin_client.cookies.get(admin_ui._COOKIE))


@pytest.fixture
def offline_inventory(monkeypatch):
    """Stub the schedule + Mongo reads so no network or DB is touched."""
    monkeypatch.setattr(plot_inventory, "available_years", lambda: [2025, 2024])
    monkeypatch.setattr(
        plot_inventory, "available_events",
        lambda year: [{"round_nr": 1, "name": "Test Grand Prix"}],
    )
    monkeypatch.setattr(
        plot_inventory, "compute_missing",
        lambda **kw: {
            "version": "v2",
            "scope": kw,
            "years_scanned": [2025],
            "total_sessions": 1,
            "total_expected_plots": 3,
            "total_missing_plots": 1,
            "total_extra_plots": 2,
            "grand_prix": [{
                "year": 2025, "round_nr": 1, "event_name": "Test Grand Prix",
                "sessions": [{
                    "session_type": "R",
                    "expected": ["driver_pace", "teams_pace", "race_story"],
                    "present": ["driver_pace", "teams_pace"],
                    "missing": ["race_story"],
                    "extra_groups": [{"prefix": "track_map_speed", "count": 2,
                                      "sample": ["track_map_speed_VER"]}],
                    "extra_count": 2,
                    "labels": {"driver_pace": "Driver Pace", "teams_pace": "Teams Pace",
                               "race_story": "Race Story"},
                }],
            }],
        },
    )
    monkeypatch.setattr(
        plot_inventory, "season_inventory",
        lambda year: {"year": year, "features": [], "extra_groups": [], "is_current_season": False},
    )
    monkeypatch.setattr(plot_inventory, "list_jobs", lambda *a, **kw: [])


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/admin", "/admin/plots", "/admin/jobs",
                                  "/admin/cache", "/admin/data", "/admin/ops"])
def test_pages_redirect_when_not_signed_in(client, monkeypatch, path):
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/admin/login"


def test_json_endpoints_401_when_not_signed_in(client, monkeypatch):
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    response = client.get("/admin/plots/jobs/whatever/status")
    assert response.status_code == 401


def test_login_page_renders(client, monkeypatch):
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert "/static/admin/admin.css" in response.text


# --------------------------------------------------------------------------- #
# Generate page
# --------------------------------------------------------------------------- #
def test_plots_page_renders_labels_and_catalog(admin_client, offline_inventory):
    response = admin_client.get("/admin/plots?year=2025&session=R")
    assert response.status_code == 200
    body = response.text

    # Human labels, not raw slugs (the old page rendered data_type strings).
    assert "Race Story" in body
    assert "Driver Pace" in body
    # Extra stored keys are surfaced grouped rather than hidden.
    assert "track_map_speed" in body
    # The feature selector is driven by the catalog.
    assert "const CATALOG" in body
    assert "top_speed_telemetry" in body


def test_plots_page_ignores_a_gp_from_another_year(admin_client, offline_inventory):
    """A stale GP selection must not silently scope the job to a foreign event."""
    captured = {}

    def _capture(**kw):
        captured.update(kw)
        return {"version": "v2", "scope": kw, "years_scanned": [], "total_sessions": 0,
                "total_expected_plots": 0, "total_missing_plots": 0, "total_extra_plots": 0,
                "grand_prix": []}

    import src.workers.plot_inventory as pi
    pi.compute_missing = _capture

    admin_client.get("/admin/plots?year=2025&gp=Not+A+Real+Event")
    assert captured["identifier"] is None


def test_scope_values_are_json_encoded_into_the_script(admin_client, offline_inventory):
    """Scope strings go through |tojson so they cannot break out of the literal."""
    response = admin_client.get("/admin/plots?year=2025&session=R")
    assert 'session: "R"' in response.text
    # No year selected renders JSON null, not the literal string "None".
    assert '"None"' not in response.text


def test_estimate_endpoint_returns_a_breakdown(admin_client, monkeypatch, csrf):
    monkeypatch.setattr(
        plot_inventory, "estimate_plan",
        lambda **kw: {"units": 42, "by_feature": [{"feature": "driver_pace",
                                                   "label": "Driver Pace", "units": 42}],
                      "warnings": [], "sessions": 1, "truncated": False,
                      "drivers_by_session": {}},
    )
    response = admin_client.post(
        "/admin/plots/estimate",
        data={"year": 2025, "session": "R", "features": ["driver_pace"]},
    )
    assert response.status_code == 200
    assert response.json()["units"] == 42


def test_generate_requires_a_valid_csrf_token(admin_client):
    response = admin_client.post(
        "/admin/plots/generate",
        data={"year": 2025, "csrf_token": "forged"},
    )
    assert response.status_code in (400, 403)


def test_generate_starts_a_job(admin_client, monkeypatch, csrf):
    started = {}

    class _Job:
        def as_dict(self):
            return {"job_id": "abc123", "status": "queued"}

    def _start(**kw):
        started.update(kw)
        return _Job()

    monkeypatch.setattr(plot_inventory, "find_conflicting_job", lambda scope: None)
    monkeypatch.setattr(plot_inventory, "start_generation_job", _start)

    response = admin_client.post(
        "/admin/plots/generate",
        data={
            "year": 2025, "gp": "Test Grand Prix", "session": "R",
            "features": ["driver_pace", "track_map"], "drivers": ["ver", "nor"],
            "concurrency": 3, "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    assert response.json()["job_id"] == "abc123"
    assert started["selection"].features == ["driver_pace", "track_map"]
    # Driver TLAs are normalized to the stored casing.
    assert started["selection"].drivers == ["VER", "NOR"]
    assert started["concurrency"] == 3


def test_generate_rejects_an_overlapping_job(admin_client, monkeypatch, csrf):
    """Two backfills over the same sessions duplicate every upstream fetch."""
    monkeypatch.setattr(
        plot_inventory, "find_conflicting_job",
        lambda scope: {"job_id": "inflight", "scope": scope},
    )
    response = admin_client.post(
        "/admin/plots/generate",
        data={"year": 2025, "session": "R", "csrf_token": csrf},
    )
    assert response.status_code == 409
    assert "inflight" in response.json()["detail"]


def test_generate_clamps_concurrency(admin_client, monkeypatch, csrf):
    started = {}

    class _Job:
        def as_dict(self):
            return {"job_id": "x"}

    monkeypatch.setattr(plot_inventory, "find_conflicting_job", lambda scope: None)
    monkeypatch.setattr(
        plot_inventory, "start_generation_job",
        lambda **kw: (started.update(kw), _Job())[1],
    )

    admin_client.post(
        "/admin/plots/generate",
        data={"year": 2025, "concurrency": 99, "csrf_token": csrf},
    )
    assert started["concurrency"] == plot_inventory.MAX_CONCURRENCY


# --------------------------------------------------------------------------- #
# Job status / cancel
# --------------------------------------------------------------------------- #
def test_job_status_404_for_unknown_job(admin_client, monkeypatch):
    monkeypatch.setattr(plot_inventory, "get_job_dict", lambda jid: None)
    assert admin_client.get("/admin/plots/jobs/nope/status").status_code == 404


def test_job_status_serves_from_the_durable_store(admin_client, monkeypatch):
    """A poll must be answerable by a worker that never ran the job."""
    monkeypatch.setattr(
        plot_inventory, "get_job_dict",
        lambda jid: {"job_id": jid, "status": "running", "done": 5, "total": 10},
    )
    body = admin_client.get("/admin/plots/jobs/xyz/status").json()
    assert body["status"] == "running"
    assert body["done"] == 5


def test_cancel_requires_csrf(admin_client):
    response = admin_client.post("/admin/plots/jobs/abc/cancel", data={"csrf_token": "bad"})
    assert response.status_code in (400, 403)


def test_cancel_flags_the_job(admin_client, monkeypatch, csrf):
    monkeypatch.setattr(plot_inventory, "cancel_job", lambda jid: True)
    response = admin_client.post("/admin/plots/jobs/abc/cancel", data={"csrf_token": csrf})
    assert response.status_code == 200
    assert response.json()["cancel_requested"] is True


def test_cancel_conflicts_on_a_finished_job(admin_client, monkeypatch, csrf):
    monkeypatch.setattr(plot_inventory, "cancel_job", lambda jid: False)
    response = admin_client.post("/admin/plots/jobs/abc/cancel", data={"csrf_token": csrf})
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# Jobs pages
# --------------------------------------------------------------------------- #
def test_jobs_page_lists_history(admin_client, monkeypatch):
    monkeypatch.setattr(
        plot_inventory, "list_jobs",
        lambda *a, **kw: [{
            "job_id": "deadbeef", "scope": {"year": 2025, "gp": "Monaco Grand Prix",
                                            "session": "R"},
            "status": "completed", "done": 10, "total": 10,
            "success": 9, "failed": 1, "skipped": 0,
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:05:00+00:00",
        }],
    )
    response = admin_client.get("/admin/jobs")
    assert response.status_code == 200
    assert "deadbeef" in response.text
    assert "Monaco Grand Prix" in response.text


def test_job_detail_renders_duration_and_per_feature(admin_client, monkeypatch):
    monkeypatch.setattr(
        plot_inventory, "get_job_dict",
        lambda jid: {
            "job_id": jid, "scope": {"year": 2025}, "selection": {"features": ["race_story"]},
            "status": "completed", "done": 3, "total": 3, "success": 2, "failed": 1,
            "skipped": 0, "errors": ["2025 R1 R race_story: boom"],
            "per_feature": {"race_story": {"success": 2, "failed": 1, "skipped": 0}},
            "warnings": [], "worker": "host:1",
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:02:30+00:00",
        },
    )
    response = admin_client.get("/admin/jobs/abc")
    assert response.status_code == 200
    assert "2m 30s" in response.text
    assert "boom" in response.text


def test_job_detail_404(admin_client, monkeypatch):
    monkeypatch.setattr(plot_inventory, "get_job_dict", lambda jid: None)
    assert admin_client.get("/admin/jobs/missing").status_code == 404


# --------------------------------------------------------------------------- #
# Cache / data pages
# --------------------------------------------------------------------------- #
def test_cache_page_renders_inventory(admin_client, monkeypatch):
    # admin_ui imports the read-model by name, so patch it on admin_ui.
    monkeypatch.setattr(
        admin_ui, "cache_inventory",
        lambda year=None: {
            "year": year,
            "raw": {"sessions": [{"session_key": "2025_1_R", "year": 2025, "round_nr": 1,
                                  "session": "R", "streams": ["car_data"], "stream_count": 1,
                                  "bytes": 2048, "schema_drift": 1, "newest": None}],
                    "session_count": 1, "files": 1, "bytes": 2048,
                    "schema_version": 1, "schema_drift": 1},
            # A bundle row must carry a populated "keys" list: rendering it via
            # dotted lookup resolves the dict's built-in .keys method instead,
            # which raised a 500 in the real page.
            "bundles": {"sessions": [{"doc_id": "2025_1_R", "year": 2025, "round_nr": 1,
                                      "session": "R", "event_name": "Test Grand Prix",
                                      "keys": ["lap_times", "pit_stops"],
                                      "schema_version": 1, "schema_drift": False,
                                      "created_at": None}],
                        "session_count": 1, "total": 1,
                        "schema_version": 1, "schema_drift": 0},
        },
    )
    response = admin_client.get("/admin/cache")
    assert response.status_code == 200
    assert "2025_1_R" in response.text
    assert "car_data" in response.text
    assert "lap_times, pit_stops" in response.text


def test_cache_purge_requires_csrf(admin_client):
    response = admin_client.post(
        "/admin/cache/purge/raw",
        data={"session_key": "2025_1_R", "csrf_token": "bad"},
    )
    assert response.status_code in (400, 403)


def test_cache_purge_raw(admin_client, monkeypatch, csrf):
    purged = []
    monkeypatch.setattr(
        admin_ui.raw_stream_cache, "delete_raw_streams",
        lambda key: purged.append(key) or 3,
    )
    response = admin_client.post(
        "/admin/cache/purge/raw",
        data={"session_key": "2025_1_R", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert purged == ["2025_1_R"]


def test_data_page_lists_stored_keys(admin_client, monkeypatch):
    monkeypatch.setattr(plot_inventory, "available_years", lambda: [2025])
    monkeypatch.setattr(
        admin_ui, "browse_stored_data",
        lambda year, gp=None, session=None: {
            "year": year, "total_sessions": 1, "total_data_types": 2,
            "rows": [{"year": year, "gp_id": "2025_MON", "event_name": "MonacoGrandPrix",
                      "round_nr": 6, "session_type": "R",
                      "data_types": ["race_story", "legacy_orphan_key"], "count": 2}],
        },
    )
    response = admin_client.get("/admin/data?year=2025")
    assert response.status_code == 200
    # Legacy/orphan keys are visible here even though the inventory hides them.
    assert "legacy_orphan_key" in response.text
    assert "2025_MON" in response.text


# --------------------------------------------------------------------------- #
# Ops / backups pages
# --------------------------------------------------------------------------- #
def test_ops_page_renders(admin_client):
    """Exercises the real system-metrics and health probes, which must fail soft."""
    response = admin_client.get("/admin/ops")
    assert response.status_code == 200
    assert "Processor" in response.text
    assert "Ensure indexes" in response.text


def test_backups_page_renders_when_subsystem_disabled(admin_client):
    """With BACKUP_ENABLED off the page must explain itself, not 500."""
    response = admin_client.get("/admin/backups")
    assert response.status_code == 200
    assert "Restore" in response.text


def test_restore_refuses_without_a_drill_target_or_typed_confirmation(admin_client, csrf):
    """A destructive restore needs an explicit acknowledgement."""
    response = admin_client.post(
        "/admin/backups/restore",
        data={"backup_id": "b-1", "mongo_target_db": "", "confirm_text": "",
              "csrf_token": csrf},
    )
    assert response.status_code == 400
    assert "RESTORE" in response.json()["detail"]


def test_data_delete_removes_one_key(admin_client, monkeypatch, csrf):
    calls = []
    monkeypatch.setattr(
        admin_ui.MongoDBManager, "delete_session_data",
        lambda self, gp_id, session_type, data_type, year=None: calls.append(
            (gp_id, session_type, data_type, year)
        ) or True,
    )
    response = admin_client.post(
        "/admin/data/delete",
        data={"year": 2025, "gp_id": "2025_MON", "session_type": "r",
              "data_type": "race_story", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert calls == [("2025_MON", "R", "race_story", 2025)]
