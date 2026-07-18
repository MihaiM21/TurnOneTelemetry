"""Offline unit tests for Race Gaps / Race Trace (V2).

Two flavours of fake store:

* ``_SyntheticStore`` — a hand-built lap-time set with known expected gaps, so
  the leader-gap and race-trace math can be asserted exactly.
* ``_FakeStore`` — the captured 2025 Australian GP fixtures (same pattern as
  ``test_position_changes.py``) for payload shape, driver filter, session
  rejection and PNG rendering.

No network, no Redis/Mongo (``cached_or_generate``'s Mongo read is monkeypatched
to ``None`` so the generator always runs).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.analysis.v2 import race_gaps as rg
from src.services.analysis.v2.session_store import SessionDataStore

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Synthetic store with hand-computed expected gaps
# ---------------------------------------------------------------------------
class _SyntheticStore:
    """Three drivers, three laps, known lap times.

    Cumulative times (seconds):
        A (num 1, McLaren):   90, 180, 270   -> avg 90.0
        B (num 2, McLaren):   91, 183, 276   -> avg 92.0
        C (num 3, Ferrari):   92, 186, 282   -> avg 94.0

    Leader per lap is A (smallest cum every lap).
    Winner (smallest final cum) is A, so reference pace = 90.0.
    """

    year = 2025
    identifier = 1
    session_name = "Race"
    event_name = "Synthetic GP"

    _LAP_TIMES = {
        "1": [
            {"lap": 1, "time_s": 90.0, "timestamp_s": 90.0, "pit_in": False, "pit_out": False},
            {"lap": 2, "time_s": 90.0, "timestamp_s": 180.0, "pit_in": False, "pit_out": False},
            {"lap": 3, "time_s": 90.0, "timestamp_s": 270.0, "pit_in": False, "pit_out": False},
        ],
        "2": [
            {"lap": 1, "time_s": 91.0, "timestamp_s": 91.0, "pit_in": False, "pit_out": False},
            {"lap": 2, "time_s": 92.0, "timestamp_s": 183.0, "pit_in": False, "pit_out": False},
            {"lap": 3, "time_s": 93.0, "timestamp_s": 276.0, "pit_in": False, "pit_out": False},
        ],
        "3": [
            {"lap": 1, "time_s": 92.0, "timestamp_s": 92.0, "pit_in": False, "pit_out": False},
            {"lap": 2, "time_s": 94.0, "timestamp_s": 186.0, "pit_in": False, "pit_out": False},
            {"lap": 3, "time_s": 96.0, "timestamp_s": 282.0, "pit_in": False, "pit_out": False},
        ],
    }

    _DRIVERS = {
        "1": {"tla": "AAA", "team": "McLaren", "color": "FF8000"},
        "2": {"tla": "BBB", "team": "McLaren", "color": "FF8000"},
        "3": {"tla": "CCC", "team": "Ferrari", "color": "E80020"},
    }

    def __init__(self, track_status=None):
        # No red flags by default -> get_track_status_periods returns [].
        self._track = track_status if track_status is not None else []

    def timing_data(self):
        return []

    def track_status(self):
        return self._track

    def driver_list(self):
        return {k: dict(v) for k, v in self._DRIVERS.items()}


def _lap_times(store):
    return _SyntheticStore._LAP_TIMES


@pytest.fixture(autouse=True)
def _patch_lap_times(monkeypatch):
    """For synthetic tests, feed known lap times regardless of timing_data()."""
    monkeypatch.setattr(rg, "extract_lap_times", _lap_times)


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setattr(
        "src.services.analysis.base.get_plot_data_from_mongo",
        lambda *a, **k: None,
    )


# ---------------------------------------------------------------------------
# Leader-gap math
# ---------------------------------------------------------------------------
def test_leader_gap_math():
    store = _SyntheticStore()
    payload = rg._build_payload(store, "leader", drivers_filter=None)
    by = {d["driver"]: d for d in payload}

    # Leader A is at 0 every lap.
    assert [p["gap_s"] for p in by["AAA"]["laps"]] == [0.0, 0.0, 0.0]
    # B: cum 91,183,276 minus leader 90,180,270 -> 1,3,6
    assert [p["gap_s"] for p in by["BBB"]["laps"]] == [1.0, 3.0, 6.0]
    # C: cum 92,186,282 minus 90,180,270 -> 2,6,12
    assert [p["gap_s"] for p in by["CCC"]["laps"]] == [2.0, 6.0, 12.0]
    # All gaps >= 0
    assert all(p["gap_s"] >= 0 for d in payload for p in d["laps"])


def test_leader_ordered_by_finish():
    store = _SyntheticStore()
    payload = rg._build_payload(store, "leader", drivers_filter=None)
    assert [d["driver"] for d in payload] == ["AAA", "BBB", "CCC"]


def test_payload_shape():
    store = _SyntheticStore()
    payload = rg._build_payload(store, "leader", drivers_filter=None)
    for entry in payload:
        assert set(entry.keys()) == {"driver", "team", "color", "laps"}
        for lap in entry["laps"]:
            assert set(lap.keys()) == {"lap", "gap_s"}


# ---------------------------------------------------------------------------
# Average / race-trace math
# ---------------------------------------------------------------------------
def test_average_race_trace_math():
    store = _SyntheticStore()
    payload = rg._build_payload(store, "average", drivers_filter=None)
    by = {d["driver"]: d for d in payload}

    # reference pace = winner A avg = 90.0
    # value = 90*lap - cum
    # A: 90-90, 180-180, 270-270 -> 0,0,0
    assert [p["gap_s"] for p in by["AAA"]["laps"]] == [0.0, 0.0, 0.0]
    # B: 90-91, 180-183, 270-276 -> -1,-3,-6
    assert [p["gap_s"] for p in by["BBB"]["laps"]] == [-1.0, -3.0, -6.0]
    # C: 90-92, 180-186, 270-282 -> -2,-6,-12
    assert [p["gap_s"] for p in by["CCC"]["laps"]] == [-2.0, -6.0, -12.0]


def test_reference_pace_is_winner_average():
    pace = rg._reference_pace(
        _SyntheticStore._LAP_TIMES,
        rg._cumulative_times(_SyntheticStore._LAP_TIMES),
    )
    assert pace == pytest.approx(90.0)


# ---------------------------------------------------------------------------
# Red-flag null insertion
# ---------------------------------------------------------------------------
def test_red_flag_inserts_null():
    # Track status: RED on lap 2 only (synthetic period list feeds via periods).
    store = _SyntheticStore()

    # get_track_status_periods is called inside _build_payload; patch it.
    import src.services.analysis.v2.race_gaps as mod
    orig = mod.get_track_status_periods
    mod.get_track_status_periods = lambda s: [
        {"status": "RED", "start_lap": 2, "end_lap": 2}
    ]
    try:
        payload = mod._build_payload(store, "leader", drivers_filter=None)
    finally:
        mod.get_track_status_periods = orig

    by = {d["driver"]: d for d in payload}
    lap2 = next(p for p in by["BBB"]["laps"] if p["lap"] == 2)
    assert lap2["gap_s"] is None
    lap1 = next(p for p in by["BBB"]["laps"] if p["lap"] == 1)
    assert lap1["gap_s"] == 1.0


# ---------------------------------------------------------------------------
# Drivers filter + unknown TLA
# ---------------------------------------------------------------------------
def test_drivers_filter_via_data_call(monkeypatch):
    # Patch SessionDataStore so RaceGapsData builds from the synthetic store.
    monkeypatch.setattr(rg, "SessionDataStore", lambda *a, **k: _SyntheticStore())
    monkeypatch.setattr(
        rg, "get_track_status_periods", lambda s: []
    )
    result = rg.RaceGapsData()(2025, 1, "R", "leader", "AAA,CCC")
    tlas = {d["driver"] for d in result}
    assert tlas == {"AAA", "CCC"}


def test_unknown_tla_raises(monkeypatch):
    monkeypatch.setattr(rg, "SessionDataStore", lambda *a, **k: _SyntheticStore())
    monkeypatch.setattr(rg, "get_track_status_periods", lambda s: [])
    with pytest.raises(DataNotAvailableError):
        rg.RaceGapsData()(2025, 1, "R", "leader", "ZZZ")


def test_normalize_drivers():
    assert rg._normalize_drivers("nor, pia") == ["NOR", "PIA"]
    assert rg._normalize_drivers(["nor"]) == ["NOR"]
    assert rg._normalize_drivers(None) is None
    assert rg._normalize_drivers("  ") is None


# ---------------------------------------------------------------------------
# Session validation
# ---------------------------------------------------------------------------
def test_session_qualifying_rejected():
    with pytest.raises(DataNotAvailableError):
        rg._assert_valid_session("Q", 2025, 1)


def test_session_data_call_rejects_qualifying():
    with pytest.raises(DataNotAvailableError):
        rg.RaceGapsData()(2025, 1, "Q")


def test_session_accepts_race_and_sprint():
    rg._assert_valid_session("R", 2025, 1)
    rg._assert_valid_session("Race", 2025, 1)
    rg._assert_valid_session("S", 2025, 1)
    rg._assert_valid_session("Sprint", 2025, 1)


# ---------------------------------------------------------------------------
# PNG rendering
# ---------------------------------------------------------------------------
def test_plot_renders_png(tmp_path, monkeypatch):
    store = _SyntheticStore()
    monkeypatch.setattr(rg, "get_track_status_periods", lambda s: [])
    payload = rg._build_payload(store, "leader", drivers_filter=None)

    monkeypatch.chdir(tmp_path)
    out_path = rg.RaceGapsPlot._render(
        payload, [], 2025, "Synthetic GP", "Race", "leader"
    )
    assert os.path.isfile(out_path)
    assert out_path.endswith(".png")


def test_plot_renders_png_average(tmp_path, monkeypatch):
    store = _SyntheticStore()
    monkeypatch.setattr(rg, "get_track_status_periods", lambda s: [])
    payload = rg._build_payload(store, "average", drivers_filter=None)

    monkeypatch.chdir(tmp_path)
    out_path = rg.RaceGapsPlot._render(
        payload, [], 2025, "Synthetic GP", "Race", "average"
    )
    assert os.path.isfile(out_path)


# ---------------------------------------------------------------------------
# Real-fixture smoke (payload shape against captured Australian GP data)
# ---------------------------------------------------------------------------
class _FixtureStore:
    year = 2025
    identifier = 1
    session_name = "Race"
    event_name = "Australian Grand Prix"

    def __init__(self):
        raw = (FIXTURES / "timing_data_sample.jsonStream").read_bytes()
        self._timing = SessionDataStore._parse_stream_content(raw)
        self._track = json.loads((FIXTURES / "track_status_sample.json").read_text())
        self._drivers = json.loads((FIXTURES / "driver_list_sample.json").read_text())

    def timing_data(self):
        return self._timing

    def track_status(self):
        return self._track

    def driver_list(self):
        result = {}
        for num, info in self._drivers.items():
            result[str(num)] = {
                "tla": info.get("Tla", str(num)),
                "team": info.get("TeamName", "Unknown"),
                "color": info.get("TeamColour", ""),
                "name": info.get("FullName", ""),
            }
        return result


def test_fixture_payload_builds(monkeypatch):
    # Undo the synthetic extract_lap_times patch: use the real helper here.
    from src.services.analysis.v2._race_helpers import (
        extract_lap_times as real_extract,
        get_track_status_periods as real_periods,
    )
    monkeypatch.setattr(rg, "extract_lap_times", real_extract)
    monkeypatch.setattr(rg, "get_track_status_periods", real_periods)

    store = _FixtureStore()
    payload = rg._build_payload(store, "leader", drivers_filter=None)
    assert payload, "expected at least one driver from fixtures"
    # Leader (first entry) has gap 0 on its final recorded lap.
    leader = payload[0]
    assert leader["laps"], "leader should have laps"
    assert leader["laps"][-1]["gap_s"] == 0.0
