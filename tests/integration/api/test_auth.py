"""Integration tests for API key auth on protected endpoints."""
import pytest


# /api/monitoring/system requires Depends(verify_api_key) and no Mongo work.
PROTECTED_ENDPOINT = "/api/monitoring/system"


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
