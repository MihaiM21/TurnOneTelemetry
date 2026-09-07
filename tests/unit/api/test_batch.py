"""Tests for POST /api/v2/batch (src/api/routers/batch_v2.py).

The router is not wired into ``src.api.app:create_app`` yet (a sibling task
owns ``app.py`` and mounts it separately), so every test here builds a
throwaway ``FastAPI()`` app with just this router included, rather than going
through the full ``create_app()`` / ``client`` fixtures in ``tests/conftest.py``.

The centerpiece is ``test_batch_shares_one_session_store_across_features``:
the whole point of this endpoint is that N features of one session share one
``SessionDataStore`` instead of building N of them, and that test is the only
thing that actually demonstrates it.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.core.security import rate_limiting as rl
from src.services.analysis.v2 import _helpers as helpers_module
from src.services.analysis.v2 import session_store as session_store_module
from src.services.analysis.v2.registry import (
    KIND_SINGLETON,
    FeatureEntry,
    PlotSpec,
)


# --------------------------------------------------------------------------- #
# App wiring
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module", autouse=True)
def _ensure_limiter():
    """batch_v2's @apply_tiered_limit binds to the limiter at import time."""
    if rl._limiter_instance is None:
        rl.init_limiter()


@pytest.fixture()
def batch_module():
    from src.api.routers import batch_v2
    return batch_v2


@pytest.fixture()
def api_client(batch_module):
    app = FastAPI()
    app.state.limiter = rl.get_limiter()
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(batch_module.router)
    return TestClient(app)


@pytest.fixture()
def auth_headers():
    return {"X-API-Key": "test-standard-key"}


def _body(**overrides):
    payload = {"year": 2024, "gp": 1, "session": "Q", "features": [{"key": "qualifying_results"}]}
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _CountingStore:
    """Stands in for SessionDataStore: cheap, and counts real construction."""

    created = 0

    def __init__(self, year, identifier, session, client=None):
        _CountingStore.created += 1
        self.year = year
        self.identifier = identifier
        self.session = session

    @classmethod
    def reset(cls):
        cls.created = 0


def _fake_singleton_entry(key: str, generate) -> FeatureEntry:
    spec = PlotSpec(data_type=key, label=key, applies_to=frozenset(), generate=generate)
    return FeatureEntry(
        key=key, label=key, kind=KIND_SINGLETON, group="Field-wide",
        applies_to=frozenset(), cost="light", spec=spec,
    )


