"""Integration tests for /api/static/* endpoints (drivers, teams)."""
import pytest


def test_drivers_for_known_year(client, disable_rate_limit):
    resp = client.get("/api/static/drivers", params={"year": 2026})
    assert resp.status_code == 200
    body = resp.json()
    assert "drivers" in body
    assert isinstance(body["drivers"], list)
    assert len(body["drivers"]) > 0


def test_drivers_invalid_year_validation(client, disable_rate_limit):
    resp = client.get("/api/static/drivers", params={"year": 1999})
    assert resp.status_code == 422


def test_teams_for_known_year(client, disable_rate_limit):
    resp = client.get("/api/static/teams", params={"year": 2026})
    assert resp.status_code == 200
    body = resp.json()
    assert "teams" in body
    assert isinstance(body["teams"], list)


def test_team_details_unknown_404(client, disable_rate_limit):
    resp = client.get("/api/static/teams/Atlantis-F1", params={"year": 2026})
    assert resp.status_code == 404


def test_driver_details_unknown_404(client, disable_rate_limit):
    resp = client.get("/api/static/drivers/Nobody", params={"year": 2026})
    assert resp.status_code == 404
