"""Offline unit tests for Telemetry Track Map (V2).

Pure-function tests on synthetic DataFrames — no network, Redis, or Mongo.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.services.analysis.v2 import telemetry_track_map as tm


# ----------------------------------------------------------------------
# _braking_segments — square-wave segmentation
# ----------------------------------------------------------------------
def test_braking_segments_square_wave():
    """Brake toggles 0/1/0/1/0 should yield two clean contiguous segments."""
    df = pd.DataFrame({
        "Distance": [0, 10, 20, 30, 40, 50, 60, 70, 80],
        "Brake": [0, 0, 100, 100, 0, 0, 100, 0, 0],
    })
    segs = tm._braking_segments(df)
    assert len(segs) == 2
    assert segs[0]["start_idx"] == 2 and segs[0]["end_idx"] == 3
    assert segs[0]["start_distance"] == 20 and segs[0]["end_distance"] == 30
    assert segs[1]["start_idx"] == 6 and segs[1]["end_idx"] == 6


def test_braking_segments_trailing_zone_open_at_end():
    """A braking zone still active on the final sample is still captured."""
    df = pd.DataFrame({
        "Distance": [0, 10, 20, 30],
        "Brake": [0, 0, 100, 100],
    })
    segs = tm._braking_segments(df)
    assert len(segs) == 1
    assert segs[0]["end_idx"] == 3


def test_braking_segments_no_braking():
    df = pd.DataFrame({"Distance": [0, 10, 20], "Brake": [0, 0, 0]})
    assert tm._braking_segments(df) == []


def test_braking_segments_empty_or_missing_column():
    assert tm._braking_segments(pd.DataFrame()) == []
    assert tm._braking_segments(pd.DataFrame({"Distance": [0, 1]})) == []


# ----------------------------------------------------------------------
# _find_callouts — extremes
# ----------------------------------------------------------------------
def test_find_callouts_extremes():
    df = pd.DataFrame({
        "Distance": [0, 10, 20, 30, 40],
        "Speed": [200, 320, 90, 250, 310],
        "X": [0, 1, 2, 3, 4],
        "Y": [0, 1, 2, 3, 4],
    })
    callouts = tm._find_callouts(df)
    assert callouts["top_speed"]["speed"] == 320
    assert callouts["top_speed"]["distance"] == 10
    assert callouts["slowest_corner"]["speed"] == 90
    assert callouts["slowest_corner"]["distance"] == 20


def test_find_callouts_empty_frame():
    callouts = tm._find_callouts(pd.DataFrame())
    assert callouts == {"top_speed": None, "slowest_corner": None}


def test_find_callouts_missing_speed_column():
    df = pd.DataFrame({"Distance": [0, 1]})
    callouts = tm._find_callouts(df)
    assert callouts["top_speed"] is None
    assert callouts["slowest_corner"] is None


# ----------------------------------------------------------------------
# _downsample_map_points — preserves first/last, respects bound
# ----------------------------------------------------------------------
def test_downsample_preserves_first_and_last():
    df = pd.DataFrame({"Distance": list(range(2000)), "Speed": [100] * 2000})
    out = tm._downsample_map_points(df, max_points=800)
    assert len(out) <= 800 + 2  # small slack for first/last insertion
    assert out.iloc[0]["Distance"] == 0
    assert out.iloc[-1]["Distance"] == 1999


def test_downsample_noop_when_under_limit():
    df = pd.DataFrame({"Distance": list(range(10)), "Speed": [1] * 10})
    out = tm._downsample_map_points(df, max_points=800)
    assert len(out) == 10


def test_downsample_bound_respected():
    df = pd.DataFrame({"Distance": list(range(5000)), "Speed": [1] * 5000})
    out = tm._downsample_map_points(df, max_points=500)
    assert len(out) <= 502


# ----------------------------------------------------------------------
# color_by validation
# ----------------------------------------------------------------------
def test_assert_valid_color_by_rejects_unknown():
    from src.core.exceptions import DataNotAvailableError
    with pytest.raises(DataNotAvailableError):
        tm._assert_valid_color_by("throttle", 2025, 1, "Q")


def test_assert_valid_color_by_accepts_speed_and_gear():
    tm._assert_valid_color_by("speed", 2025, 1, "Q")
    tm._assert_valid_color_by("gear", 2025, 1, "Q")


# ----------------------------------------------------------------------
# _rotate_xy
# ----------------------------------------------------------------------
def test_rotate_xy_zero_is_noop():
    import numpy as np
    x = np.array([1.0, 2.0])
    y = np.array([3.0, 4.0])
    rx, ry = tm._rotate_xy(x, y, 0)
    assert list(rx) == list(x)
    assert list(ry) == list(y)


def test_rotate_xy_90_degrees():
    import numpy as np
    x = np.array([1.0])
    y = np.array([0.0])
    rx, ry = tm._rotate_xy(x, y, 90)
    assert rx[0] == pytest.approx(0.0, abs=1e-9)
    assert ry[0] == pytest.approx(1.0, abs=1e-9)