# --------------------------------------------------------------------------- #
# The point of the endpoint
# --------------------------------------------------------------------------- #
def test_batch_shares_one_session_store_across_features(monkeypatch, batch_module, api_client, auth_headers):
    """N features of one session must build exactly one SessionDataStore."""
    _CountingStore.reset()
    monkeypatch.setattr(session_store_module, "SessionDataStore", _CountingStore)
    monkeypatch.setattr(batch_module, "get_plot_data_from_mongo", lambda *a, **kw: None)

    def _make_generate(feature_id: str):
        def _generate(y, ident, e):
            # Exercises the real build_session_store() -- the same seam every
            # V2 analysis module uses -- so this proves the production code
            # path shares stores, not a mock of the sharing itself.
            store = helpers_module.build_session_store(y, ident, e)
            return {"feature": feature_id, "store_id": id(store)}
        return _generate

    keys = [f"fake_feature_{i}" for i in range(5)]
    entries = {k: _fake_singleton_entry(k, _make_generate(k)) for k in keys}
    monkeypatch.setattr(batch_module, "feature_by_key", lambda k: entries.get(k))

    resp = api_client.post(
        "/api/v2/batch",
        json=_body(features=[{"key": k} for k in keys]),
        headers=auth_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 5
    assert all(r["status"] == "ok" for r in body["results"]), body["results"]

    store_ids = {r["data"]["store_id"] for r in body["results"]}
    assert len(store_ids) == 1, "every feature should have shared the same SessionDataStore instance"
    assert _CountingStore.created == 1, (
        f"expected exactly one SessionDataStore construction, got {_CountingStore.created}"
    )


def test_outside_the_batch_endpoint_stores_are_not_shared(monkeypatch):
    """Sanity check on the seam itself: without a scope, each call is fresh.

    Confirms the counting fake actually counts, and that the endpoint's
    sharing behaviour above comes from shared_session_stores() and not from
    some accidental memoization inside build_session_store() itself.
    """
    _CountingStore.reset()
    monkeypatch.setattr(session_store_module, "SessionDataStore", _CountingStore)

    for _ in range(3):
        helpers_module.build_session_store(2024, 1, "Q")

    assert _CountingStore.created == 3


# --------------------------------------------------------------------------- #
# Request validation
# --------------------------------------------------------------------------- #
def test_empty_feature_list_is_rejected(api_client, auth_headers):
    resp = api_client.post("/api/v2/batch", json=_body(features=[]), headers=auth_headers)
    assert resp.status_code == 400
    assert "non-empty" in resp.json()["detail"]


def test_batch_over_the_cap_is_rejected(batch_module, api_client, auth_headers):
    many = [{"key": "qualifying_results"} for _ in range(batch_module.MAX_BATCH_ITEMS + 1)]
    resp = api_client.post("/api/v2/batch", json=_body(features=many), headers=auth_headers)
    assert resp.status_code == 400
    assert str(batch_module.MAX_BATCH_ITEMS) in resp.json()["detail"]


def test_unknown_feature_key_rejects_whole_request(api_client, auth_headers):
    resp = api_client.post(
        "/api/v2/batch",
        json=_body(features=[{"key": "qualifying_results"}, {"key": "not_a_real_feature"}]),
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "not_a_real_feature" in resp.json()["detail"]


def test_missing_api_key_is_401(api_client):
    resp = api_client.post("/api/v2/batch", json=_body())
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Per-item behaviour: one bad feature must not fail the batch
# --------------------------------------------------------------------------- #
def test_feature_not_applicable_to_session_is_a_per_item_error(api_client, auth_headers):
    """qualifying_results only applies to Q/SQ -- requesting it for R must not 500."""
    resp = api_client.post(
        "/api/v2/batch",
        json=_body(session="R", features=[{"key": "qualifying_results"}]),
        headers=auth_headers,
    )
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["status"] == "error"
    assert "does not apply" in result["error"]


def test_per_driver_feature_without_driver_is_a_per_item_error(api_client, auth_headers):
    resp = api_client.post(
        "/api/v2/batch",
        json=_body(features=[{"key": "speed_distribution"}]),  # per-driver kind, no `driver` given
        headers=auth_headers,
    )
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["status"] == "error"
    assert "driver" in result["error"]


def test_per_pair_feature_missing_second_driver_is_a_per_item_error(api_client, auth_headers):
    resp = api_client.post(
        "/api/v2/batch",
        json=_body(features=[{"key": "track_comparison", "driver1": "VER"}]),
        headers=auth_headers,
    )
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["status"] == "error"


def test_per_driver_lap_feature_without_lap_is_rejected_not_generated(api_client, auth_headers):
    """lap_all_data must never be generated implicitly (drivers x laps is huge)."""
    resp = api_client.post(
        "/api/v2/batch",
        json=_body(features=[{"key": "lap_all_data", "driver": "VER"}]),
        headers=auth_headers,
    )
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["status"] == "error"
    assert "lap" in result["error"]


def test_season_scope_feature_is_rejected_in_a_session_batch(api_client, auth_headers):
    resp = api_client.post(
        "/api/v2/batch",
        json=_body(features=[{"key": "teammate_battle"}]),
        headers=auth_headers,
    )
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["status"] == "error"
    assert "session" in result["error"].lower()


def test_one_bad_feature_does_not_fail_the_others(monkeypatch, batch_module, api_client, auth_headers):
    monkeypatch.setattr(batch_module, "get_plot_data_from_mongo", lambda *a, **kw: None)

    def _good_generate(y, ident, e):
        return {"ok": True}

    entries = {
        "good_feature": _fake_singleton_entry("good_feature", _good_generate),
    }
    real_lookup = batch_module.feature_by_key
    monkeypatch.setattr(
        batch_module, "feature_by_key",
        lambda k: entries.get(k) or real_lookup(k),
    )

    resp = api_client.post(
        "/api/v2/batch",
        json=_body(features=[
            {"key": "good_feature"},
            {"key": "qualifying_results"},  # applies (Q session) but no real data -> per-item error, not a crash
        ]),
        headers=auth_headers,
    )
    assert resp.status_code == 200
    results = {r["key"]: r for r in resp.json()["results"]}
    assert results["good_feature"]["status"] == "ok"
    assert results["good_feature"]["data"] == {"ok": True}
    # Whatever qualifying_results did (hit Mongo/network and failed, or found
    # nothing), it must have come back as a per-item result, not an exception
    # that took down the whole response.
    assert results["qualifying_results"]["status"] in ("ok", "error")


# --------------------------------------------------------------------------- #
# Cache short-circuit
# --------------------------------------------------------------------------- #
def test_stored_data_short_circuits_generation(monkeypatch, batch_module, api_client, auth_headers):
    """When Mongo already has the data_type, the generator must not run at all."""
    def _boom(y, ident, e):
        raise AssertionError("generator should not run when Mongo already has the data")

    entry = _fake_singleton_entry("cached_feature", _boom)
    monkeypatch.setattr(batch_module, "feature_by_key", lambda k: entry if k == "cached_feature" else None)
    monkeypatch.setattr(
        batch_module, "get_plot_data_from_mongo",
        lambda *a, **kw: {"data": {"from": "mongo"}, "metadata": {}},
    )

    resp = api_client.post(
        "/api/v2/batch",
        json=_body(features=[{"key": "cached_feature"}]),
        headers=auth_headers,
    )
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["status"] == "ok"
    assert result["data"] == {"from": "mongo"}


# --------------------------------------------------------------------------- #
# Session normalization
# --------------------------------------------------------------------------- #
def test_long_form_session_name_is_normalized_for_applicability(monkeypatch, batch_module, api_client, auth_headers):
    """'Qualifying' must resolve to the same abbreviation the registry uses."""
    seen: dict = {}

    def _generate(y, ident, e):
        seen["session"] = e
        return {"ok": True}

    entry = _fake_singleton_entry("session_probe", _generate)
    monkeypatch.setattr(batch_module, "feature_by_key", lambda k: entry if k == "session_probe" else None)
    monkeypatch.setattr(batch_module, "get_plot_data_from_mongo", lambda *a, **kw: None)

    resp = api_client.post(
        "/api/v2/batch",
        json=_body(session="Qualifying", features=[{"key": "session_probe"}]),
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["session"] == "Q"
    assert seen["session"] == "Q"
