"""Offline unit tests for Race Story Timeline (V2).

Synthetic dicts exercise lead-change detection, retirement truncation, key
moment merging/capping, and the leader-at-zero / red-flag-None gap math, with
no network, Redis, or Mongo dependency.
"""
from __future__ import annotations

import os

import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.analysis.v2 import race_story as rs
from src.services.analysis.v2._race_helpers import cumtime_by_lap


# ----------------------------------------------------------------------
# Session validation
# ----------------------------------------------------------------------
def test_assert_valid_session_rejects_qualifying():
    with pytest.raises(DataNotAvailableError):
        rs._assert_valid_session("Q", 2025, 1)


def test_assert_valid_session_accepts_race_and_sprint():
    rs._assert_valid_session("R", 2025, 1)
    rs._assert_valid_session("Race", 2025, 1)
    rs._assert_valid_session("S", 2025, 1)


def test_data_call_rejects_qualifying():
    with pytest.raises(DataNotAvailableError):
        rs.RaceStoryData()(2025, 1, "Q")


# ----------------------------------------------------------------------
# _build_gap_series: leader-at-zero + red-flag None
# ----------------------------------------------------------------------
def test_build_gap_series_leader_at_zero():
    lap_times = {
        "1": [{"lap": 1, "time_s": 90.0, "timestamp_s": 0.0},
              {"lap": 2, "time_s": 90.0, "timestamp_s": 0.0}],
        "2": [{"lap": 1, "time_s": 91.0, "timestamp_s": 0.0},
              {"lap": 2, "time_s": 91.0, "timestamp_s": 0.0}],
    }
    cum = cumtime_by_lap(lap_times)
    series = rs._build_gap_series(cum, periods=[])
    assert series["1"][0]["gap_s"] == 0.0
    assert series["1"][1]["gap_s"] == 0.0
    assert series["2"][0]["gap_s"] == pytest.approx(1.0)
    assert series["2"][1]["gap_s"] == pytest.approx(2.0)


def test_build_gap_series_red_flag_lap_is_none():
    lap_times = {
        "1": [{"lap": 1, "time_s": 90.0, "timestamp_s": 0.0},
              {"lap": 2, "time_s": 90.0, "timestamp_s": 0.0},
              {"lap": 3, "time_s": 90.0, "timestamp_s": 0.0}],
        "2": [{"lap": 1, "time_s": 92.0, "timestamp_s": 0.0},
              {"lap": 2, "time_s": 92.0, "timestamp_s": 0.0},
              {"lap": 3, "time_s": 92.0, "timestamp_s": 0.0}],
    }
    cum = cumtime_by_lap(lap_times)
    periods = [{"status": "RED", "start_lap": 2, "end_lap": 2}]
    series = rs._build_gap_series(cum, periods)
    assert series["1"][1]["gap_s"] is None
    assert series["1"][0]["gap_s"] is not None
    assert series["1"][2]["gap_s"] is not None


# ----------------------------------------------------------------------
# _detect_lead_changes
# ----------------------------------------------------------------------
def test_detect_lead_changes_on_synthetic_swap():
    positions = {
        "1": [{"lap": 1, "position": 1}, {"lap": 2, "position": 1}, {"lap": 3, "position": 2}],
        "2": [{"lap": 1, "position": 2}, {"lap": 2, "position": 2}, {"lap": 3, "position": 1}],
    }
    changes = rs._detect_lead_changes(positions)
    assert len(changes) == 1
    assert changes[0]["lap"] == 3
    assert changes[0]["new_leader_num"] == "2"
    assert changes[0]["prev_leader_num"] == "1"


def test_detect_lead_changes_no_swap_when_stable():
    positions = {
        "1": [{"lap": 1, "position": 1}, {"lap": 2, "position": 1}],
        "2": [{"lap": 1, "position": 2}, {"lap": 2, "position": 2}],
    }
    assert rs._detect_lead_changes(positions) == []


# ----------------------------------------------------------------------
# _detect_retirements
# ----------------------------------------------------------------------
def test_detect_retirements_truncated_series():
    lap_times = {
        "1": [{"lap": i, "time_s": 90.0, "timestamp_s": 0.0} for i in range(1, 51)],
        "2": [{"lap": i, "time_s": 90.0, "timestamp_s": 0.0} for i in range(1, 21)],  # retires L20
    }
    retirements = rs._detect_retirements(lap_times)
    assert len(retirements) == 1
    assert retirements[0]["num"] == "2"
    assert retirements[0]["last_lap"] == 20


