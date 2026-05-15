"""Integration tests for analysis v2 routers — auth and validation only.

We don't exercise the data-generation path (FastF1 / static client / Mongo).
We just verify that authentication and input validation work for each
endpoint surface.
"""
import pytest


V2_ENDPOINTS = [
    "/api/v2/top-speed-telemetry-data",
    "/api/v2/top-speed-st-data",
    "/api/v2/throttle-comparison-data",
    "/api/v2/speed-distribution-data",
    "/api/v2/qualifying-results-data",
    "/api/v2/track-comparison-data",
]


@pytest.mark.parametrize("endpoint", V2_ENDPOINTS)
def test_v2_endpoint_requires_api_key(client, disable_rate_limit, endpoint):
    resp = client.get(endpoint, params={"year": 2024, "gp": 1, "session": "Q"})
    assert resp.status_code == 401, f"{endpoint} should require API key"


@pytest.mark.parametrize("endpoint", V2_ENDPOINTS)
def test_v2_endpoint_rejects_invalid_year(client, disable_rate_limit, auth_headers, endpoint):
    resp = client.get(
        endpoint,
        params={"year": 1900, "gp": 1, "session": "Q"},
        headers=auth_headers("standard"),
    )
    assert resp.status_code == 422, f"{endpoint} should reject year=1900"
