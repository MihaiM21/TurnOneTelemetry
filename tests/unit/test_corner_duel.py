"""Offline unit tests for Corner-by-Corner Duel (V2) and detect_corners (_helpers).

Pure-function tests on synthetic DataFrames / arrays — no network, Redis, or Mongo.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.services.analysis.v2 import corner_duel as cd
from src.services.analysis.v2._helpers import detect_corners


# ----------------------------------------------------------------------
# detect_corners — synthetic 3-corner speed profile
# ----------------------------------------------------------------------
def _three_corner_profile():
    """Distance 0..1200m, speed peaks and dips at three well-separated corners."""
    distance = np.arange(0, 1200, 5.0)
    speed = np.full_like(distance, 300.0)

    def dip(center, width, min_speed):
        mask = np.abs(distance - center) < width
        # Smooth-ish dip via a parabola clipped at min_speed..300.
        depth = 300.0 - min_speed
        speed[mask] = np.minimum(
            speed[mask],
            300.0 - depth * (1 - ((distance[mask] - center) / width) ** 2),
        )

    dip(200, 60, 80)   # corner 1: heavy braking, slow apex
    dip(600, 60, 150)  # corner 2: medium speed corner
    dip(1000, 60, 100)  # corner 3: slow-ish

    df = pd.DataFrame({"Distance": distance, "Speed": speed})
    return df


def test_detect_corners_count_and_apex_distances():
    df = _three_corner_profile()
    corners = detect_corners(df, min_separation_m=50.0, min_drop_kmh=10.0)
    assert len(corners) == 3
    apexes = [c["distance_m"] for c in corners]
    # Apexes should land close to the dip centers, in ascending order.
    assert apexes == sorted(apexes)
    assert apexes[0] == pytest.approx(200, abs=15)
    assert apexes[1] == pytest.approx(600, abs=15)
    assert apexes[2] == pytest.approx(1000, abs=15)


def test_detect_corners_entry_and_min_speed_sane():
    df = _three_corner_profile()
    corners = detect_corners(df)
    for c in corners:
        assert c["min_speed_kmh"] < c["entry_speed_kmh"]


def test_detect_corners_enforces_min_separation():
    """Two dips within the separation window collapse to one (the slower apex)."""
    distance = np.arange(0, 400, 5.0)
    speed = np.full_like(distance, 300.0)
    # Two dips only 30m apart -> should merge under a 50m separation threshold.
    speed[(distance > 190) & (distance < 210)] = 120.0
    speed[(distance > 215) & (distance < 235)] = 90.0
    df = pd.DataFrame({"Distance": distance, "Speed": speed})

    corners = detect_corners(df, min_separation_m=50.0, min_drop_kmh=10.0)
    assert len(corners) == 1
    assert corners[0]["min_speed_kmh"] == pytest.approx(90.0, abs=1.0)


def test_detect_corners_empty_or_missing_columns():
    assert detect_corners(pd.DataFrame()) == []
    assert detect_corners(pd.DataFrame({"Distance": [1, 2, 3]})) == []


def test_detect_corners_too_few_points():
    df = pd.DataFrame({"Distance": [0, 10], "Speed": [200, 100]})
    assert detect_corners(df) == []


# ----------------------------------------------------------------------
# Delta sign convention — two constant-speed traces
# ----------------------------------------------------------------------
def test_delta_sign_convention_driver2_slower_is_positive():
    """driver2 takes longer (bigger elapsed time) at every point -> delta > 0 (driver2 behind)."""
    distance = np.linspace(0, 1000, 100)
    time1 = distance / 50.0   # driver1: 50 m/s constant
    time2 = distance / 40.0   # driver2: slower, 40 m/s constant -> larger elapsed time

    grid = cd.build_common_distance_grid(distance, distance, n=50)
    t1_grid = cd.interp_time_onto_grid(distance, time1, grid)
    t2_grid = cd.interp_time_onto_grid(distance, time2, grid)
    delta = cd.compute_delta_series(t1_grid, t2_grid)

    # Skip the very first point (delta ~0 at start).
    assert np.all(delta[5:] > 0)


def test_delta_sign_convention_driver2_faster_is_negative():
    distance = np.linspace(0, 1000, 100)
    time1 = distance / 40.0   # driver1 slower
    time2 = distance / 50.0   # driver2 faster -> smaller elapsed time -> delta < 0 (driver2 ahead)

    grid = cd.build_common_distance_grid(distance, distance, n=50)
    t1_grid = cd.interp_time_onto_grid(distance, time1, grid)
    t2_grid = cd.interp_time_onto_grid(distance, time2, grid)
    delta = cd.compute_delta_series(t1_grid, t2_grid)

    assert np.all(delta[5:] < 0)


def test_delta_series_identical_drivers_is_zero():
    distance = np.linspace(0, 1000, 100)
    time_ = distance / 45.0

    grid = cd.build_common_distance_grid(distance, distance, n=50)
    t1_grid = cd.interp_time_onto_grid(distance, time_, grid)
    t2_grid = cd.interp_time_onto_grid(distance, time_, grid)
    delta = cd.compute_delta_series(t1_grid, t2_grid)

    assert np.allclose(delta, 0.0, atol=1e-9)


# ----------------------------------------------------------------------
# Braking-point backward scan
# ----------------------------------------------------------------------
def test_braking_point_backward_scan_finds_first_brake_at_apex():
    """Scanning backward from the apex, the first Brake>0 sample hit wins —

    if braking is still active right at the apex, that's the "first" hit.
    """
    distance = np.arange(0, 300, 5.0)
    brake = np.zeros_like(distance)
    # Braking starts at distance=150 and continues to the apex at 200.
    brake[(distance >= 150) & (distance <= 200)] = 100.0

    point = cd._braking_point_backward_scan(distance, brake, apex=200.0, scan_back=300.0)
    assert point == pytest.approx(200.0, abs=5.0)


def test_braking_point_backward_scan_gap_before_apex():
    """When braking has already stopped before the apex, the scan finds that
    earlier isolated braking sample (still the first Brake>0 walking backward).
    """
    distance = np.arange(0, 300, 5.0)
    brake = np.zeros_like(distance)
    # Braking only in a short window well before the apex.
    brake[(distance >= 140) & (distance <= 160)] = 100.0

    point = cd._braking_point_backward_scan(distance, brake, apex=200.0, scan_back=300.0)
    assert point == pytest.approx(160.0, abs=5.0)


def test_braking_point_backward_scan_no_braking_returns_none():
    distance = np.arange(0, 300, 5.0)
    brake = np.zeros_like(distance)
    point = cd._braking_point_backward_scan(distance, brake, apex=200.0, scan_back=300.0)
    assert point is None


def test_braking_point_backward_scan_respects_scan_back_window():
    distance = np.arange(0, 300, 5.0)
    brake = np.zeros_like(distance)
    # Braking way back at distance=10, outside a small scan_back window from apex=200.
    brake[(distance >= 5) & (distance <= 15)] = 100.0

    point = cd._braking_point_backward_scan(distance, brake, apex=200.0, scan_back=50.0)
    assert point is None


# ----------------------------------------------------------------------
# _match_corner_number
# ----------------------------------------------------------------------
def test_match_corner_number_within_radius():
    circuit_corners = [
        {"number": 1, "x": 100.0, "y": 100.0},
        {"number": 2, "x": 500.0, "y": 500.0},
    ]
    num = cd._match_corner_number(110.0, 105.0, circuit_corners, radius=150.0)
    assert num == 1


def test_match_corner_number_outside_radius_returns_none():
    circuit_corners = [{"number": 1, "x": 100.0, "y": 100.0}]
    num = cd._match_corner_number(1000.0, 1000.0, circuit_corners, radius=150.0)
    assert num is None


def test_match_corner_number_no_circuit_data():
    assert cd._match_corner_number(1.0, 1.0, []) is None
    assert cd._match_corner_number(None, None, [{"number": 1, "x": 0, "y": 0}]) is None


# ----------------------------------------------------------------------
# _data_type — sorted TLA cache key
# ----------------------------------------------------------------------
def test_data_type_sorts_tlas():
    assert cd._data_type("NOR", "VER") == cd._data_type("VER", "NOR")
    assert cd._data_type("VER", "NOR") == "corner_duel_NOR_VER"


# ----------------------------------------------------------------------
# _downsample_indices + speed_series payload shape
# ----------------------------------------------------------------------
def test_downsample_indices_bound_and_endpoints():
    idx = cd._downsample_indices(5000, max_points=400)
    assert len(idx) <= 400
    assert idx[0] == 0
    assert idx[-1] == 4999


def test_downsample_indices_noop_when_under_limit():
    assert cd._downsample_indices(100, max_points=400) == list(range(100))


def test_build_payload_includes_per_driver_speed_series():
    n = 1000
    distance = np.linspace(0, 2000, n)
    df1 = pd.DataFrame({
        "Distance": distance,
        "Time": distance / 50.0,
        "Speed": np.full(n, 280.0),
        "Brake": np.zeros(n),
    })
    df2 = pd.DataFrame({
        "Distance": distance,
        "Time": distance / 45.0,
        "Speed": np.full(n, 260.0),
        "Brake": np.zeros(n),
    })

    payload = cd.build_payload_from_parts(
        df1, df2, "VER", "NOR", None, "Red Bull Racing", "McLaren",
        2025, "Test GP", "Q",
    )

    assert set(payload["speed_series"].keys()) == {"VER", "NOR"}
    for tla, expected_speed in (("VER", 280.0), ("NOR", 260.0)):
        trace = payload["speed_series"][tla]
        assert 0 < len(trace) <= cd._MAX_DELTA_SERIES_POINTS
        assert trace[0]["dist_m"] == pytest.approx(0.0)
        assert trace[-1]["dist_m"] == pytest.approx(2000.0)
        assert all(p["speed_kmh"] == pytest.approx(expected_speed) for p in trace)
    assert payload["same_team"] is False
    # driver2 (NOR) is slower here -> delta positive (NOR behind).
    assert payload["delta_series"][-1]["delta_s"] > 0
