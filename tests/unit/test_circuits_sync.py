"""Unit tests for :mod:`src.workers.circuits_sync` (multiviewer top-up sync).

``circuits_loader.CIRCUITS_DIR`` is redirected to a temp directory, and every
network call (``fetch_circuits_list`` / ``fetch_circuit_data``) plus the Mongo
mirror (``src.repositories.circuit_layouts.store_layout``) is monkeypatched --
no real HTTP or Mongo involved.
"""
from __future__ import annotations

import json

import pytest

from src.ingestion import circuits_loader
from src.repositories import circuit_layouts as circuit_layouts_repo
from src.workers import circuits_sync as sync_mod


@pytest.fixture(autouse=True)
def _circuits_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(circuits_loader, "CIRCUITS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def seeded_year(tmp_path):
    """Pre-seed 2026 with a multiviewer Monza file and a telemetry-derived
    Madring file (the latter counts as "missing" per the module docstring)."""
    year_dir = tmp_path / "2026"
    year_dir.mkdir(parents=True)
    (year_dir / "39_monza.json").write_text(json.dumps({
        "circuit_id": "39", "year": 2026, "name": "Monza", "country": "Italy", "country_code": "ITA",
        "location": "Monza", "rotation": 0.0, "track_outline": {"x": [], "y": []}, "corners": [],
        "marshal_lights": [], "marshal_sectors": [], "source": "multiviewer",
    }))
    (year_dir / "153_madring.json").write_text(json.dumps({
        "circuit_id": "153", "year": 2026, "name": "Madring", "country": "Spain", "country_code": "ESP",
        "location": "Madrid", "rotation": 170.7, "track_outline": {"x": [], "y": []}, "corners": [],
        "marshal_lights": [], "marshal_sectors": [], "source": "telemetry",
    }))
    (year_dir / "all_circuits.json").write_text(json.dumps({
        "year": 2026, "total_circuits": 2,
        "circuits": [
            {"circuit_id": "39", "name": "Monza", "country": "Italy", "country_code": "ITA",
             "circuit_key": 39, "years_available": [2026]},
            {"circuit_id": "153", "name": "Madring", "country": "Spain", "country_code": "ESP",
             "circuit_key": 153, "years_available": [2026]},
        ],
    }))
    return year_dir


@pytest.fixture
def patch_sync_sources(monkeypatch):
    fetch_calls = []
    recorder = []

    def fake_fetch_circuits_list():
        return {
            "153": {"name": "Madring", "country": "Spain", "years": [2026],
                    "circuitKey": 153, "iocCountryCode": "ESP"},
            "39": {"name": "Monza", "country": "Italy", "years": [2026],
                   "circuitKey": 39, "iocCountryCode": "ITA"},
        }

    def fake_fetch_circuit_data(circuit_id, year):
        fetch_calls.append((circuit_id, year))
        return {
            "circuitName": "Madring", "countryName": "Spain", "countryIocCode": "ESP",
            "location": "Madrid", "rotation": 170.7,
            "x": [0.0, 1.0], "y": [0.0, 1.0],
            "corners": [{"number": 1, "angle": 10.0, "length": 100.0,
                         "trackPosition": {"x": 1.0, "y": 2.0}}],
            "marshalLights": [], "marshalSectors": [],
        }

    def fake_store_layout(layout_dict, summary_dict):
        recorder.append((layout_dict, summary_dict))
        return True

    monkeypatch.setattr(sync_mod, "fetch_circuits_list", fake_fetch_circuits_list)
    monkeypatch.setattr(sync_mod, "fetch_circuit_data", fake_fetch_circuit_data)
    monkeypatch.setattr(sync_mod, "YEARS", [2026])
    monkeypatch.setattr(circuit_layouts_repo, "store_layout", fake_store_layout)
    return {"fetch_calls": fetch_calls, "recorder": recorder}


def test_find_missing_circuits_and_years_only_flags_telemetry_derived(seeded_year, patch_sync_sources):
    missing = sync_mod.find_missing_circuits_and_years()
    assert missing == [("153", 2026)]


def test_sync_missing_circuits_replaces_telemetry_layout(seeded_year, patch_sync_sources):
    added = sync_mod.sync_missing_circuits()

    assert added == 1
    assert patch_sync_sources["fetch_calls"] == [("153", 2026)]

    data = json.loads((seeded_year / "153_madring.json").read_text())
    assert data["source"] == "multiviewer"

    manifest = json.loads((seeded_year / "all_circuits.json").read_text())
    assert len(manifest["circuits"]) == 2


def test_find_missing_and_sync_fail_open_on_network_error(seeded_year, monkeypatch):
    def raising_fetch_list():
        raise RuntimeError("network down")

    monkeypatch.setattr(sync_mod, "fetch_circuits_list", raising_fetch_list)
    monkeypatch.setattr(sync_mod, "YEARS", [2026])

    assert sync_mod.find_missing_circuits_and_years() == []
    assert sync_mod.sync_missing_circuits() == 0
