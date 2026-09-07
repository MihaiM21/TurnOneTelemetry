"""Offline tests for the static media endpoints (driver images, team logos)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("disable_rate_limit")


def test_list_driver_images_shape(client):
    resp = client.get("/api/static/media/drivers", params={"year": 2026})
    assert resp.status_code == 200
    body = resp.json()
    assert body["year"] == 2026
    assert body["images"], "expected a non-empty grid"
    first = body["images"][0]
    assert set(first) == {"code", "name", "team", "number", "image_url", "available"}
    assert first["image_url"] == f"/assets/drivers/{first['code']}.png"


def test_driver_image_redirects_when_file_present(client):
    # VER.png ships in assets/drivers/.
    resp = client.get("/api/static/media/drivers/ver", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/assets/drivers/VER.png"


def test_driver_image_404_when_missing(client):
    resp = client.get("/api/static/media/drivers/ZZZ", follow_redirects=False)
    assert resp.status_code == 404


def test_list_team_logos_shape(client):
    resp = client.get("/api/static/media/teams", params={"year": 2026})
    assert resp.status_code == 200
    body = resp.json()
    assert body["year"] == 2026
    assert body["logos"]
    first = body["logos"][0]
    assert set(first) == {"name", "short_name", "color", "logo_url", "available"}
    assert first["logo_url"] == f"/assets/logos/{first['short_name']}.png"


def test_team_logo_404_when_missing(client):
    resp = client.get("/api/static/media/teams/RBR", follow_redirects=False)
    assert resp.status_code == 404


def test_media_endpoints_need_no_api_key(client):
    # No X-API-Key header at all.
    assert client.get("/api/static/media/drivers", params={"year": 2025}).status_code == 200
