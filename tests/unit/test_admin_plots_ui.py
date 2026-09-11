"""Offline tests for the admin plots UI backing helpers."""

import json
from datetime import datetime, timezone

from src.workers import plot_inventory


def test_available_years_includes_current_and_sorted_desc(monkeypatch):
    monkeypatch.setattr(plot_inventory, "_years_with_v2_data", lambda: [2022, 2024])
    years = plot_inventory.available_years()

    now = datetime.now(timezone.utc).year
    assert now in years and (now - 1) in years
    assert 2022 in years and 2024 in years
    assert years == sorted(years, reverse=True)
    assert len(years) == len(set(years))  # deduped


def test_available_years_without_v2_data(monkeypatch):
    monkeypatch.setattr(plot_inventory, "_years_with_v2_data", lambda: [])
    years = plot_inventory.available_years()
    now = datetime.now(timezone.utc).year
    assert years == [now, now - 1]


def test_job_dict_is_json_serializable():
    job = plot_inventory.PlotGenJob(job_id="abc", scope={"year": 2025}, started_at="t")
    job.errors.append("boom")
    payload = job.as_dict()
    # The status route returns exactly this via JSONResponse — must be JSON-safe.
    encoded = json.dumps(payload)
    restored = json.loads(encoded)
    assert restored["job_id"] == "abc"
    assert restored["scope"] == {"year": 2025}
    assert "boom" in restored["errors"]
    assert set(["status", "total", "done", "success", "failed", "skipped"]).issubset(restored)


def test_unknown_job_lookup_returns_none():
    # Backs the 404 branch of GET /admin/plots/jobs/{id}/status.
    assert plot_inventory.get_job("does-not-exist") is None


def test_available_events_shape_and_excludes_testing(monkeypatch):
    schedule = [
        {"round": 1, "grandPrix": "Australian Grand Prix", "sessions": []},
        {"round": 2, "grandPrix": "Chinese Grand Prix", "sessions": []},
    ]
    monkeypatch.setattr(plot_inventory, "get_season_events", lambda y: schedule)
    events = plot_inventory.available_events(2026)

    assert events == [
        {"round_nr": 1, "name": "Australian Grand Prix"},
        {"round_nr": 2, "name": "Chinese Grand Prix"},
    ]
    # The curated schedule never yields testing; names are the picker values.
    assert all("test" not in e["name"].lower() for e in events)


def test_available_events_handles_missing_schedule(monkeypatch):
    def _boom(_y):
        raise RuntimeError("no data")

    monkeypatch.setattr(plot_inventory, "get_season_events", _boom)
    assert plot_inventory.available_events(1999) == []
