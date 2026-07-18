"""Offline unit tests for radar metric extractors (V2).

Synthetic timing entries, telemetry frames and season rows exercise the pure
metric math with no network / store dependency.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.services.analysis.v2 import _radar_axes as axes


# ----------------------------------------------------------------------
# Top speed (speed trap)
# ----------------------------------------------------------------------
def test_top_speeds_by_driver_keeps_max():
    entries = [
        {"Lines": {"1": {"Speeds": {"ST": {"Value": "320.0"}}}}},
        {"Lines": {"1": {"Speeds": {"ST": {"Value": "335.5"}}}}},
        {"Lines": {"1": {"Speeds": {"ST": {"Value": "310.0"}}}}},
        {"Lines": {"44": {"Speeds": {"ST": {"Value": "300.0"}}}}},
    ]
    speeds = axes.top_speeds_by_driver(entries)
    assert speeds["1"] == pytest.approx(335.5)
    assert speeds["44"] == pytest.approx(300.0)


def test_top_speeds_ignores_blank_and_bad():
    entries = [{"Lines": {"1": {"Speeds": {"ST": {"Value": ""}}}}},
               {"Lines": {"1": {"Speeds": {"ST": {"Value": "nope"}}}}}]
    assert axes.top_speeds_by_driver(entries) == {}


# ----------------------------------------------------------------------
# Single-session field metrics
# ----------------------------------------------------------------------
def _laps(*times, pit_laps=()):
    out = []
    for i, t in enumerate(times):
        out.append({
            "lap": i + 1, "time_s": t, "timestamp_s": 0.0,
            "pit_in": (i + 1) in pit_laps, "pit_out": False,
        })
    return out


def test_single_session_field_metrics_basic():
    entries = [{"Lines": {"1": {"Speeds": {"ST": {"Value": "330.0"}}}}}]
    lap_times = {"1": _laps(90.0, 90.5, 91.0, 90.2)}
    metrics = axes.single_session_field_metrics(entries, lap_times)
    m = metrics["1"]
    assert m["top_speed"] == pytest.approx(330.0)
    assert m["quali_pace"] == pytest.approx(90.0)          # best lap
    assert m["race_pace"] == pytest.approx(90.35)          # median of the four
    assert m["consistency"] is not None                    # >=3 clean laps


def test_single_session_excludes_pit_laps_and_short_running():
    entries = []
    lap_times = {"1": _laps(90.0, 200.0, 90.5, pit_laps=(2,))}  # lap2 is an in-lap
    metrics = axes.single_session_field_metrics(entries, lap_times)
    m = metrics["1"]
    # Pit lap excluded -> best is 90.0, not skewed by the 200s in-lap.
    assert m["quali_pace"] == pytest.approx(90.0)
    # Only 2 clean laps -> consistency needs >=3 -> None.
    assert m["consistency"] is None


# ----------------------------------------------------------------------
# Corner ratios (telemetry)
# ----------------------------------------------------------------------
def _corner_frame(entry_speed, apex_speed):
    """A distance/speed frame with one clear corner (wide enough to survive
    the rolling-median smoothing in detect_corners)."""
    speeds = [entry_speed] * 4 + [apex_speed] * 5 + [entry_speed] * 4
    dist = [i * 60.0 for i in range(len(speeds))]
    return pd.DataFrame({"Distance": dist, "Speed": speeds})


def test_corner_ratios_high_speed_corner():
    df = _corner_frame(entry_speed=300.0, apex_speed=150.0)
    r = axes.corner_ratios_from_frame(df)
    assert r["cornering"] == pytest.approx(0.5, abs=0.05)
    # Entry 300 >= 200 -> counts as a high-speed corner too.
    assert r["braveness"] == pytest.approx(0.5, abs=0.05)


def test_corner_ratios_low_speed_only_has_no_braveness():
    df = _corner_frame(entry_speed=180.0, apex_speed=90.0)
    r = axes.corner_ratios_from_frame(df)
    assert r["cornering"] is not None
    assert r["braveness"] is None  # no corner clears the high-speed threshold


def test_corner_ratios_empty_frame():
    r = axes.corner_ratios_from_frame(pd.DataFrame())
    assert r == {"cornering": None, "braveness": None}


# ----------------------------------------------------------------------
# Season metrics
# ----------------------------------------------------------------------
def _race_row(driver, rnd, position, grid, classified=True, constructor="T1"):
    return {
        "round": rnd, "driver_code": driver, "constructor": constructor,
        "position": position, "grid": grid,
        "status": "Finished" if classified else "Retired",
        "classified": classified,
    }


def test_season_field_metrics_values():
    race_rows = [
        _race_row("VER", 1, 1, 2),   # gained 1
        _race_row("VER", 2, 2, 4),   # gained 2
        _race_row("VER", 3, 1, 1),   # gained 0
        _race_row("HAM", 1, 5, 3, classified=False),  # DNF
    ]
    quali_rows = [
        {"round": 1, "driver_code": "VER", "constructor": "T1",
         "position": 2, "grid": None, "classified": True},
    ]
    metrics = axes.season_field_metrics(race_rows, quali_rows)
    ver = metrics["VER"]
    assert ver["race_pace"] == pytest.approx((1 + 2 + 1) / 3)
    assert ver["racecraft"] == pytest.approx((1 + 2 + 0) / 3)
    assert ver["reliability"] == pytest.approx(1.0)   # all 3 classified
    assert ver["peak"] == pytest.approx(1.0)          # all top-3 finishes
    assert ver["consistency"] is not None             # 3 finishes

    ham = metrics["HAM"]
    assert ham["reliability"] == pytest.approx(0.0)   # 0/1 classified
    assert ham["race_pace"] is None                   # no classified finish
