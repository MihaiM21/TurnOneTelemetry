"""Route-level coverage for the storage-cleanup surfaces.

Two surfaces reach the same service and authenticate differently:
``/api/admin/storage/*`` (admin API key) and ``/admin/cleanup`` (cookie session
+ CSRF). Both are destructive, so the tests here are weighted toward the
refusals: no admin key, no CSRF, no valid confirm token, no unscoped wipe.
"""
import pytest

from src.api import admin_security
from src.api.routers import admin_ui
from src.services import storage_cleanup
from src.workers import plot_inventory


@pytest.fixture
def plot_tree(tmp_path, monkeypatch):
    """A real miniature plot tree, with one GP stored under two spellings."""
    files = {
        "2023/BritishGrandPrix/Race/Speed Distribution 2023 British GP Race.png": 100,
        "2023/BritishGrandPrix/Q/Speed Distribution 2023 British GP Q.png": 200,
        "2025/British Grand Prix/R/Top speed comparison 2025 British GP R.png": 300,
        "2025/BritishGrandPrix/R/Top speed comparison 2025 British GP R.png": 400,
    }
    root = tmp_path / "outputs" / "plots"
    for rel, size in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def plots_only(monkeypatch):
    """Restrict enumeration to the filesystem so no test needs Mongo or Redis."""
    monkeypatch.setattr(storage_cleanup, "ALL_LAYERS", (storage_cleanup.LAYER_PLOTS,))
    monkeypatch.setattr(
        "src.api.routers.admin_storage.ALL_LAYERS", (storage_cleanup.LAYER_PLOTS,)
    )
    monkeypatch.setattr(
        "src.api.routers.admin_ui.ALL_LAYERS", (storage_cleanup.LAYER_PLOTS,)
    )


@pytest.fixture
def admin_client(client, monkeypatch):
    monkeypatch.setattr(admin_security, "enforce_ip_allowlist", lambda request: None)
    monkeypatch.setattr(admin_ui, "enforce_ip_allowlist", lambda request: None)
    monkeypatch.setattr(plot_inventory, "available_years", lambda: [2025, 2023])
    client.cookies.set(admin_ui._COOKIE, admin_security.create_session_token())
    return client


@pytest.fixture
def csrf(admin_client):
    return admin_security.csrf_token_for(admin_client.cookies.get(admin_ui._COOKIE))


@pytest.fixture
def admin_api_client(app, client, monkeypatch):
    """A client whose requests satisfy ``require_admin_key``.

    The test env seeds only *consumer* keys (``ALLOWED_API_KEYS`` /
    ``PREMIUM_API_KEYS``), and those must never pass the admin gate -- that
    separation is the point of ``test_storage_endpoints_reject_a_consumer_key``
    below. So rather than smuggling a consumer key past the gate, the
    dependency itself is overridden for the tests that need an authorised
    caller.
    """
    from src.api.routers.admin import require_admin_key

    monkeypatch.setattr(admin_security, "enforce_ip_allowlist", lambda request: None)
    app.dependency_overrides[require_admin_key] = lambda: "admin-test-key"
    yield client
    app.dependency_overrides.pop(require_admin_key, None)


# --------------------------------------------------------------------------- #
# Admin UI
# --------------------------------------------------------------------------- #

