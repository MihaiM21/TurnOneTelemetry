"""Offline unit tests for Pit Strategy & Undercut (V2).

The undercut scenarios are built from synthetic lap-time / stint / pit dicts so
the gap-before / gap-after / gain math can be verified against hand-computed
values with no network, Redis, or Mongo.
"""
from __future__ import annotations

import os

import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.analysis.v2 import pit_strategy as ps


# ----------------------------------------------------------------------
# Synthetic builders
# ----------------------------------------------------------------------
def _lap_records(times):
    """[(lap, time_s), ...] -> lap-time records list."""
    return [{"lap": lap, "time_s": t, "timestamp_s": 0.0} for lap, t in times]


def _drivers(*nums):
    return {n: {"tla": f"D{n}", "team": f"Team{n}"} for n in nums}


# ----------------------------------------------------------------------
# Session validation
# ----------------------------------------------------------------------
def test_assert_valid_session_rejects_qualifying():
    with pytest.raises(DataNotAvailableError):
        ps._assert_valid_session("Q", 2025, 1)


def test_assert_valid_session_accepts_race_and_sprint():
    ps._assert_valid_session("R", 2025, 1)
    ps._assert_valid_session("Race", 2025, 1)
    ps._assert_valid_session("S", 2025, 1)


def test_data_call_rejects_qualifying():
    with pytest.raises(DataNotAvailableError):
        ps.PitStrategyData()(2025, 1, "Q")


# ----------------------------------------------------------------------
# Undercut math — hand-computed scenario
# ----------------------------------------------------------------------
def test_undercut_worked_hand_computed():
    """Attacker A (num 1) chases defender B (num 2), pits earlier, jumps ahead.

    Cumulative times (end of lap):
      lap 9 : A=900.0, B=898.0  -> gap_before = A - B = +2.0 (B ahead)
      A pits on lap 10 (slow out-lap), B pits on lap 12.
      lap 13: A=1181.0, B=1182.0 -> gap_after = A - B = -1.0 (A ahead)
      gain = gap_before - gap_after = 2.0 - (-1.0) = 3.0 ; worked (A ends ahead)
    """
    # A: 100/lap for laps 1..9 -> cum(9)=900; laps 10..13 sum=281 -> cum(13)=1181.
    a_times = [(lap, 100.0) for lap in range(1, 10)]
    a_times += [(10, 110.0), (11, 57.0), (12, 57.0), (13, 57.0)]

    # B: cum(9)=898 -> laps 1..9 sum 898. cum(13)=1182 -> laps10..13 sum=284.
    b_times = [(lap, 898.0 / 9) for lap in range(1, 10)]
    b_times += [(10, 100.0), (11, 100.0), (12, 27.0), (13, 57.0)]  # sum=284 -> cum13=1182

    lap_times = {"1": _lap_records(a_times), "2": _lap_records(b_times)}
    drivers = _drivers("1", "2")

    stops = [
        {"driver": "D1", "num": "1", "team": "Team1", "lap": 10, "stop_n": 1,
         "pit_lane_time_s": 22.0, "compound_in": "MEDIUM", "compound_out": "HARD",
         "under_sc": False, "drive_through": False},
        {"driver": "D2", "num": "2", "team": "Team2", "lap": 12, "stop_n": 1,
         "pit_lane_time_s": 23.0, "compound_in": "MEDIUM", "compound_out": "HARD",
         "under_sc": False, "drive_through": False},
    ]

    undercuts = ps._build_undercuts(stops, lap_times, drivers)
    assert len(undercuts) == 1
    uc = undercuts[0]
    assert uc["attacker"] == "D1"
    assert uc["defender"] == "D2"
    assert uc["lap"] == 10
    assert uc["gap_before_s"] == pytest.approx(2.0, abs=1e-6)
    assert uc["gap_after_s"] == pytest.approx(-1.0, abs=1e-6)
    assert uc["gain_s"] == pytest.approx(3.0, abs=1e-6)
    assert uc["worked"] is True


