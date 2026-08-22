"""Shared pytest fixtures.

NOTE: this module sets environment variables BEFORE importing any `src.*`
module. `src.core.config.settings` is a cached, module-level instance, so we
must seed the environment first.
"""
from __future__ import annotations

import os

# ---- Environment seeding (must run before any `src` import) ------------------
# Explicit assignment (not setdefault) so we override values from any local .env
os.environ["MPLBACKEND"] = "Agg"
os.environ["ENVIRONMENT"] = "testing"
os.environ["MONGODB_PASSWORD"] = "test-password"
os.environ["API_SECRET_KEY"] = "test-secret-key-do-not-use-in-prod"
os.environ["ALLOWED_API_KEYS"] = "test-standard-key,another-standard-key,test-premium-key"
os.environ["PREMIUM_API_KEYS"] = "test-premium-key"
os.environ["DOCS_USERNAME"] = "testdocs"
os.environ["DOCS_PASSWORD"] = "testpw"
os.environ["DOCS_AUTH_ALWAYS"] = "false"
os.environ["ENABLE_BACKGROUND_PROCESSOR"] = "false"
os.environ["ENABLE_CIRCUITS_SYNC"] = "false"
# Keep unit tests fully offline and deterministic: never touch a real Redis.
# Otherwise a warm response cache leaks state across tests (e.g. a shared
# livetiming stream key served stale data between fixtures).
os.environ["ENABLE_RESPONSE_CACHE"] = "false"
os.environ["FASTF1_CACHE_DIR"] = "./cache"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["LOG_FILE"] = "logs/test.log"
os.environ["SENTRY_DSN"] = ""
os.environ["F1_PROXY_BASE_URL"] = ""

# ---- Stdlib / third-party ---------------------------------------------------
from typing import Iterator
from unittest.mock import MagicMock

import mongomock
import pytest
from fastapi.testclient import TestClient


# ---- Pre-import patches ------------------------------------------------------
# Patch pymongo.MongoClient so any module that imports it picks up mongomock.
# This must happen before src.repositories.mongo is imported.
import pymongo  # noqa: E402

_real_mongo_client = pymongo.MongoClient


def _mongomock_factory(*args, **kwargs):
    # ServerApi kwarg isn't supported by mongomock; strip it.
    kwargs.pop("server_api", None)
    return mongomock.MongoClient(*args, **kwargs)


pymongo.MongoClient = _mongomock_factory  # type: ignore[assignment]
import pymongo.mongo_client  # noqa: E402
pymongo.mongo_client.MongoClient = _mongomock_factory  # type: ignore[assignment]


# ---- Fixtures ----------------------------------------------------------------
@pytest.fixture
def settings_obj():
    """Return the live settings object (configured from env above)."""
    from src.core.config import settings
    return settings


@pytest.fixture
def stub_processor(monkeypatch):
    """Replace background processor with a stub so /api/health is testable
    without spinning up workers or hitting Mongo."""
    from src.workers import processor as proc_module

    fake = MagicMock()
    fake.running = False
    fake.processed_sessions = set()
    monkeypatch.setattr(proc_module, "_processor", fake)
    return fake


@pytest.fixture
def app(monkeypatch, stub_processor):
    """Construct a fresh FastAPI app for the test.

    Lifespan is suppressed by using TestClient without `with` semantics in the
    `client` fixture (TestClient runs lifespan on enter; we skip it here by
    not entering a context manager when not needed).
    """
    # Reset slowapi limiter singleton between tests so init_limiter rebuilds.
    from src.core.security import rate_limiting as rl
    monkeypatch.setattr(rl, "_limiter_instance", None)

    from src.api.app import create_app
    return create_app()


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    """TestClient that DOES NOT trigger lifespan (no background processor,
    no real Mongo connection attempt)."""
    # Pass raise_server_exceptions=True so we surface bugs.
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client


@pytest.fixture
def disable_rate_limit(app):
    """Disable slowapi enforcement for the duration of the test."""
    app.state.limiter.enabled = False
    yield
    app.state.limiter.enabled = True


@pytest.fixture
def auth_headers():
    """Header factory: auth_headers() → standard, auth_headers('premium') → premium."""
    def _make(tier: str = "standard") -> dict:
        if tier == "premium":
            return {"X-API-Key": "test-premium-key"}
        if tier == "invalid":
            return {"X-API-Key": "definitely-not-a-real-key"}
        return {"X-API-Key": "test-standard-key"}
    return _make
