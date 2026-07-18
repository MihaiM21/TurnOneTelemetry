"""Offline unit tests for the derived race helpers.

These use a minimal fake store that returns captured fixtures directly, so the
parsing logic is exercised against real 2025 Australian GP stream shapes with
no network and no Redis.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.analysis.v2 import _race_helpers as rh
from src.services.analysis.v2.session_store import SessionDataStore

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeStore:
    """Returns parsed fixtures for the stream accessors the helpers use.

    The public ``extract_*`` helpers delegate to the store's durable derived
    accessors (``lap_times()`` etc.); this stub reproduces that seam by routing
    them straight to the ``_compute_*`` functions, so the parsing logic is
    exercised end-to-end through the same interface production uses — just
    without the MongoDB tier.
    """

    def __init__(self):
        raw = (FIXTURES / "timing_data_sample.jsonStream").read_bytes()
        self._timing = SessionDataStore._parse_stream_content(raw)
        self._track = json.loads((FIXTURES / "track_status_sample.json").read_text())

    def timing_data(self):
        return self._timing

    def track_status(self):
        return self._track

    # Derived accessors — mirror SessionDataStore's cached layer (no Mongo).
    def lap_times(self):
        return rh._compute_lap_times(self)

    def positions_by_lap(self):
        return rh._compute_positions_by_lap(self)

    def pit_stops(self):
        return rh._compute_pit_stops(self)

    def best_sectors(self):
        return rh._compute_best_sectors(self)

    def track_status_periods(self):
        return rh._compute_track_status_periods(self)


@pytest.fixture
def store():
    return _FakeStore()


def test_extract_lap_times_per_driver(store):
    laps = rh.extract_lap_times(store)
    assert set(laps.keys()) == {"1", "4"}
    d4 = laps["4"]
    # Laps are sorted ascending and carry a positive time.
    assert d4[0]["lap"] < d4[-1]["lap"]
    assert all(r["time_s"] > 0 for r in d4)
    # A representative green-flag lap time is present.
    assert any(85.0 < r["time_s"] < 95.0 for r in d4)


def test_lap_times_flag_pit_in_and_out(store):
    laps = rh.extract_lap_times(store)
    d4 = laps["4"]
    assert any(r["pit_in"] for r in d4)
    assert any(r["pit_out"] for r in d4)


def test_extract_positions_by_lap(store):
    pos = rh.extract_positions_by_lap(store)
    assert "4" in pos
    # Winner finishes P1.
    assert pos["4"][-1]["position"] == 1
    # Positions are integers, laps non-decreasing.
    laps_seq = [p["lap"] for p in pos["4"]]
    assert laps_seq == sorted(laps_seq)


def test_extract_pit_stops(store):
    pits = rh.extract_pit_stops(store)
    assert "4" in pits
    stops = pits["4"]
    assert len(stops) >= 1
    # Stop numbers increase and pit-lane times are plausible.
    assert [s["stop_n"] for s in stops] == sorted(s["stop_n"] for s in stops)
    timed = [s for s in stops if s["pit_lane_time_s"] is not None]
    assert timed and all(5.0 < s["pit_lane_time_s"] < 60.0 for s in timed)
    # No leftover private keys leak out.
    assert all("_in_ts" not in s for s in stops)


def test_get_track_status_periods_detects_sc(store):
    periods = rh.get_track_status_periods(store)
    statuses = {p["status"] for p in periods}
    assert "SC" in statuses
    assert "GREEN" in statuses
    sc = [p for p in periods if p["status"] == "SC"]
    for p in sc:
        assert p["start_lap"] >= 1
        # end_lap is None (ran to session end) or after start.
        assert p["end_lap"] is None or p["end_lap"] >= p["start_lap"]


def test_track_status_collapses_consecutive_duplicates(store):
    periods = rh.get_track_status_periods(store)
    for a, b in zip(periods, periods[1:]):
        assert a["status"] != b["status"]


def test_last_period_open_ended(store):
    periods = rh.get_track_status_periods(store)
    assert periods[-1]["end_time_s"] is None
    assert periods[-1]["end_lap"] is None


# ----------------------------------------------------------------------
# cumtime_by_lap
# ----------------------------------------------------------------------
def test_cumtime_by_lap_accumulates_in_order():
    lap_times = {
        "1": [
            {"lap": 1, "time_s": 90.0, "timestamp_s": 0.0},
            {"lap": 2, "time_s": 91.5, "timestamp_s": 0.0},
            {"lap": 3, "time_s": 89.0, "timestamp_s": 0.0},
        ]
    }
    cum = rh.cumtime_by_lap(lap_times)
    assert cum["1"][1] == pytest.approx(90.0)
    assert cum["1"][2] == pytest.approx(181.5)
    assert cum["1"][3] == pytest.approx(270.5)


def test_cumtime_by_lap_skips_invalid_times():
    lap_times = {
        "1": [
            {"lap": 1, "time_s": 90.0, "timestamp_s": 0.0},
            {"lap": 2, "time_s": 0.0, "timestamp_s": 0.0},
            {"lap": 3, "time_s": -1.0, "timestamp_s": 0.0},
            {"lap": 4, "time_s": 88.0, "timestamp_s": 0.0},
        ]
    }
    cum = rh.cumtime_by_lap(lap_times)
    assert 2 not in cum["1"]
    assert 3 not in cum["1"]
    assert cum["1"][4] == pytest.approx(178.0)


# ----------------------------------------------------------------------
# best_sectors_from_entries: list-snapshot + dict-incremental + empty Value
# ----------------------------------------------------------------------
def test_best_sectors_from_list_snapshot():
    entries = [
        {"Lines": {"1": {"Sectors": [
            {"Value": "28.123"}, {"Value": "31.456"}, {"Value": "25.789"},
        ]}}},
    ]
    best = rh.best_sectors_from_entries(entries)
    assert best["1"]["s1"] == pytest.approx(28.123)
    assert best["1"]["s2"] == pytest.approx(31.456)
    assert best["1"]["s3"] == pytest.approx(25.789)


def test_best_sectors_from_dict_incremental_updates():
    entries = [
        {"Lines": {"1": {"Sectors": {"0": {"Value": "28.5"}}}}},
        {"Lines": {"1": {"Sectors": {"1": {"Value": "31.0"}}}}},
        {"Lines": {"1": {"Sectors": {"2": {"Value": "26.0"}}}}},
        # A faster sector 1 arrives later -> minimum is kept.
        {"Lines": {"1": {"Sectors": {"0": {"Value": "27.9"}}}}},
    ]
    best = rh.best_sectors_from_entries(entries)
    assert best["1"]["s1"] == pytest.approx(27.9)
    assert best["1"]["s2"] == pytest.approx(31.0)
    assert best["1"]["s3"] == pytest.approx(26.0)


def test_best_sectors_skips_empty_value():
    entries = [
        {"Lines": {"1": {"Sectors": {"0": {"Value": ""}}}}},
        {"Lines": {"1": {"Sectors": {"0": {"Value": "28.0"}}}}},
    ]
    best = rh.best_sectors_from_entries(entries)
    assert best["1"]["s1"] == pytest.approx(28.0)


def test_best_sectors_ignores_non_dict_lines():
    entries = [{"Lines": {"1": "not-a-dict"}}, {"NotLines": True}]
    assert rh.best_sectors_from_entries(entries) == {}


# ----------------------------------------------------------------------
# extract_race_control_events: Lap field vs Utc fallback
# ----------------------------------------------------------------------
class _FakeRaceControlStore:
    """Minimal store stub exposing only what extract_race_control_events needs."""

    def __init__(self, messages, session_info=None, leader_ts=None):
        self._messages = messages
        self._session_info = session_info or {}
        self._leader_ts = leader_ts or []

    def race_control(self):
        return self._messages

    def session_info(self):
        return self._session_info


def test_race_control_uses_message_lap_field():
    store = _FakeRaceControlStore(
        messages=[{"Utc": "2025-03-16T03:41:43", "Lap": 12, "Category": "Flag",
                   "Flag": "YELLOW", "Message": "YELLOW IN TRACK SECTOR 10"}],
    )
    events = rh.extract_race_control_events(store, leader_ts=[])
    assert events[0]["lap"] == 12
    assert events[0]["category"] == "Flag"
    assert events[0]["flag"] == "YELLOW"
    assert events[0]["message"] == "YELLOW IN TRACK SECTOR 10"


def test_race_control_falls_back_to_utc_mapping():
    # Session starts (local) at 15:00:00+11:00 -> UTC 04:00:00.
    session_info = {"StartDate": "2025-03-16T15:00:00", "GmtOffset": "11:00:00"}
    # Leader completes lap 1 at t=90s, lap 2 at t=180s (session-relative).
    leader_ts = [90.0, 180.0]
    # Message arrives at UTC 04:02:00 -> 120s into the session -> lap 2 in progress.
    messages = [{"Utc": "2025-03-16T04:02:00", "Category": "Other", "Message": "TRACK CLEAR"}]
    store = _FakeRaceControlStore(messages=messages, session_info=session_info)
    events = rh.extract_race_control_events(store, leader_ts=leader_ts)
    assert events[0]["lap"] == 2


def test_race_control_lap_none_when_unresolvable():
    store = _FakeRaceControlStore(
        messages=[{"Category": "Other", "Message": "NO LAP OR UTC"}],
    )
    events = rh.extract_race_control_events(store, leader_ts=[])
    assert events[0]["lap"] is None


def test_race_control_sorted_lap_none_last():
    store = _FakeRaceControlStore(messages=[
        {"Lap": 5, "Message": "B"},
        {"Message": "no-lap"},
        {"Lap": 1, "Message": "A"},
    ])
    events = rh.extract_race_control_events(store, leader_ts=[])
    laps = [e["lap"] for e in events]
    assert laps == [1, 5, None]
