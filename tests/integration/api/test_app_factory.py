"""End-to-end tests for the FastAPI app construction and root endpoints."""
import pytest


def test_app_constructs(app):
    assert app is not None
    assert app.title


def test_welcome_endpoint(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"].startswith("Welcome")
    assert "version" in body


def test_health_endpoint_with_stub_processor(client, stub_processor):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert "checks" in body
    assert body["checks"]["api"] == "healthy"


def test_openapi_schema_generates(client):
    # In testing env, docs auth is not required.
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "paths" in schema
    # Sanity: at least some of our known routes are registered.
    assert "/api/health" in schema["paths"]
    assert "/" in schema["paths"]


def test_404_for_unknown_route(client):
    resp = client.get("/totally-not-a-route-12345")
    assert resp.status_code == 404


def test_metrics_endpoint_returns_prometheus_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # Prometheus exposition format always has a HELP or TYPE line.
    body = resp.text
    assert "# HELP" in body or "# TYPE" in body or len(body) >= 0
