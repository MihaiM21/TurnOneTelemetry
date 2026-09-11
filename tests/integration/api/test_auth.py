"""Integration tests for API key auth on protected endpoints."""
import pytest


# A consumer-tier endpoint: requires Depends(verify_api_key) and no Mongo work.
# NB: /api/monitoring/system used to serve this role, but it exposes other
# callers' request URLs and client IPs, so it is now admin-only. Consumer keys
# are expected to be rejected there -- see ADMIN_ENDPOINT below.
PROTECTED_ENDPOINT = "/api/monitoring/stats"

# Operator-only. Ordinary ALLOWED_API_KEYS / PREMIUM_API_KEYS consumer keys
# must NOT reach this; only ADMIN_API_KEYS or a DB key whose owner is_admin.
ADMIN_ENDPOINT = "/api/monitoring/system"


def test_missing_api_key_returns_401(client, disable_rate_limit):
    resp = client.get(PROTECTED_ENDPOINT)
    assert resp.status_code == 401
    assert "API key" in resp.json()["detail"]


def test_invalid_api_key_returns_403(client, disable_rate_limit, auth_headers):
    resp = client.get(PROTECTED_ENDPOINT, headers=auth_headers("invalid"))
    assert resp.status_code == 403


def test_valid_standard_key_passes_auth(client, disable_rate_limit, auth_headers):
    resp = client.get(PROTECTED_ENDPOINT, headers=auth_headers("standard"))
    # 200 if monitor works, 500 if implementation glitch — we only assert
    # auth wasn't the reason for a non-2xx.
    assert resp.status_code not in (401, 403)


def test_valid_premium_key_passes_auth(client, disable_rate_limit, auth_headers):
    resp = client.get(PROTECTED_ENDPOINT, headers=auth_headers("premium"))
    assert resp.status_code not in (401, 403)


def test_consumer_key_is_refused_admin_endpoint(client, disable_rate_limit, auth_headers):
    """A consumer key must not be an admin key.

    Regression guard: ``require_admin_key`` used to admit *any* key present in
    ALLOWED_API_KEYS or PREMIUM_API_KEYS, so the public website's own key could
    purge caches, revoke keys and restore backups.
    """
    for tier in ("standard", "premium"):
        resp = client.get(ADMIN_ENDPOINT, headers=auth_headers(tier))
        assert resp.status_code == 403, (
            f"{tier} consumer key unexpectedly reached an admin endpoint"
        )


def test_admin_endpoint_still_requires_a_key(client, disable_rate_limit):
    assert client.get(ADMIN_ENDPOINT).status_code == 401
