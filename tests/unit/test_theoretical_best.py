"""Offline unit tests for Theoretical Best Lap (V2).

Synthetic dicts exercise the sorting / clamping / exclusion rules with no
network, Redis, or Mongo dependency.
"""
from __future__ import annotations

import os

import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.analysis.v2 import theoretical_best as tb


# ----------------------------------------------------------------------
# Session validation
# ----------------------------------------------------------------------
def test_assert_valid_session_rejects_race():
    with pytest.raises(DataNotAvailableError):
        tb._assert_valid_session("R", 2025, 1)


def test_assert_valid_session_accepts_quali_variants():
    tb._assert_valid_session("Q", 2025, 1)
    tb._assert_valid_session("Qualifying", 2025, 1)
    tb._assert_valid_session("SQ", 2025, 1)


def test_data_call_rejects_race():
    with pytest.raises(DataNotAvailableError):
        tb.TheoreticalBestData()(2025, 1, "R")


# ----------------------------------------------------------------------
# build_payload_from_parts: sorting / clamping / exclusion
# ----------------------------------------------------------------------
def _lap_records(*times):
    return [{"lap": i + 1, "time_s": t, "timestamp_s": 0.0} for i, t in enumerate(times)]


def test_theoretical_delta_clamped_and_sorted():
    """Two drivers: one with a clean theoretical/actual gap, one where summed
    sectors slightly exceed the actual lap (rounding noise) -> delta clamps to 0.
    """
    best_sectors = {
        "1": {"s1": 20.0, "s2": 25.0, "s3": 22.0},  # theoretical = 67.0
        "2": {"s1": 21.0, "s2": 26.0, "s3": 23.0},  # theoretical = 70.0
    }
    lap_times = {
        "1": _lap_records(68.5),  # actual 68.5 -> delta = 1.5
        "2": _lap_records(69.9),  # actual 69.9 < theoretical 70.0 -> delta clamps to 0
    }
    drivers = {
        "1": {"tla": "VER", "team": "Red Bull Racing"},
        "2": {"tla": "NOR", "team": "McLaren"},
    }

    payload = tb.build_payload_from_parts(best_sectors, lap_times, drivers)
    assert len(payload) == 2
    # Fastest theoretical (67.0) sorts first.
    assert payload[0]["driver"] == "VER"
    assert payload[0]["theoretical_s"] == pytest.approx(67.0)
    assert payload[0]["actual_s"] == pytest.approx(68.5)
    assert payload[0]["delta_s"] == pytest.approx(1.5)

    assert payload[1]["driver"] == "NOR"
    assert payload[1]["delta_s"] == 0.0


def test_driver_missing_sector_excluded():
    best_sectors = {
        "1": {"s1": 20.0, "s2": 25.0},  # missing s3
        "2": {"s1": 21.0, "s2": 26.0, "s3": 23.0},
    }
    lap_times = {
        "1": _lap_records(68.0),
        "2": _lap_records(70.0),
    }
    drivers = {"1": {"tla": "AAA", "team": "T1"}, "2": {"tla": "BBB", "team": "T2"}}

    payload = tb.build_payload_from_parts(best_sectors, lap_times, drivers)
    assert len(payload) == 1
    assert payload[0]["driver"] == "BBB"


def test_driver_missing_actual_lap_excluded():
    best_sectors = {"1": {"s1": 20.0, "s2": 25.0, "s3": 22.0}}
    lap_times = {"1": []}  # no valid completed laps
    drivers = {"1": {"tla": "AAA", "team": "T1"}}

    payload = tb.build_payload_from_parts(best_sectors, lap_times, drivers)
    assert payload == []


def test_invalid_lap_times_ignored():
    """Only positive time_s laps count toward the actual best lap."""
    best_sectors = {"1": {"s1": 20.0, "s2": 25.0, "s3": 22.0}}
    lap_times = {"1": [
        {"lap": 1, "time_s": 0.0, "timestamp_s": 0.0},
        {"lap": 2, "time_s": -5.0, "timestamp_s": 0.0},
        {"lap": 3, "time_s": 68.0, "timestamp_s": 0.0},
    ]}
    drivers = {"1": {"tla": "AAA", "team": "T1"}}

    payload = tb.build_payload_from_parts(best_sectors, lap_times, drivers)
    assert len(payload) == 1
    assert payload[0]["actual_s"] == pytest.approx(68.0)


# ----------------------------------------------------------------------
# Plot
# ----------------------------------------------------------------------
def test_plot_renders_png(tmp_path, monkeypatch):
    payload = [
        {"driver": "VER", "team": "Red Bull Racing", "color": "#3671C6",
         "theoretical_s": 67.0, "actual_s": 68.5, "delta_s": 1.5},
        {"driver": "NOR", "team": "McLaren", "color": "#FF8000",
         "theoretical_s": 70.0, "actual_s": 70.0, "delta_s": 0.0},
    ]
    monkeypatch.chdir(tmp_path)
    out = tb.TheoreticalBestPlot._render(payload, 2024, "Test GP", "Qualifying")
    assert os.path.isfile(out)
    assert out.endswith(".png")
