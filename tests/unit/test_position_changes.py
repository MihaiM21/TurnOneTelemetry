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


def test_assert_valid_session_rejects_qualifying():
    with pytest.raises(DataNotAvailableError):
        pc._assert_valid_session("Q", 2025, 1)


def test_assert_valid_session_rejects_practice():
    with pytest.raises(DataNotAvailableError):
        pc._assert_valid_session("FP1", 2025, 1)


def test_assert_valid_session_accepts_race_and_sprint():
    pc._assert_valid_session("R", 2025, 1)
    pc._assert_valid_session("Race", 2025, 1)
    pc._assert_valid_session("S", 2025, 1)
    pc._assert_valid_session("Sprint", 2025, 1)


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
