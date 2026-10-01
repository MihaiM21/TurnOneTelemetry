"""Offline unit tests for the Lap Duel pure math (``_lap_duel_core``).

Everything runs on synthetic laps built in ``_lap_duel_synth`` -- no network,
Redis, or Mongo.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.services.analysis.v2 import _lap_duel_core as core
from tests.unit._lap_duel_synth import (
    constant_speed,
    dipped_speed,
    make_car,
    make_circle_pos,
)

START = 1000.0


def _trace(lap_time=60.0, speed_fn=None, radius=477.5):
    speed_fn = speed_fn or constant_speed(180.0)
    car = make_car(START, lap_time, speed_fn)
    pos = make_circle_pos(START, lap_time, radius)
    return core.build_lap_trace(car, pos, START, lap_time)


# ----------------------------------------------------------------------
# build_lap_trace / resample_by_fraction
# ----------------------------------------------------------------------
def test_build_lap_trace_starts_at_zero_and_integrates_speed():
    tr = _trace(60.0)
    assert list(tr.columns) == ["t", "d", "speed", "throttle", "brake", "gear", "rpm", "drs", "x", "y"]
    assert tr["d"].iloc[0] == pytest.approx(0.0, abs=1e-6)
    assert tr["t"].iloc[0] == 0.0
    assert tr["t"].iloc[-1] == pytest.approx(60.0)
    # 180 km/h = 50 m/s for 60 s.
    assert tr["d"].iloc[-1] == pytest.approx(3000.0, rel=0.01)
    # margin samples are dropped and distance never goes backwards
    assert tr["t"].between(0.0, 60.0).all()
    assert (np.diff(tr["d"].to_numpy()) >= 0).all()


def test_build_lap_trace_empty_input_returns_empty_frame():
    import pandas as pd

    assert core.build_lap_trace(pd.DataFrame(), pd.DataFrame(), START, 60.0).empty
    assert core.build_lap_trace(None, None, START, 60.0).empty


def test_build_lap_trace_without_position_has_nan_xy():
    car = make_car(START, 60.0, constant_speed(180.0))
    tr = core.build_lap_trace(car, None, START, 60.0)
    assert tr["x"].isna().all() and tr["y"].isna().all()
    assert tr["d"].iloc[-1] == pytest.approx(3000.0, rel=0.01)


def test_resample_by_fraction_endpoints_and_length():
    tr = _trace(60.0)
    grid = core.resample_by_fraction(tr, ref_length=3100.0)
    n = core.GRID_POINTS
    for key in ("distance", "own_distance", "t", "speed", "gear", "brake", "x", "y"):
        assert len(grid[key]) == n
    assert grid["distance"][0] == 0.0
    assert grid["distance"][-1] == pytest.approx(3100.0)
    assert grid["own_distance"][-1] == pytest.approx(float(tr["d"].iloc[-1]))
    assert float(grid["length_m"]) == pytest.approx(float(tr["d"].iloc[-1]))
    assert grid["t"][0] == pytest.approx(0.0)
    assert grid["t"][-1] == pytest.approx(60.0)


def test_resample_by_fraction_keeps_discrete_channels_discrete():
    tr = _trace(60.0, speed_fn=dipped_speed())
    grid = core.resample_by_fraction(tr, float(tr["d"].iloc[-1]))
    gears = set(np.unique(grid["gear"]))
    assert gears <= set(np.unique(tr["gear"]))  # no invented 4.5
    assert set(np.unique(grid["brake"])) <= set(np.unique(tr["brake"]))


def test_resample_by_fraction_rejects_zero_length_lap():
    tr = _trace(60.0)
    tr["d"] = 0.0
    with pytest.raises(ValueError):
        core.resample_by_fraction(tr, 1000.0)


# ----------------------------------------------------------------------
# compute_delta
# ----------------------------------------------------------------------
def test_compute_delta_sign_and_final_value():
    a = _trace(60.0)
    b = _trace(61.2, speed_fn=constant_speed(176.0))  # slower lap B
    ga = core.resample_by_fraction(a, float(a["d"].iloc[-1]))
    gb = core.resample_by_fraction(b, float(a["d"].iloc[-1]))
    delta = core.compute_delta(ga["t"], gb["t"])
    assert delta[0] == pytest.approx(0.0, abs=1e-9)
    assert (delta[1:] > 0).all()  # B is behind everywhere
    assert delta[-1] == pytest.approx(61.2 - 60.0, abs=0.01)
    # swapping the sides flips the sign
    assert core.compute_delta(gb["t"], ga["t"])[-1] == pytest.approx(-(61.2 - 60.0), abs=0.01)


# ----------------------------------------------------------------------
# Lap selection helpers
# ----------------------------------------------------------------------
def test_qualifying_part_bounds_dict_and_list_forms():
    stream = [
        # Initial snapshot: Series is a list.
        {"_timestamp": 10.0, "Series": [{"Utc": "2025-01-01T10:00:00Z", "QualifyingPart": 1}]},
        # Later entries: Series is a dict keyed by index.
        {"_timestamp": "00:20:00.000", "Series": {"1": {"QualifyingPart": 2}}},
        {"_timestamp": 2400.0, "Series": {"2": {"QualifyingPart": 3}}},
        # Noise the parser must ignore.
        {"_timestamp": 2500.0, "StatusSeries": {"3": {"SessionStatus": "Started"}}},
        {"_timestamp": 2600.0, "Series": {"4": {"QualifyingPart": 1}}},  # repeat must not override
    ]
    bounds = core.qualifying_part_bounds(stream)
    assert sorted(bounds) == [1, 2, 3]
    assert bounds[1] == (10.0, 1200.0)
    assert bounds[2] == (1200.0, 2400.0)
    assert bounds[3][0] == 2400.0 and bounds[3][1] == float("inf")


def test_qualifying_part_bounds_series_dict_form_single_part_and_empty():
    bounds = core.qualifying_part_bounds([{"_timestamp": 5.0, "Series": {"1": {"QualifyingPart": 1}}}])
    assert bounds == {1: (5.0, float("inf"))}
    assert core.qualifying_part_bounds([]) == {}
    assert core.qualifying_part_bounds(None) == {}


_RECORDS = [
    {"lap": 1, "time_s": 0.0, "timestamp_s": 100.0, "pit_out": True},
    {"lap": 2, "time_s": 90.5, "timestamp_s": 190.5},
    {"lap": 3, "time_s": 88.25, "timestamp_s": 278.75},
    {"lap": 4, "time_s": 85.0, "timestamp_s": 400.0, "pit_in": True},   # fastest but a pit lap
    {"lap": 5, "time_s": 89.0, "timestamp_s": 489.0},
]


def test_window_for_lap():
    assert core.window_for_lap(_RECORDS, 3) == (pytest.approx(190.5), 278.75, 88.25)
    assert core.window_for_lap(_RECORDS, 1) is None   # untimed lap
    assert core.window_for_lap(_RECORDS, 99) is None


def test_best_window_in_range_skips_pit_laps_and_is_half_open():
    start, end, lt, lap = core.best_window_in_range(_RECORDS, 0.0, 1000.0)
    assert (lap, lt) == (3, 88.25)                     # lap 4 is faster but a pit lap
    assert start == pytest.approx(278.75 - 88.25) and end == 278.75
    # only laps completed in [start, end) count
    assert core.best_window_in_range(_RECORDS, 0.0, 278.75)[3] == 2
    assert core.best_window_in_range(_RECORDS, 278.75, 1000.0)[3] == 3
    assert core.best_window_in_range(_RECORDS, 1000.0, 2000.0) is None


def test_lap_number_for_window():
    assert core.lap_number_for_window(_RECORDS, 278.75) == 3
    assert core.lap_number_for_window(_RECORDS, 279.4) == 3      # within 1 s tolerance
    assert core.lap_number_for_window(_RECORDS, 300.0) is None


# ----------------------------------------------------------------------
# Corners and sections
# ----------------------------------------------------------------------
def _dipped_grid():
    tr = _trace(60.0, speed_fn=dipped_speed())
    return core.resample_by_fraction(tr, float(tr["d"].iloc[-1]))


def test_place_corners_projects_circuit_corners_onto_the_path():
    grid = _dipped_grid()
    length = float(grid["distance"][-1])
    # A quarter and a half way round the circle; ``distance_m`` deliberately wrong to prove x/y wins.
    radius_raw = 477.5 * 10.0
    circuit = [
        {"number": 3, "x": 0.0, "y": radius_raw, "distance_m": 10.0},
        {"number": 7, "x": -radius_raw, "y": 0.0, "distance_m": 20.0},
    ]
    corners = core.place_corners(grid, circuit)
    assert [c["number"] for c in corners] == [3, 7]
    # position is uniform in time, so the quarter point is at 25% of *time*; read the expected
    # distance from the grid itself instead of assuming constant speed.
    for c, frac in zip(corners, (0.25, 0.5)):
        i = int(np.argmin(np.abs(grid["t"] - 60.0 * frac)))
        assert c["distance_m"] == pytest.approx(float(grid["distance"][i]), abs=length * 0.01)
    assert corners == sorted(corners, key=lambda c: c["distance_m"])


def test_place_corners_falls_back_to_distance_m_then_drops_out_of_range():
    grid = _dipped_grid()
    length = float(grid["distance"][-1])
    corners = core.place_corners(grid, [
        {"number": 1, "distance_m": 500.0},                       # no x/y -> uses distance_m
        {"number": 2, "x": 1e9, "y": 1e9, "distance_m": 900.0},   # too far from the path -> distance_m
        {"number": 3, "distance_m": length + 500.0},              # outside the lap -> dropped
        {"number": None, "distance_m": 100.0},                    # no number -> dropped
    ])
    assert [(c["number"], c["distance_m"]) for c in corners] == [(1, 500.0), (2, 900.0)]


def test_place_corners_detects_from_speed_when_no_circuit():
    grid = _dipped_grid()
    for circuit in (None, []):
        corners = core.place_corners(grid, circuit)
        assert [c["number"] for c in corners] == [1, 2, 3]   # three dips, numbered in track order
        assert [c["distance_m"] for c in corners] == sorted(c["distance_m"] for c in corners)


def _corners(*ds):
    return [{"number": i, "distance_m": d} for i, d in enumerate(ds, start=1)]


def test_build_sections_merges_close_corners_and_covers_the_lap():
    length = 3000.0
    sections = core.build_sections(_corners(500.0, 560.0, 1500.0, 2500.0), length)
    assert sections[0]["start_m"] == 0.0
    assert sections[-1]["end_m"] == pytest.approx(length)
    for prev, nxt in zip(sections, sections[1:]):
        assert nxt["start_m"] == pytest.approx(prev["end_m"], abs=1.0)   # contiguous
        assert nxt["end_m"] > nxt["start_m"]
    corner_groups = [s["corners"] for s in sections if s["corners"]]
    assert corner_groups == [[1, 2], [3], [4]]                            # 60 m apart -> one section
    assert any(not s["corners"] for s in sections)                        # straights stay separate
    assert core.section_label([1, 2]) == "T1-T2"
    assert core.section_label([3]) == "T3"
    assert core.section_label([]) == "straight"


def test_build_sections_keeps_corners_120m_or_more_apart_separate():
    sections = core.build_sections(_corners(500.0, 700.0), 3000.0)
    assert [s["corners"] for s in sections if s["corners"]] == [[1], [2]]


def test_build_sections_without_corners_is_one_straight():
    assert core.build_sections([], 2500.0) == [{"start_m": 0.0, "end_m": 2500.0, "corners": []}]


def test_section_gains_sum_to_total_delta():
    length = 3000.0
    dist = np.linspace(0.0, length, core.GRID_POINTS)
    delta = 0.8 * (dist / length) + 0.15 * np.sin(dist / 300.0)   # wiggly, ends at ~0.8 + sin(10)*0.15
    sections = core.section_gains(core.build_sections(_corners(500.0, 560.0, 1500.0, 2500.0), length), dist, delta)
    assert sum(s["delta_change_s"] for s in sections) == pytest.approx(delta[-1] - delta[0], abs=0.01)
    assert all("delta_change_s" in s for s in sections)


# ----------------------------------------------------------------------
# Accelerations
# ----------------------------------------------------------------------
def test_compute_accelerations_lateral_g_on_a_circle():
    speed_ms, radius = 50.0, 300.0
    lap_time = 2 * np.pi * radius / speed_ms
    car = make_car(START, lap_time, constant_speed(speed_ms * 3.6))
    pos = make_circle_pos(START, lap_time, radius,
                          angle_fn=lambda rel: speed_ms * rel / radius)
    tr = core.build_lap_trace(car, pos, START, lap_time)
    grid = core.resample_by_fraction(tr, float(tr["d"].iloc[-1]))
    long_g, lat_g = core.compute_accelerations(grid)
    expected = speed_ms ** 2 / radius / core.G
    n = len(lat_g)
    middle = slice(int(n * 0.1), int(n * 0.9))       # smoothing pads the ends with edge values
    assert np.median(lat_g[middle]) == pytest.approx(expected, rel=0.25)   # counter-clockwise = left = positive
    assert np.max(np.abs(long_g[middle])) < 0.1      # constant speed
    assert np.max(np.abs(lat_g)) <= core.G_CLAMP


def test_compute_accelerations_is_clamped():
    n = core.GRID_POINTS
    s = np.linspace(0.0, 1000.0, n)
    speed = np.where(s < 500.0, 300.0, 20.0)         # 280 km/h lost in one grid step
    grid = {
        "own_distance": s, "speed": speed,
        "x": s * 10.0, "y": np.zeros(n),
    }
    long_g, lat_g = core.compute_accelerations(grid)
    assert np.nanmax(np.abs(long_g)) <= core.G_CLAMP + 1e-9
    assert np.nanmax(np.abs(lat_g)) <= core.G_CLAMP + 1e-9
    assert np.nanmax(np.abs(long_g)) == pytest.approx(core.G_CLAMP)   # the step really does hit the clamp


def test_compute_accelerations_without_position_has_no_lateral_g():
    n = core.GRID_POINTS
    s = np.linspace(0.0, 1000.0, n)
    grid = {"own_distance": s, "speed": np.full(n, 200.0), "x": np.full(n, np.nan), "y": np.full(n, np.nan)}
    _, lat_g = core.compute_accelerations(grid)
    assert np.isnan(lat_g).all()


# ----------------------------------------------------------------------
# Small formatters
# ----------------------------------------------------------------------
def test_format_lap_time():
    assert core.format_lap_time(102.526) == "1:42.526"
    assert core.format_lap_time(59.9) == "59.900"
    assert core.format_lap_time(60.0) == "1:00.000"
    assert core.format_lap_time(65.05) == "1:05.050"


def test_full_throttle_pct():
    assert core.full_throttle_pct(np.array([100.0, 100.0, 50.0, 0.0])) == 50.0
    assert core.full_throttle_pct(np.array([97.9, 98.0])) == 50.0


def test_braking_point_finds_the_start_of_the_zone():
    dist = np.arange(0.0, 1000.0, 5.0)
    brake = np.zeros_like(dist)
    brake[(dist >= 400) & (dist <= 480)] = 1
    assert core.braking_point(dist, brake, apex_d=500.0) == 400.0
    assert core.braking_point(dist, np.zeros_like(dist), apex_d=500.0) is None
