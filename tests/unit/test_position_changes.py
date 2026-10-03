"""Offline unit tests for Position Changes (V2).

Reuses the captured 2025 Australian GP fixtures from ``test_race_helpers.py``'s
pattern: a minimal fake store returns parsed fixtures directly, so parsing and
plotting are exercised with no network and no Redis/Mongo.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.analysis.v2 import position_changes as pc
from src.services.analysis.v2.session_store import SessionDataStore

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeStore:
    """Returns parsed fixtures for the stream accessors position_changes uses."""

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


@pytest.fixture
def store():
    return _FakeStore()


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    """Bypass Redis/Mongo entirely so cached_or_generate always calls generator."""
    monkeypatch.setattr(
        "src.services.analysis.base.get_plot_data_from_mongo",
        lambda *a, **k: None,
    )


def test_build_payload_shape(store):
    payload = pc._build_payload(store)
    assert payload, "expected at least one driver in the payload"
    for entry in payload:
        assert set(entry.keys()) == {
            "driver", "team", "color", "start_pos", "end_pos", "positions",
        }
        assert isinstance(entry["positions"], list)
        assert all({"lap", "position"} <= set(p.keys()) for p in entry["positions"])


def test_build_payload_start_end_positions(store):
    payload = pc._build_payload(store)
    by_driver = {d["driver"]: d for d in payload}
    # Driver "4" (NOR) is present in the fixture and finishes P1 per test_race_helpers.
    assert "NOR" in by_driver
    nor = by_driver["NOR"]
    assert nor["end_pos"] == 1
    assert nor["positions"][0]["lap"] == 0 or nor["positions"][0]["lap"] >= 0
    assert nor["positions"][-1]["position"] == nor["end_pos"]


def test_build_payload_ordered_by_end_position(store):
    payload = pc._build_payload(store)
    end_positions = [d["end_pos"] for d in payload]
    assert end_positions == sorted(end_positions)


def _assert_valid_session(session_name, year, identifier):
    pc.assert_session_type(
        session_name, year, identifier,
        allowed=pc.RACE_SESSIONS, feature="Position changes", sessions_label="Race/Sprint",
    )


def test_assert_valid_session_rejects_qualifying():
    with pytest.raises(DataNotAvailableError):
        _assert_valid_session("Q", 2025, 1)


def test_assert_valid_session_rejects_practice():
    with pytest.raises(DataNotAvailableError):
        _assert_valid_session("FP1", 2025, 1)


def test_assert_valid_session_accepts_race_and_sprint():
    _assert_valid_session("R", 2025, 1)
    _assert_valid_session("Race", 2025, 1)
    _assert_valid_session("S", 2025, 1)
    _assert_valid_session("Sprint", 2025, 1)


def test_data_call_rejects_qualifying(monkeypatch):
    with pytest.raises(DataNotAvailableError):
        pc.PositionChangesData()(2025, 1, "Q")


def test_plot_renders_png(store, monkeypatch, tmp_path):
    payload = pc._build_payload(store)
    from src.services.analysis.v2._race_helpers import get_track_status_periods
    periods = get_track_status_periods(store)

    monkeypatch.chdir(tmp_path)
    out_path = pc.PositionChangesPlot._render(
        payload, periods, 2025, "Australian Grand Prix", "Race"
    )

    assert os.path.isfile(out_path)
    assert out_path.endswith(".png")


# ----------------------------------------------------------------------
# Change-log -> one point per lap
# ----------------------------------------------------------------------
def test_fill_laps_single_record_runs_to_last_lap():
    out = pc._fill_laps([{"lap": 0, "position": 1}], 5)
    assert [r["lap"] for r in out] == [0, 1, 2, 3, 4, 5]
    assert {r["position"] for r in out} == {1}


def test_fill_laps_carries_positions_forward():
    records = [{"lap": 0, "position": 7}, {"lap": 1, "position": 6}, {"lap": 5, "position": 3}]
    out = pc._fill_laps(records, 6)
    assert [r["lap"] for r in out] == list(range(7))
    assert [r["position"] for r in out] == [7, 6, 6, 6, 6, 3, 3]


def test_fill_laps_never_stops_before_the_last_record():
    records = [{"lap": 0, "position": 4}, {"lap": 8, "position": 2}]
    out = pc._fill_laps(records, 3)
    assert out[-1] == {"lap": 8, "position": 2}
    assert len(out) == 9


def test_laps_completed_empty_when_lap_times_raise(monkeypatch):
    def boom(_store):
        raise RuntimeError("no lap stream")

    monkeypatch.setattr(pc, "extract_lap_times", boom)
    assert pc._laps_completed(object()) == {}


def test_laps_completed_is_highest_lap_per_car(monkeypatch):
    monkeypatch.setattr(pc, "extract_lap_times", lambda _s: {"63": [{"lap": 1}, {"lap": 3}], "1": []})
    assert pc._laps_completed(object()) == {"63": 3, "1": 0}


def test_build_payload_emits_one_entry_per_lap(monkeypatch):
    class _Store:
        def driver_list(self):
            return {"63": {"tla": "RUS", "team": "Mercedes", "color": "00D7B6"}}

    monkeypatch.setattr(pc, "extract_positions_by_lap",
                        lambda _s: {"63": [{"lap": 0, "position": 3}, {"lap": 4, "position": 2}]})
    monkeypatch.setattr(pc, "extract_lap_times", lambda _s: {"63": [{"lap": n} for n in range(1, 11)]})
    payload = pc._build_payload(_Store())
    assert len(payload) == 1
    rus = payload[0]
    assert rus["driver"] == "RUS" and rus["color"] == "#00D7B6"
    assert rus["start_pos"] == 3 and rus["end_pos"] == 2
    assert [p["lap"] for p in rus["positions"]] == list(range(11))
    assert [p["position"] for p in rus["positions"]] == [3, 3, 3, 3, 2, 2, 2, 2, 2, 2, 2]
