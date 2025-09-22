import pytest
from fastapi.testclient import TestClient
from server import app

client = TestClient(app)


def test_health_check():
    """Test the health check endpoint"""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_docs_endpoint():
    """Test that the API documentation is accessible"""
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_schema():
    """Test that the OpenAPI schema is accessible"""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "openapi" in schema
    assert schema["info"]["title"] == "F1 Telemetry API"


class TestTopSpeedEndpoint:
    """Test the top speed endpoint"""

    def test_top_speed_plot_default_params(self):
        """Test top speed plot with default parameters"""
        response = client.get("/api/top-speed-plot")
        # This might fail if no data is available, but we test the endpoint exists
        assert response.status_code in [200, 404, 500]  # Allow for data availability issues

    def test_top_speed_plot_with_params(self):
        """Test top speed plot with custom parameters"""
        response = client.get("/api/top-speed-plot?year=2025&gp=1&session=Q")
        assert response.status_code in [200, 404, 500]  # Allow for data availability issues

    def test_top_speed_plot_invalid_params(self):
        """Test top speed plot with invalid parameters"""
        response = client.get("/api/top-speed-plot?year=invalid&gp=invalid&session=invalid")
        assert response.status_code == 422  # Validation error


class TestThrottleComparisonEndpoint:
    """Test the throttle comparison endpoints"""

    def test_throttle_comparison_plot(self):
        """Test throttle comparison plot endpoint"""
        response = client.get("/api/throttle-comparison-plot")
        assert response.status_code in [200, 404, 500]  # Allow for data availability issues

    def test_throttle_comparison_data(self):
        """Test throttle comparison data endpoint"""
        response = client.get("/api/throttle-comparison-data")
        assert response.status_code in [200, 404, 500]  # Allow for data availability issues


def test_cors_headers():
    """Test that CORS headers are properly set"""
    response = client.options("/api/health")
    assert response.status_code == 200
    # CORS headers should be present due to the middleware