def test_undercut_excluded_when_under_sc():
    """Same geometry, but the attacker's stop is under SC -> no undercut pair."""
    a_times = [(lap, 100.0) for lap in range(1, 10)]
    a_times += [(10, 110.0), (11, 57.0), (12, 57.0), (13, 57.0)]
    b_times = [(lap, 898.0 / 9) for lap in range(1, 10)]
    b_times += [(10, 100.0), (11, 100.0), (12, 27.0), (13, 57.0)]
    lap_times = {"1": _lap_records(a_times), "2": _lap_records(b_times)}
    drivers = _drivers("1", "2")

    stops = [
        {"driver": "D1", "num": "1", "team": "Team1", "lap": 10, "stop_n": 1,
         "pit_lane_time_s": 22.0, "compound_in": "MEDIUM", "compound_out": "HARD",
         "under_sc": True, "drive_through": False},
        {"driver": "D2", "num": "2", "team": "Team2", "lap": 12, "stop_n": 1,
         "pit_lane_time_s": 23.0, "compound_in": "MEDIUM", "compound_out": "HARD",
         "under_sc": False, "drive_through": False},
    ]

    assert ps._build_undercuts(stops, lap_times, drivers) == []


def test_undercut_outside_window_excluded():
    """Defender pits 6 laps later (outside the 1-5 window) -> no pair."""
    a_times = [(lap, 100.0) for lap in range(1, 20)]
    b_times = [(lap, 99.8) for lap in range(1, 20)]
    lap_times = {"1": _lap_records(a_times), "2": _lap_records(b_times)}
    drivers = _drivers("1", "2")
    stops = [
        {"driver": "D1", "num": "1", "team": "Team1", "lap": 10, "stop_n": 1,
         "pit_lane_time_s": 22.0, "compound_in": "M", "compound_out": "H",
         "under_sc": False, "drive_through": False},
        {"driver": "D2", "num": "2", "team": "Team2", "lap": 16, "stop_n": 1,
         "pit_lane_time_s": 23.0, "compound_in": "M", "compound_out": "H",
         "under_sc": False, "drive_through": False},
    ]
    assert ps._build_undercuts(stops, lap_times, drivers) == []


# ----------------------------------------------------------------------
# Stops / drive-through / compounds
# ----------------------------------------------------------------------
def test_drive_through_detection():
    """Pit increment with same compound in/out -> drive_through, no fitted change."""
    pit_stops = {"1": [{"lap": 15, "pit_lane_time_s": 8.0, "stop_n": 1}]}
    stints = {"1": [
        {"stint_number": 1, "compound": "HARD", "start_lap": 1, "end_lap": 15,
         "lap_count": 15, "tyre_life_end": 15},
        {"stint_number": 2, "compound": "HARD", "start_lap": 16, "end_lap": 40,
         "lap_count": 25, "tyre_life_end": 25},
    ]}
    drivers = _drivers("1")
    payload = ps.build_payload_from_parts(pit_stops, stints, {}, drivers, [])
    assert len(payload["stops"]) == 1
    assert payload["stops"][0]["drive_through"] is True
    # Drive-through excluded from fastest-stop stats.
    assert payload["summary"]["fastest_stop"] is None


def test_normal_stop_compounds_and_summary():
    pit_stops = {
        "1": [{"lap": 18, "pit_lane_time_s": 22.5, "stop_n": 1}],
        "2": [{"lap": 20, "pit_lane_time_s": 21.0, "stop_n": 1}],
    }
    stints = {
        "1": [
            {"stint_number": 1, "compound": "SOFT", "start_lap": 1, "end_lap": 18,
             "lap_count": 18, "tyre_life_end": 18},
            {"stint_number": 2, "compound": "HARD", "start_lap": 19, "end_lap": 50,
             "lap_count": 31, "tyre_life_end": 31},
        ],
        "2": [
            {"stint_number": 1, "compound": "SOFT", "start_lap": 1, "end_lap": 20,
             "lap_count": 20, "tyre_life_end": 20},
            {"stint_number": 2, "compound": "MEDIUM", "start_lap": 21, "end_lap": 50,
             "lap_count": 29, "tyre_life_end": 29},
        ],
    }
    drivers = {"1": {"tla": "AAA", "team": "Alpha"},
               "2": {"tla": "BBB", "team": "Beta"}}
    payload = ps.build_payload_from_parts(pit_stops, stints, {}, drivers, [])

    s1 = next(s for s in payload["stops"] if s["driver"] == "AAA")
    assert s1["compound_in"] == "SOFT"
    assert s1["compound_out"] == "HARD"
    assert s1["drive_through"] is False

    fastest = payload["summary"]["fastest_stop"]
    assert fastest["driver"] == "BBB"
    assert fastest["pit_lane_time_s"] == 21.0
    assert len(payload["summary"]["avg_stop_by_team"]) == 2


