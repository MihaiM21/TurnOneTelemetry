"""Offline unit tests for LapAllData (V2).

Exercises the per-lap assembly logic (driver/lap resolution, stint & weather &
track-status selection, telemetry-row normalization) with synthetic in-memory
data. The heavy stream-extract functions are monkeypatched to return small
DataFrames, so no network / Mongo / Redis is touched.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.analysis.v2 import lap_all_data as m


class _FakeStore:
    year = 2025
    identifier = 1
    session_name = "R"
    event_name = "Synthetic Grand Prix"
    round_nr = 1
    base_url = "http://example/"
    client = object()

    def driver_list(self):
        return {"44": {"tla": "HAM", "team": "Ferrari", "color": "#E80020",
                       "name": "Lewis Hamilton", "racing_number": "44"}}

    def lap_times(self):
        return {"44": [
            {"lap": 1, "time_s": 90.0, "timestamp_s": 100.0, "pit_in": False, "pit_out": True},
            {"lap": 2, "time_s": 88.0, "timestamp_s": 188.0, "pit_in": False, "pit_out": False},
        ]}

    def positions_by_lap(self):
        return {"44": [{"lap": 1, "position": 3}, {"lap": 2, "position": 2}]}

    def stints(self):
        return {"44": [
            {"stint_number": 1, "compound": "MEDIUM", "start_lap": 1, "end_lap": 20,
             "lap_count": 20, "tyre_life_end": 20},
        ]}

    def best_sectors(self):
        return {"44": {"s1": 28.1, "s2": 31.2, "s3": 30.7}}

    def weather_data(self):
        return [
            {"_timestamp": "00:01:00.000", "AirTemp": "20"},   # 60s
            {"_timestamp": "00:03:05.000", "AirTemp": "22"},   # 185s (closest to 188)
        ]

    def track_status_periods(self):
        return [
            {"status": "GREEN", "start_lap": 1, "end_lap": 1},
            {"status": "SC", "start_lap": 2, "end_lap": 4},
        ]


@pytest.fixture
def _fake_telemetry(monkeypatch):
    """Stub the two stream-extract functions with small deterministic frames."""
    def _tel(base_url, client, num, start_t, end_t, channels=None, store=None):
        return pd.DataFrame([
            {"Time": 0.0, "Speed": 250.0, "RPM": 11000.0, "Throttle": 100.0,
             "Brake": 0.0, "Gear": 7.0, "47": 1.0},
            {"Time": 1.0, "Speed": 120.0, "RPM": 9000.0, "Throttle": 0.0,
             "Brake": 80.0, "Gear": 3.0, "47": 0.0},
        ])

    def _pos(base_url, client, num, start_t, end_t, store=None):
        return pd.DataFrame([
            {"Time": 0.0, "X": 0.0, "Y": 0.0, "Z": 0.0},
            {"Time": 1.0, "X": 30.0, "Y": 40.0, "Z": 0.0},
        ])

    monkeypatch.setattr(m, "extract_telemetry_for_lap", _tel)
    monkeypatch.setattr(m, "extract_position_for_lap", _pos)


# ---------------------------------------------------------------------------
# Driver / lap resolution
# ---------------------------------------------------------------------------
def test_resolve_driver_unknown_raises():
    with pytest.raises(DataNotAvailableError):
        m._resolve_driver(_FakeStore(), "VER")


def test_find_lap_missing_raises():
    with pytest.raises(DataNotAvailableError):
        m._find_lap(_FakeStore().lap_times()["44"], 99)


# ---------------------------------------------------------------------------
# Metadata selection helpers
# ---------------------------------------------------------------------------
def test_stint_for_lap_selects_containing_stint():
    st = m._stint_for_lap(_FakeStore().stints()["44"], 5)
    assert st["compound"] == "MEDIUM"
    assert m._stint_for_lap(_FakeStore().stints()["44"], 25) is None


def test_nearest_weather_picks_closest_sample():
    w = m._nearest_weather(_FakeStore().weather_data(), 188.0)
    assert w["AirTemp"] == "22"


def test_track_status_for_lap_filters_to_covering_periods():
    periods = _FakeStore().track_status_periods()
    assert [p["status"] for p in m._track_status_for_lap(periods, 2)] == ["SC"]
    assert [p["status"] for p in m._track_status_for_lap(periods, 1)] == ["GREEN"]


# ---------------------------------------------------------------------------
# Full payload
# ---------------------------------------------------------------------------
def test_build_payload_full_shape(_fake_telemetry):
    payload = m._build_payload(_FakeStore(), "HAM", 2)

    assert set(payload) >= {
        "driver", "lap", "tyre", "sectors", "weather",
        "track_status", "telemetry", "session_info",
    }
    assert payload["driver"]["tla"] == "HAM"
    assert payload["driver"]["car_number"] == "44"
    assert payload["lap"]["number"] == 2
    assert payload["lap"]["position"] == 2
    assert payload["tyre"]["compound"] == "MEDIUM"
    assert payload["sectors"]["s2"] == 31.2
    assert payload["weather"]["AirTemp"] == "22"
    assert [p["status"] for p in payload["track_status"]] == ["SC"]

    rows = payload["telemetry"]
    assert len(rows) == 2
    first = rows[0]
    assert set(first) == {
        "time", "distance", "speed", "rpm", "throttle",
        "brake", "gear", "drs", "x", "y", "z",
    }
    assert first["speed"] == 250.0
    assert first["drs"] == 1
    # Distance accumulates from X/Y deltas: sqrt(30^2+40^2) = 50 at the 2nd sample.
    assert rows[1]["distance"] == pytest.approx(50.0)
    assert rows[1]["x"] == 30.0


def test_build_payload_empty_telemetry_raises(monkeypatch):
    monkeypatch.setattr(m, "extract_telemetry_for_lap",
                        lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(m, "extract_position_for_lap",
                        lambda *a, **k: pd.DataFrame())
    with pytest.raises(DataNotAvailableError):
        m._build_payload(_FakeStore(), "HAM", 1)
