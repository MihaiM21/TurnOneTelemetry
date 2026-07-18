"""Offline unit tests for Track Evolution (V2).

Synthetic lap-time / weather dicts so the running-best step series and
weather-overlay handling can be verified with no network, Redis, or Mongo.
"""
from __future__ import annotations

import os

import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.analysis.v2 import track_evolution as te


def _lap_records(times, pit_out_laps=()):
    """[(lap, time_s, timestamp_s), ...] -> lap-time records list."""
    return [
        {
            "lap": lap, "time_s": t, "timestamp_s": ts,
            "pit_in": False, "pit_out": lap in pit_out_laps,
        }
        for lap, t, ts in times
    ]


def _drivers(*nums):
    return {n: {"tla": f"D{n}", "team": f"Team{n}"} for n in nums}


def _weather_entry(t_str, track_temp):
    return {"_timestamp": t_str, "TrackTemp": track_temp}


# ----------------------------------------------------------------------
# Session validation
# ----------------------------------------------------------------------
def test_assert_valid_session_rejects_race():
    with pytest.raises(DataNotAvailableError):
        te._assert_valid_session("R", 2025, 1)


def test_assert_valid_session_accepts_practice_and_quali():
    te._assert_valid_session("FP1", 2025, 1)
    te._assert_valid_session("Q", 2025, 1)
    te._assert_valid_session("SQ", 2025, 1)


def test_data_call_rejects_race():
    with pytest.raises(DataNotAvailableError):
        te.TrackEvolutionData()(2025, 1, "R")


# ----------------------------------------------------------------------
# Running best is monotonic and skips pit-out laps
# ----------------------------------------------------------------------
def test_running_best_monotonic_non_increasing():
    records = _lap_records([
        (1, 95.0, 100.0), (2, 93.0, 200.0), (3, 94.0, 300.0), (4, 90.0, 400.0),
    ])
    series = te._running_best(records, t0=0.0)
    bests = [p["best_s"] for p in series]
    assert bests == [95.0, 93.0, 93.0, 90.0]
    # Monotonic non-increasing.
    assert all(bests[i] >= bests[i + 1] for i in range(len(bests) - 1))


def test_running_best_skips_pit_out_laps():
    records = _lap_records(
        [(1, 95.0, 100.0), (2, 200.0, 200.0), (3, 92.0, 300.0)],
        pit_out_laps={2},
    )
    series = te._running_best(records, t0=0.0)
    bests = [p["best_s"] for p in series]
    # The 200.0 out-lap must never appear or become the running best.
    assert 200.0 not in bests
    assert bests == [95.0, 92.0]


def test_running_best_minute_offset_from_t0():
    records = _lap_records([(1, 90.0, 660.0)])  # 660s = 11 minutes
    series = te._running_best(records, t0=60.0)  # session started at t=60s
    assert series[0]["minute"] == pytest.approx(10.0)


# ----------------------------------------------------------------------
# Weather series: bad values and empty stream
# ----------------------------------------------------------------------
def test_weather_series_drops_bad_values():
    entries = [
        _weather_entry("00:01:00.000", "35.2"),
        _weather_entry("00:02:00.000", "not-a-number"),
        _weather_entry("00:03:00.000", None),
        _weather_entry("00:04:00.000", "36.0"),
    ]
    series = te._weather_series(entries, t0=0.0)
    assert len(series) == 2
    assert series[0]["track_temp"] == pytest.approx(35.2)
    assert series[1]["track_temp"] == pytest.approx(36.0)


def test_weather_series_empty_stream_returns_empty_list():
    assert te._weather_series([], t0=0.0) == []
    assert te._weather_series(None, t0=0.0) == []


def test_build_payload_with_missing_weather_has_empty_weather_key():
    lap_times = {"1": _lap_records([(1, 90.0, 100.0)])}
    drivers = _drivers("1")
    payload = te.build_payload_from_parts(lap_times, drivers, [])
    assert payload["weather"] == []
    assert payload["overall"]


# ----------------------------------------------------------------------
# Driver selection
# ----------------------------------------------------------------------
def test_default_driver_selection_picks_fastest_five():
    lap_times = {
        str(n): _lap_records([(1, 100.0 - n, float(n))]) for n in range(1, 8)
    }
    drivers = _drivers(*[str(n) for n in range(1, 8)])
    payload = te.build_payload_from_parts(lap_times, drivers, [])
    # Fastest laps belong to the highest n (100 - n smallest for n=7..3).
    assert len(payload["drivers"]) == 5
    assert set(payload["drivers"].keys()) == {"D7", "D6", "D5", "D4", "D3"}


def test_requested_drivers_filter_selection():
    lap_times = {
        "1": _lap_records([(1, 95.0, 100.0)]),
        "2": _lap_records([(1, 96.0, 100.0)]),
        "3": _lap_records([(1, 97.0, 100.0)]),
    }
    drivers = _drivers("1", "2", "3")
    payload = te.build_payload_from_parts(lap_times, drivers, [], requested_tlas=["D2"])
    assert set(payload["drivers"].keys()) == {"D2"}


# ----------------------------------------------------------------------
# Plot
# ----------------------------------------------------------------------
def test_plot_renders_png(tmp_path, monkeypatch):
    lap_times = {
        "1": _lap_records([(1, 95.0, 100.0), (2, 93.0, 200.0)]),
        "2": _lap_records([(1, 96.0, 150.0), (2, 92.0, 250.0)]),
    }
    drivers = _drivers("1", "2")
    weather = [_weather_entry("00:01:00.000", "34.0"), _weather_entry("00:03:00.000", "35.5")]
    payload = te.build_payload_from_parts(lap_times, drivers, weather)

    monkeypatch.chdir(tmp_path)
    out = te.TrackEvolutionPlot._render(payload, 2025, "Test GP", "FP1")
    assert os.path.isfile(out)
    assert out.endswith(".png")


def test_plot_renders_png_without_weather(tmp_path, monkeypatch):
    lap_times = {"1": _lap_records([(1, 95.0, 100.0)])}
    drivers = _drivers("1")
    payload = te.build_payload_from_parts(lap_times, drivers, [])

    monkeypatch.chdir(tmp_path)
    out = te.TrackEvolutionPlot._render(payload, 2025, "Test GP", "Q")
    assert os.path.isfile(out)