def test_under_sc_flag_from_periods():
    pit_stops = {"1": [{"lap": 25, "pit_lane_time_s": 15.0, "stop_n": 1}]}
    stints = {"1": [
        {"stint_number": 1, "compound": "MEDIUM", "start_lap": 1, "end_lap": 25,
         "lap_count": 25, "tyre_life_end": 25},
        {"stint_number": 2, "compound": "HARD", "start_lap": 26, "end_lap": 50,
         "lap_count": 25, "tyre_life_end": 25},
    ]}
    periods = [{"status": "SC", "start_lap": 24, "end_lap": 27}]
    drivers = _drivers("1")
    payload = ps.build_payload_from_parts(pit_stops, stints, {}, drivers, periods)
    assert payload["stops"][0]["under_sc"] is True


def test_free_change_red_flag():
    """Compound change with no pit stop near the boundary -> free_changes entry."""
    pit_stops = {"1": []}
    stints = {"1": [
        {"stint_number": 1, "compound": "MEDIUM", "start_lap": 1, "end_lap": 30,
         "lap_count": 30, "tyre_life_end": 30},
        {"stint_number": 2, "compound": "SOFT", "start_lap": 31, "end_lap": 50,
         "lap_count": 20, "tyre_life_end": 20},
    ]}
    drivers = _drivers("1")
    payload = ps.build_payload_from_parts(pit_stops, stints, {}, drivers, [])
    assert len(payload["free_changes"]) == 1
    fc = payload["free_changes"][0]
    assert fc["compound_in"] == "MEDIUM"
    assert fc["compound_out"] == "SOFT"
    assert fc["lap"] == 30


# ----------------------------------------------------------------------
# Plot
# ----------------------------------------------------------------------
def test_plot_renders_png(tmp_path, monkeypatch):
    payload = {
        "stops": [
            {"driver": "AAA", "team": "Alpha", "lap": 18, "stop_n": 1,
             "pit_lane_time_s": 22.5, "compound_in": "SOFT", "compound_out": "HARD",
             "under_sc": False, "drive_through": False},
            {"driver": "BBB", "team": "Beta", "lap": 20, "stop_n": 1,
             "pit_lane_time_s": 21.0, "compound_in": "SOFT", "compound_out": "MEDIUM",
             "under_sc": True, "drive_through": False},
        ],
        "undercuts": [
            {"attacker": "AAA", "defender": "BBB", "lap": 18,
             "gap_before_s": 2.0, "gap_after_s": -1.0, "gain_s": 3.0, "worked": True},
        ],
        "summary": {"fastest_stop": None, "avg_stop_by_team": []},
        "free_changes": [],
    }
    periods = [{"status": "SC", "start_lap": 19, "end_lap": 21}]

    monkeypatch.chdir(tmp_path)
    out = ps.PitStrategyPlot._render(payload, periods, 2025, "Australian Grand Prix", "Race")
    assert os.path.isfile(out)
    assert out.endswith(".png")


def test_plot_renders_png_no_undercuts(tmp_path, monkeypatch):
    payload = {
        "stops": [
            {"driver": "AAA", "team": "Alpha", "lap": 18, "stop_n": 1,
             "pit_lane_time_s": 22.5, "compound_in": "SOFT", "compound_out": "HARD",
             "under_sc": False, "drive_through": False},
        ],
        "undercuts": [],
        "summary": {"fastest_stop": None, "avg_stop_by_team": []},
        "free_changes": [],
    }
    monkeypatch.chdir(tmp_path)
    out = ps.PitStrategyPlot._render(payload, [], 2025, "Test GP", "Race")
    assert os.path.isfile(out)