def test_detect_retirements_lapped_car_not_flagged():
    """A car finishing just 1-2 laps down (lapped) is not a retirement."""
    lap_times = {
        "1": [{"lap": i, "time_s": 90.0, "timestamp_s": 0.0} for i in range(1, 51)],
        "2": [{"lap": i, "time_s": 90.0, "timestamp_s": 0.0} for i in range(1, 50)],  # 1 lap down
    }
    assert rs._detect_retirements(lap_times) == []


# ----------------------------------------------------------------------
# _build_key_moments: merging + penalty filter + cap
# ----------------------------------------------------------------------
def test_build_key_moments_merges_and_captions():
    lead_changes = [{"lap": 23, "new_leader_num": "1", "prev_leader_num": "4"}]
    retirements = [{"num": "44", "last_lap": 30}]
    race_control_events = [
        {"lap": 12, "category": "Other", "message": "5 SECOND TIME PENALTY FOR CAR 16", "flag": None,
         "racing_number": "16"},
        {"lap": 15, "category": "Flag", "message": "YELLOW IN TRACK SECTOR 4", "flag": "YELLOW",
         "racing_number": None},
    ]
    tla_by_num = {"1": "VER", "4": "NOR", "44": "HAM"}

    moments = rs._build_key_moments(lead_changes, retirements, race_control_events, tla_by_num)
    kinds = {m["kind"] for m in moments}
    assert kinds == {"lead_change", "retirement", "penalty"}
    lead = next(m for m in moments if m["kind"] == "lead_change")
    assert "VER passes NOR for the lead" in lead["caption"]
    ret = next(m for m in moments if m["kind"] == "retirement")
    assert "HAM retires" in ret["caption"]
    # Non-penalty race control message excluded.
    assert not any("YELLOW" in m["caption"] for m in moments)
    # Chronological order and numbering.
    laps = [m["lap"] for m in moments]
    assert laps == sorted(laps)
    assert [m["n"] for m in moments] == list(range(1, len(moments) + 1))


def test_build_key_moments_capped_at_max():
    lead_changes = [
        {"lap": i, "new_leader_num": "1", "prev_leader_num": "2"} for i in range(1, 15)
    ]
    tla_by_num = {"1": "VER", "2": "NOR"}
    moments = rs._build_key_moments(lead_changes, [], [], tla_by_num)
    assert len(moments) == rs._MAX_KEY_MOMENTS


# ----------------------------------------------------------------------
# build_payload_from_parts
# ----------------------------------------------------------------------
def test_build_payload_from_parts_orders_by_finish():
    lap_times = {
        "1": [{"lap": 1, "time_s": 90.0, "timestamp_s": 0.0}],
        "2": [{"lap": 1, "time_s": 91.0, "timestamp_s": 0.0}],
    }
    positions = {
        "1": [{"lap": 1, "position": 1}],
        "2": [{"lap": 1, "position": 2}],
    }
    drivers = {
        "1": {"tla": "VER", "team": "Red Bull Racing"},
        "2": {"tla": "NOR", "team": "McLaren"},
    }
    payload = rs.build_payload_from_parts(lap_times, positions, {}, {}, drivers, [], [])
    assert [d["driver"] for d in payload["drivers"]] == ["VER", "NOR"]
    assert payload["drivers"][0]["laps"][0]["gap_s"] == 0.0


def test_build_payload_from_parts_empty_lap_times():
    payload = rs.build_payload_from_parts({}, {}, {}, {}, {}, [], [])
    assert payload["drivers"] == []
    assert payload["key_moments"] == []


# ----------------------------------------------------------------------
# Plot
# ----------------------------------------------------------------------
def test_plot_renders_png(tmp_path, monkeypatch):
    payload = {
        "drivers": [
            {"driver": "VER", "team": "Red Bull Racing", "color": "#3671C6", "finish_rank": 1,
             "laps": [{"lap": 1, "gap_s": 0.0}, {"lap": 2, "gap_s": 0.0}],
             "pit_stops": [{"lap": 1, "compound": "HARD"}], "last_lap": 2},
            {"driver": "NOR", "team": "McLaren", "color": "#FF8000", "finish_rank": 2,
             "laps": [{"lap": 1, "gap_s": 1.0}, {"lap": 2, "gap_s": 2.0}],
             "pit_stops": [], "last_lap": 2},
        ],
        "key_moments": [
            {"lap": 1, "kind": "lead_change", "caption": "L1 VER passes NOR for the lead", "n": 1},
        ],
        "track_status_periods": [{"status": "SC", "start_lap": 1, "end_lap": 2}],
    }
    monkeypatch.chdir(tmp_path)
    out = rs.RaceStoryPlot._render(payload, 2025, "Test GP", "Race")
    assert os.path.isfile(out)
    assert out.endswith(".png")