def test_cleanup_page_requires_a_session(client, plot_tree, plots_only):
    resp = client.get("/admin/cleanup", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/login"


def test_cleanup_page_renders_totals_and_orphans(admin_client, plot_tree, plots_only):
    resp = admin_client.get("/admin/cleanup")
    assert resp.status_code == 200
    body = resp.text
    assert "Storage cleanup" in body
    assert "Duplicate Grand Prix directories" in body
    # The 2025 GP exists under two spellings and must be reported.
    assert "BritishGrandPrix" in body


def test_cleanup_preview_lists_matching_items(admin_client, plot_tree, plots_only):
    resp = admin_client.get("/admin/cleanup?preview=true&year=2023")
    assert resp.status_code == 200
    assert "Preview" in resp.text
    assert "2 item(s)" in resp.text


def test_cleanup_preview_deletes_nothing(admin_client, plot_tree, plots_only):
    before = sorted(p.name for p in plot_tree.rglob("*") if p.is_file())
    admin_client.get("/admin/cleanup?preview=true&year=2023")
    assert sorted(p.name for p in plot_tree.rglob("*") if p.is_file()) == before


def test_cleanup_purge_requires_csrf(admin_client, plot_tree, plots_only):
    scope = storage_cleanup.CleanupScope(year=2023, layers=(storage_cleanup.LAYER_PLOTS,))
    token = storage_cleanup.plan_cleanup(scope)["confirm_token"]

    resp = admin_client.post(
        "/admin/cleanup/purge",
        data={"confirm_token": token, "csrf_token": "wrong", "year": 2023},
    )
    assert resp.status_code == 403
    assert len(list(plot_tree.rglob("*.png"))) == 4


def test_cleanup_purge_requires_a_session(client, plot_tree, plots_only):
    resp = client.post(
        "/admin/cleanup/purge",
        data={"confirm_token": "x", "csrf_token": "y"},
    )
    assert resp.status_code == 401
    assert len(list(plot_tree.rglob("*.png"))) == 4


def test_cleanup_purge_deletes_the_previewed_scope(admin_client, csrf, plot_tree, plots_only):
    scope = storage_cleanup.CleanupScope(year=2023, layers=(storage_cleanup.LAYER_PLOTS,))
    token = storage_cleanup.plan_cleanup(scope)["confirm_token"]

    resp = admin_client.post(
        "/admin/cleanup/purge",
        data={"confirm_token": token, "csrf_token": csrf, "year": 2023,
              "layers": ["plots"]},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "purged=2" in resp.headers["location"]
    assert not (plot_tree / "2023").exists()
    assert len(list((plot_tree / "2025").rglob("*.png"))) == 2


def test_cleanup_purge_rejects_a_stale_token(admin_client, csrf, plot_tree, plots_only):
    scope = storage_cleanup.CleanupScope(year=2023, layers=(storage_cleanup.LAYER_PLOTS,))
    token = storage_cleanup.plan_cleanup(scope)["confirm_token"]
    (plot_tree / "2023" / "BritishGrandPrix" / "Race" / "new.png").write_bytes(b"z")

    resp = admin_client.post(
        "/admin/cleanup/purge",
        data={"confirm_token": token, "csrf_token": csrf, "year": 2023,
              "layers": ["plots"]},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "error=" in resp.headers["location"]
    assert len(list(plot_tree.rglob("*.png"))) == 5


def test_unscoped_ui_purge_needs_the_typed_confirmation(admin_client, csrf, plot_tree, plots_only):
    """The checkbox alone is too easy to leave set from a previous run, so an
    unscoped purge also requires typing the phrase."""
    scope = storage_cleanup.CleanupScope(layers=(storage_cleanup.LAYER_PLOTS,))
    token = storage_cleanup.plan_cleanup(scope)["confirm_token"]

    resp = admin_client.post(
        "/admin/cleanup/purge",
        data={"confirm_token": token, "csrf_token": csrf, "layers": ["plots"],
              "allow_full_purge": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "error=" in resp.headers["location"]
    assert len(list(plot_tree.rglob("*.png"))) == 4, "unscoped purge ran without confirmation"


def test_unscoped_ui_purge_proceeds_with_the_typed_confirmation(
    admin_client, csrf, plot_tree, plots_only
):
    scope = storage_cleanup.CleanupScope(layers=(storage_cleanup.LAYER_PLOTS,))
    token = storage_cleanup.plan_cleanup(scope)["confirm_token"]

    resp = admin_client.post(
        "/admin/cleanup/purge",
        data={"confirm_token": token, "csrf_token": csrf, "layers": ["plots"],
              "allow_full_purge": "true", "typed_confirmation": "DELETE EVERYTHING"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "purged=4" in resp.headers["location"]
    assert list(plot_tree.rglob("*.png")) == []


# --------------------------------------------------------------------------- #
# Admin JSON API
# --------------------------------------------------------------------------- #

def test_storage_endpoints_reject_a_consumer_key(client, plot_tree, plots_only):
    """Consumer keys must not reach an admin surface -- the exact regression
    that made every ordinary key a full admin key."""
    for method, path in (
        ("get", "/api/admin/storage/totals"),
        ("get", "/api/admin/storage/orphans"),
        ("post", "/api/admin/storage/preview"),
    ):
        resp = getattr(client, method)(path, headers={"X-API-Key": "test-standard-key"})
        assert resp.status_code in (401, 403), f"{path} accepted a consumer key"


def test_storage_endpoints_reject_no_key(client, plot_tree, plots_only):
    resp = client.get("/api/admin/storage/totals")
    assert resp.status_code in (401, 403)


def test_purge_rejects_an_unscoped_body_without_opt_in(admin_api_client, plot_tree, plots_only):
    scope = storage_cleanup.CleanupScope(layers=(storage_cleanup.LAYER_PLOTS,))
    token = storage_cleanup.plan_cleanup(scope)["confirm_token"]

    resp = admin_api_client.post(
        "/api/admin/storage/purge",
        json={"scope": {"layers": ["plots"]}, "confirm_token": token},
    )
    assert resp.status_code == 400
    assert "unscoped" in resp.json()["detail"].lower()
    assert len(list(plot_tree.rglob("*.png"))) == 4


def test_purge_rejects_a_stale_token_with_409(admin_api_client, plot_tree, plots_only):
    scope = storage_cleanup.CleanupScope(year=2023, layers=(storage_cleanup.LAYER_PLOTS,))
    token = storage_cleanup.plan_cleanup(scope)["confirm_token"]
    (plot_tree / "2023" / "BritishGrandPrix" / "Race" / "new.png").write_bytes(b"z")

    resp = admin_api_client.post(
        "/api/admin/storage/purge",
        json={"scope": {"year": 2023, "layers": ["plots"]}, "confirm_token": token},
    )
    assert resp.status_code == 409
    assert len(list(plot_tree.rglob("*.png"))) == 5


def test_preview_rejects_an_unknown_layer(admin_api_client, plot_tree, plots_only):
    resp = admin_api_client.post("/api/admin/storage/preview?layers=not_a_layer")
    assert resp.status_code == 400
    assert "Unknown storage layer" in resp.json()["detail"]


def test_purge_body_is_a_body_not_a_query_param(admin_api_client, plot_tree, plots_only):
    """Regression guard for the PEP 563 trap: `from __future__ import
    annotations` plus @apply_tiered_limit silently turns a Pydantic body into a
    required *query* parameter, and the endpoint starts 422-ing with
    `loc: ['query', 'body']`."""
    resp = admin_api_client.post(
        "/api/admin/storage/purge",
        json={"scope": {"year": 2023, "layers": ["plots"]}, "confirm_token": "x"},
    )
    assert resp.status_code != 422, "body degraded into a query parameter"
    if resp.status_code == 422:  # pragma: no cover - diagnostic only
        assert "query" not in str(resp.json())
