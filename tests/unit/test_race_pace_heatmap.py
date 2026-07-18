"""Offline unit tests for Race Pace Heatmap (V2).

Synthetic lap-time / position / track-status dicts so the median-delta grid,
SC masking, and finish-order rows can be verified with no network, Redis, or
Mongo.
"""
from __future__ import annotations

import os

import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.analysis.v2 import race_pace_heatmap as rph


def _lap_records(times, pit_in_laps=(), pit_out_laps=()):
    """[(lap, time_s), ...] -> lap-time records list."""
    return [
        {
            "lap": lap, "time_s": t, "timestamp_s": float(lap),
            "pit_in": lap in pit_in_laps, "pit_out": lap in pit_out_laps,
        }
        for lap, t in times
    ]


def _drivers(*nums):
    return {n: {"tla": f"D{n}", "team": f"Team{n}"} for n in nums}


def _positions(num_to_final_pos):
    return {num: [{"lap": 1, "position": pos}] for num, pos in num_to_final_pos.items()}


# ----------------------------------------------------------------------
# Session validation
# ----------------------------------------------------------------------
def test_assert_valid_session_rejects_qualifying():
    with pytest.raises(DataNotAvailableError):
        rph._assert_valid_session("Q", 2025, 1)


def test_assert_valid_session_accepts_race_and_sprint():
    rph._assert_valid_session("R", 2025, 1)
    rph._assert_valid_session("Race", 2025, 1)
    rph._assert_valid_session("S", 2025, 1)


def test_data_call_rejects_qualifying():
    with pytest.raises(DataNotAvailableError):
        rph.RacePaceHeatmapData()(2025, 1, "Q")


# ----------------------------------------------------------------------
# Median excludes pit laps
# ----------------------------------------------------------------------
def test_median_excludes_pit_laps():
    """Driver 2's pit-in lap of 130s must not drag the field median."""
    lap_times = {
        "1": _lap_records([(1, 100.0), (2, 100.0)]),
        "2": _lap_records([(1, 100.0), (2, 130.0)], pit_in_laps={2}),
        "3": _lap_records([(1, 100.0), (2, 101.0)]),
    }
    periods = []
    drivers = _drivers("1", "2", "3")
    positions = _positions({"1": 1, "2": 2, "3": 3})

    payload = rph.build_payload_from_parts(lap_times, positions, periods, drivers)
    median_lap2 = rph._field_median_by_lap(lap_times)[2]
    # Only D1 (100.0) and D3 (101.0) count -> median 100.5, D2's pit lap excluded.
    assert median_lap2 == pytest.approx(100.5)

    # D2's grid cell for lap 2 is masked (None) because it's a pit lap.
    lap_idx = payload["laps"].index(2)
    assert payload["grid"]["D2"][lap_idx] is None


# ----------------------------------------------------------------------
# SC/VSC masking
# ----------------------------------------------------------------------
def test_sc_masking_hides_cells_and_excludes_from_median():
    lap_times = {
        "1": _lap_records([(1, 100.0), (2, 140.0), (3, 100.0)]),
        "2": _lap_records([(1, 100.0), (2, 141.0), (3, 101.0)]),
    }
    periods = [{"status": "SC", "start_lap": 2, "end_lap": 2}]
    drivers = _drivers("1", "2")
    positions = _positions({"1": 1, "2": 2})

    payload = rph.build_payload_from_parts(lap_times, positions, periods, drivers)
    assert payload["sc_laps"] == [2]

    lap_idx = payload["laps"].index(2)
    assert payload["grid"]["D1"][lap_idx] is None
    assert payload["grid"]["D2"][lap_idx] is None
    # Non-SC laps still have deltas.
    lap_idx3 = payload["laps"].index(3)
    assert payload["grid"]["D1"][lap_idx3] is not None


def test_sc_period_with_open_end_masks_to_max_lap():
    lap_times = {
        "1": _lap_records([(1, 100.0), (2, 100.0), (3, 100.0)]),
    }
    periods = [{"status": "VSC", "start_lap": 2, "end_lap": None}]
    drivers = _drivers("1")
    positions = _positions({"1": 1})

    payload = rph.build_payload_from_parts(lap_times, positions, periods, drivers)
    assert payload["sc_laps"] == [2, 3]


# ----------------------------------------------------------------------
# Finish ordering
# ----------------------------------------------------------------------
def test_finish_ordering_winner_first():
    lap_times = {
        "1": _lap_records([(1, 100.0)]),
        "2": _lap_records([(1, 100.0)]),
        "3": _lap_records([(1, 100.0)]),
    }
    periods = []
    drivers = _drivers("1", "2", "3")
    # D3 finishes P1, D1 finishes P2, D2 finishes P3.
    positions = _positions({"1": 2, "2": 3, "3": 1})

    payload = rph.build_payload_from_parts(lap_times, positions, periods, drivers)
    assert payload["drivers"] == ["D3", "D1", "D2"]


def test_retiree_with_no_position_data_pushed_to_bottom():
    lap_times = {
        "1": _lap_records([(1, 100.0)]),
        "2": _lap_records([(1, 100.0)]),
    }
    periods = []
    drivers = _drivers("1", "2")
    # D2 has no position records at all (retired before any position update).
    positions = _positions({"1": 1})

    payload = rph.build_payload_from_parts(lap_times, positions, periods, drivers)
    assert payload["drivers"][-1] == "D2"
    assert payload["drivers"][0] == "D1"


# ----------------------------------------------------------------------
# Pit laps surfaced for plotting
# ----------------------------------------------------------------------
def test_pit_laps_surfaced_by_tla():
    lap_times = {
        "1": _lap_records([(1, 100.0), (2, 130.0), (3, 95.0)], pit_in_laps={2}, pit_out_laps={3}),
    }
    periods = []
    drivers = _drivers("1")
    positions = _positions({"1": 1})

    payload = rph.build_payload_from_parts(lap_times, positions, periods, drivers)
    assert payload["pit_laps"]["D1"] == [2, 3]


# ----------------------------------------------------------------------
# Plot
# ----------------------------------------------------------------------
def test_plot_renders_png(tmp_path, monkeypatch):
    lap_times = {
        "1": _lap_records([(1, 100.0), (2, 100.5)]),
        "2": _lap_records([(1, 101.0), (2, 130.0)], pit_in_laps={2}),
    }
    periods = [{"status": "SC", "start_lap": 2, "end_lap": 2}]
    drivers = _drivers("1", "2")
    positions = _positions({"1": 1, "2": 2})

    payload = rph.build_payload_from_parts(lap_times, positions, periods, drivers)

    monkeypatch.chdir(tmp_path)
    out = rph.RacePaceHeatmapPlot._render(payload, 2025, "Test GP", "Race")
    assert os.path.isfile(out)
    assert out.endswith(".png")
