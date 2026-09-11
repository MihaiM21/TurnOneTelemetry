"""Offline tests for the lap-range telemetry frame builder.

No network, Redis or Mongo: every test drives the pure builders in
``lap_frames`` directly, or a duck-typed store double. There are no
CarData/Position fixtures in ``tests/unit/fixtures``, so the raw stream shapes
are synthesized inline from the structures the feed actually publishes.
"""
import math

import pandas as pd
import pytest

from src.services.analysis.v2 import _helpers as helpers
from src.services.analysis.v2 import lap_frames as lf


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------
def test_grid_length_is_ceil_duration_times_hz():
    grid = lf.build_grid(100.0, 184.123, 10)
    assert len(grid) == math.ceil(84.123 * 10)


def test_grid_interval_is_uniform():
    grid = lf.build_grid(100.0, 184.123, 10)
    deltas = {round(b - a, 9) for a, b in zip(grid, grid[1:])}
    assert deltas == {0.1}


@pytest.mark.parametrize("hz", [1, 5, 10, 20])
def test_grid_uniform_at_every_supported_rate(hz):
    grid = lf.build_grid(0.0, 30.0, hz)
    deltas = {round(b - a, 9) for a, b in zip(grid, grid[1:])}
    assert deltas == {round(1.0 / hz, 9)}


def test_empty_window_yields_no_frames():
    assert lf.build_grid(100.0, 100.0, 10) == []


# ---------------------------------------------------------------------------
# Resampling: interpolate continuous fields, hold discrete ones
# ---------------------------------------------------------------------------
def test_linear_interpolation_at_known_midpoints():
    series = [(100.0, 0.0), (101.0, 100.0)]
    out = lf.resample_series(series, [100.0, 100.25, 100.5, 101.0])
    assert out == [0.0, 25.0, 50.0, 100.0]


def test_step_hold_never_averages_a_discrete_value():
    # Gear 4 -> 5 must never resample to 4.5, a gear the car never selected.
    out = lf.resample_series([(100.0, 4), (101.0, 5)], [100.0, 100.5, 101.0], hold=True)
    assert out == [4, 4, 5]


def test_values_before_and_after_coverage_are_null():
    series = [(100.0, 1.0), (100.5, 2.0)]
    assert lf.resample_series(series, [99.0, 101.0]) == [None, None]


def test_empty_series_resamples_to_all_null():
    assert lf.resample_series([], [1.0, 2.0]) == [None, None]


def test_exact_sample_hit_is_returned_verbatim():
    # An exact hit is trustworthy even when the next hole is huge.
    series = [(100.0, 7.0), (200.0, 9.0)]
    assert lf.resample_series(series, [100.0], max_gap_s=2.0) == [7.0]


# ---------------------------------------------------------------------------
# Gaps: never bridge a hole
# ---------------------------------------------------------------------------
def test_interpolation_does_not_bridge_a_gap():
    series = [(100.0, 10.0), (105.0, 60.0)]
    out = lf.resample_series(series, [100.0, 101.0, 102.5, 104.0, 105.0], max_gap_s=2.0)
    assert out == [10.0, None, None, None, 60.0]


def test_detect_gaps_flags_a_mid_window_dropout():
    gaps = lf.detect_gaps([100.0, 105.0], 100.0, 105.0, max_gap_s=2.0)
    assert gaps == [{"from": 100.0, "to": 105.0, "reason": "stream_dropout"}]


def test_detect_gaps_flags_missing_head_and_tail():
    gaps = lf.detect_gaps([105.0, 105.5], 100.0, 110.0, max_gap_s=2.0)
    reasons = [g["reason"] for g in gaps]
    assert reasons == ["no_data", "no_data"]
    assert gaps[0]["from"] == 100.0 and gaps[-1]["to"] == 110.0


def test_detect_gaps_with_no_samples_covers_whole_window():
    assert lf.detect_gaps([], 10.0, 20.0) == [
        {"from": 10.0, "to": 20.0, "reason": "no_data"}
    ]


def test_dense_samples_report_no_gaps():
    times = [100.0 + i * 0.25 for i in range(40)]
    assert lf.detect_gaps(times, 100.0, 109.75, max_gap_s=2.0) == []


# ---------------------------------------------------------------------------
# Whole-driver frame build
# ---------------------------------------------------------------------------
LAPS = [
    {"number": 10, "lap_time_s": 10.0, "start_s": 100.0, "end_s": 110.0},
    {"number": 11, "lap_time_s": 10.0, "start_s": 110.0, "end_s": 120.0},
]


def _series(times):
    """Full series dict for a driver whose position is known at ``times``."""
    series = {f: [] for f in lf.LERP_FIELDS + lf.HOLD_FIELDS}
    series["x"] = [(t, float(t)) for t in times]
    series["y"] = [(t, -float(t)) for t in times]
    series["z"] = [(t, 0.0) for t in times]
    series["throttle"] = [(t, 50.0) for t in times]
    series["gear"] = [(t, 6) for t in times]
    series["status"] = [(t, "OnTrack") for t in times]
    return series


def _dense(start, end, step=0.25):
    n = int(round((end - start) / step)) + 1
    return [start + i * step for i in range(n)]


def test_build_frames_covers_the_range_at_the_requested_rate():
    frames, _ = lf.build_frames(_series(_dense(100.0, 120.0)), LAPS, 100.0, 120.0, 10)
    assert len(frames) == 200
    assert frames[0]["t"] == 0.0
    assert frames[0]["session_time"] == 100.0


def test_frames_carry_both_clocks():
    frames, _ = lf.build_frames(_series(_dense(100.0, 120.0)), LAPS, 100.0, 120.0, 10)
    for fr in frames[:20]:
        assert round(fr["session_time"] - fr["t"], 6) == 100.0


def test_gap_frames_are_null_and_marked_nodata():
    times = _dense(100.0, 102.0) + _dense(106.0, 120.0)
    frames, gaps = lf.build_frames(_series(times), LAPS, 100.0, 120.0, 10)

    holed = [f for f in frames if f["x"] is None]
    assert holed, "expected null frames inside the hole"
    assert all(f["status"] == "NoData" for f in holed)
    assert all(f["y"] is None and f["throttle"] is None for f in holed)
    assert any(g["reason"] == "stream_dropout" for g in gaps)


def test_gaps_are_reported_on_the_lap_relative_clock():
    times = _dense(100.0, 102.0) + _dense(106.0, 120.0)
    _, gaps = lf.build_frames(_series(times), LAPS, 100.0, 120.0, 10)
    assert gaps[0]["from"] == 2.0 and gaps[0]["to"] == 6.0


def test_discrete_channels_stay_integral():
    frames, _ = lf.build_frames(_series(_dense(100.0, 120.0)), LAPS, 100.0, 120.0, 10)
    assert all(f["gear"] is None or isinstance(f["gear"], int) for f in frames)


def test_every_frame_is_assigned_to_its_lap():
    frames, _ = lf.build_frames(_series(_dense(100.0, 120.0)), LAPS, 100.0, 120.0, 10)
    assert {f["lap"] for f in frames} == {10, 11}
    assert all(f["lap"] == 10 for f in frames if f["session_time"] < 110.0)


def test_distance_resets_at_each_lap_boundary():
    frames, _ = lf.build_frames(_series(_dense(100.0, 120.0)), LAPS, 100.0, 120.0, 10)
    first_of_lap_11 = next(f for f in frames if f["lap"] == 11)
    assert first_of_lap_11["d"] == 0.0


def test_distance_is_monotonic_within_a_lap():
    frames, _ = lf.build_frames(_series(_dense(100.0, 120.0)), LAPS, 100.0, 120.0, 10)
    lap10 = [f["d"] for f in frames if f["lap"] == 10]
    assert all(b >= a for a, b in zip(lap10, lap10[1:]))


# ---------------------------------------------------------------------------
# Lap indexing
# ---------------------------------------------------------------------------
def test_frame_ranges_partition_the_frame_list_contiguously():
    frames, _ = lf.build_frames(_series(_dense(100.0, 120.0)), LAPS, 100.0, 120.0, 10)
    indexed = lf.index_laps(frames, LAPS)

    assert indexed[0]["frame_from"] == 0
    assert indexed[0]["frame_to"] == indexed[1]["frame_from"]
    assert indexed[-1]["frame_to"] == len(frames)


def test_index_laps_preserves_lap_metadata():
    frames, _ = lf.build_frames(_series(_dense(100.0, 120.0)), LAPS, 100.0, 120.0, 10)
    indexed = lf.index_laps(frames, LAPS)
    assert indexed[0]["lap_time_s"] == 10.0 and indexed[0]["number"] == 10


def test_lap_with_no_frames_gets_null_bounds():
    absent = [{"number": 99, "lap_time_s": 1.0, "start_s": 900.0, "end_s": 901.0}]
    assert lf.index_laps([], absent)[0]["frame_from"] is None


# ---------------------------------------------------------------------------
# Encoding and precision
# ---------------------------------------------------------------------------
def test_columnar_round_trips_every_field():
    times = _dense(100.0, 102.0) + _dense(106.0, 120.0)
    frames, _ = lf.build_frames(_series(times), LAPS, 100.0, 120.0, 10)
    columns = lf.to_columnar(frames)

    assert set(columns) == set(lf.FRAME_FIELDS)
    for field in lf.FRAME_FIELDS:
        assert columns[field] == [f.get(field) for f in frames]


def test_columnar_preserves_nulls():
    times = _dense(100.0, 102.0) + _dense(106.0, 120.0)
    frames, _ = lf.build_frames(_series(times), LAPS, 100.0, 120.0, 10)
    assert None in lf.to_columnar(frames)["x"]


def test_columnar_of_empty_frames_is_empty_arrays():
    assert lf.to_columnar([]) == {f: [] for f in lf.FRAME_FIELDS}


@pytest.mark.parametrize("precision,expected", [(0, 3.0), (2, 3.14), (6, 3.141593)])
def test_round_frames_honours_precision(precision, expected):
    out = lf.round_frames([{"x": 3.14159265}], precision)
    assert out[0]["x"] == expected


def test_round_frames_leaves_non_floats_alone():
    out = lf.round_frames([{"gear": 5, "status": "OnTrack", "x": None}], 2)
    assert out[0] == {"gear": 5, "status": "OnTrack", "x": None}


def test_precision_none_is_a_no_op():
    out = lf.round_frames([{"x": 3.14159265}], None)
    assert out[0]["x"] == 3.14159265


# ---------------------------------------------------------------------------
# Cost budget
# ---------------------------------------------------------------------------
def test_a_reasonable_animation_request_fits():
    cost = lf.estimate_cost(2, 11, 84.0, 10)
    assert cost["within_budget"]


def test_whole_race_for_the_whole_field_is_rejected():
    assert not lf.estimate_cost(20, 57, 84.0, 10)["within_budget"]


def test_unit_cap_error_names_the_computed_cost():
    with pytest.raises(ValueError) as exc:
        lf.check_budget(lf.estimate_cost(20, 10, 5.0, 1), 1)
    assert "200 driver-laps" in str(exc.value)
    assert str(lf.MAX_DRIVER_LAPS) in str(exc.value)


def test_frame_budget_error_names_the_cost_and_suggests_an_hz():
    # 80 driver-laps clears the unit cap, so this isolates the frame budget.
    with pytest.raises(ValueError) as exc:
        lf.check_budget(lf.estimate_cost(2, 40, 84.0, 20), 20)
    message = str(exc.value)
    assert "134400 frames" in message
    assert str(lf.MAX_FRAMES) in message
    assert "hz=" in message


def test_lowering_hz_brings_an_over_budget_request_back_in():
    assert not lf.estimate_cost(2, 40, 84.0, 20)["within_budget"]
    assert lf.estimate_cost(2, 40, 84.0, 5)["within_budget"]


# ---------------------------------------------------------------------------
# Series construction from the raw stream frames
# ---------------------------------------------------------------------------
def test_build_series_splits_channels_and_position_into_per_field_series():
    # Columns are the feed's raw channel keys: 0=RPM, 2=Speed, 3=Gear,
    # 4=Throttle, 5=Brake, 45=DRS.
    tel = pd.DataFrame({"Time": [1.0, 2.0], "2": [300.0, 305.0],
                        "3": [7.0, 8.0], "45": [8.0, 8.0], "0": [11000.0, 11500.0]})
    pos = pd.DataFrame({"Time": [1.0, 2.0], "X": [10.0, 20.0], "Y": [-5.0, -6.0],
                        "Z": [1.0, 1.0], "Status": ["OnTrack", "OnTrack"]})

    series = lf.build_series(tel, pos)
    assert series["speed"] == [(1.0, 300.0), (2.0, 305.0)]
    assert series["gear"] == [(1.0, 7.0), (2.0, 8.0)]
    assert series["drs"] == [(1.0, 8.0), (2.0, 8.0)]
    assert series["rpm"] == [(1.0, 11000.0), (2.0, 11500.0)]
    assert series["x"] == [(1.0, 10.0), (2.0, 20.0)]
    assert series["status"] == [(1.0, "OnTrack"), (2.0, "OnTrack")]


def test_channel_map_reflects_the_actual_feed_not_the_legacy_table():
    # Regression guard: helpers.CHANNEL_NAMES mislabels '3' as RPM and '45' as
    # Gear. This module must not inherit that.
    assert lf._CHANNEL_TO_FIELD["3"] == "gear"
    assert lf._CHANNEL_TO_FIELD["45"] == "drs"
    assert lf._CHANNEL_TO_FIELD["0"] == "rpm"
    assert "47" not in lf._CHANNELS


def test_build_series_tolerates_missing_streams():
    series = lf.build_series(None, None)
    assert all(v == [] for v in series.values())
    assert lf.build_series(pd.DataFrame(), pd.DataFrame())["x"] == []


def test_build_series_drops_nan_channel_values():
    tel = pd.DataFrame({"Time": [1.0, 2.0], "2": [300.0, float("nan")]})
    assert lf.build_series(tel, None)["speed"] == [(1.0, 300.0)]


# ---------------------------------------------------------------------------
# Single-pass stream scanning
# ---------------------------------------------------------------------------
def _car_entry(t, utc, drivers):
    return {"_timestamp": t, "Entries": [{"Utc": utc, "Cars": {
        d: {"Channels": {"0": 11000, "2": 300, "3": 7, "4": 100, "5": 0, "45": 8}}
        for d in drivers}}]}


def _pos_entry(t, utc, drivers):
    return {"_timestamp": t, "Position": [{"Timestamp": utc, "Entries": {
        d: {"Status": "OnTrack", "X": 100, "Y": -200, "Z": 5} for d in drivers}}]}


class _CountingStore:
    """Counts how many times each raw stream is walked."""

    base_url = "http://example.invalid/"
    client = None

    def __init__(self, drivers=("1", "44", "16"), n=200):
        self.car_calls = 0
        self.pos_calls = 0
        self._car, self._pos = [], []
        for i in range(n):
            secs = i * 0.25
            stamp = f"00:00:{secs:06.3f}" if secs < 60 else f"00:{int(secs // 60):02d}:{secs % 60:06.3f}"
            utc = f"2025-03-16T15:00:{secs:06.3f}Z" if secs < 60 else None
            if utc is None:
                continue
            self._car.append(_car_entry(stamp, utc, drivers))
            self._pos.append(_pos_entry(stamp, utc, drivers))

    def car_data(self):
        self.car_calls += 1
        return self._car

    def position_data(self):
        self.pos_calls += 1
        return self._pos


def test_channel_window_scans_the_stream_once_for_many_drivers():
    store = _CountingStore()
    out = helpers.extract_channels_window(
        None, None, ["1", "44", "16"], 0.0, 50.0, channels=lf._CHANNELS, store=store,
    )
    assert store.car_calls == 1
    assert set(out) == {"1", "44", "16"}
    assert all(not df.empty for df in out.values())


def test_position_window_scans_the_stream_once_for_many_drivers():
    store = _CountingStore()
    out = helpers.extract_positions_window(None, None, ["1", "44", "16"], 0.0, 50.0, store=store)
    assert store.pos_calls == 1
    assert "Status" in out["1"].columns


def test_window_scanners_return_a_key_for_every_requested_driver():
    store = _CountingStore()
    out = helpers.extract_channels_window(None, None, ["1", "99"], 0.0, 50.0, store=store)
    assert set(out) == {"1", "99"}
    assert out["99"].empty


def test_window_scanner_time_axis_is_absolute_session_seconds():
    store = _CountingStore()
    out = helpers.extract_positions_window(None, None, ["1"], 10.0, 20.0, store=store)
    times = out["1"]["Time"].tolist()
    assert min(times) >= 10.0 - helpers.WINDOW_MARGIN_S
    assert max(times) <= 20.0 + helpers.WINDOW_MARGIN_S


def test_window_scanners_fail_open_on_a_broken_stream():
    class _Broken:
        base_url = ""
        client = None

        def car_data(self):
            raise RuntimeError("stream exploded")

        def position_data(self):
            raise RuntimeError("stream exploded")

    out = helpers.extract_channels_window(None, None, ["1"], 0.0, 10.0, store=_Broken())
    assert out["1"].empty
    out = helpers.extract_positions_window(None, None, ["1"], 0.0, 10.0, store=_Broken())
    assert out["1"].empty


# ---------------------------------------------------------------------------
# Single-driver wrappers keep their historical contract
# ---------------------------------------------------------------------------
def test_per_lap_wrapper_rebases_to_a_zero_based_lap_clock():
    store = _CountingStore()
    df = helpers.extract_position_for_lap(None, None, "1", 10.0, 20.0, store=store)
    assert not df.empty
    assert df["Time"].min() >= 0.0
    assert df["Time"].max() <= 10.0


def test_per_lap_wrapper_keeps_the_historical_column_set():
    store = _CountingStore()
    df = helpers.extract_position_for_lap(None, None, "1", 10.0, 20.0, store=store)
    assert list(df.columns) == ["Time", "X", "Y", "Z"]


def test_per_lap_telemetry_wrapper_keeps_legacy_channel_names():
    # The historical wrapper still labels via CHANNEL_NAMES -- existing
    # endpoints publish those column names, so it must not change here.
    store = _CountingStore()
    df = helpers.extract_telemetry_for_lap(
        None, None, "1", 10.0, 20.0, channels=["2", "45"], store=store,
    )
    assert set(df.columns) == {"Time", "Speed", "Gear"}


def test_raw_names_bypasses_the_legacy_channel_table():
    store = _CountingStore()
    out = helpers.extract_channels_window(
        None, None, ["1"], 0.0, 50.0, channels=["2", "3", "45"], store=store,
        raw_names=True,
    )
    assert set(out["1"].columns) == {"Time", "2", "3", "45"}


def test_per_lap_wrapper_on_an_absent_driver_is_empty():
    store = _CountingStore()
    assert helpers.extract_position_for_lap(None, None, "99", 10.0, 20.0, store=store).empty


# ---------------------------------------------------------------------------
# Lap windows
# ---------------------------------------------------------------------------
class _LapStore:
    year = 2025
    identifier = 16
    session_name = "R"
    event_name = "Italian Grand Prix"
    round_nr = 16

    def lap_times(self):
        return {"1": [
            {"lap": 10, "time_s": 84.1, "timestamp_s": 3485.3, "pit_in": False, "pit_out": False},
            {"lap": 11, "time_s": 83.9, "timestamp_s": 3569.2, "pit_in": True, "pit_out": False},
            {"lap": 12, "time_s": 0.0, "timestamp_s": 0.0},          # unusable record
        ]}

    def positions_by_lap(self):
        return {"1": [{"lap": 10, "position": 3}, {"lap": 11, "position": 4}]}

    def stints(self):
        return {"1": [{"stint_number": 2, "compound": "MEDIUM",
                       "start_lap": 8, "end_lap": 20, "tyre_life_end": 15}]}

    def driver_list(self):
        return {"1": {"tla": "VER", "team": "Red Bull Racing", "color": "3671C6",
                      "name": "Max Verstappen"}}


def test_lap_windows_use_the_canonical_start_end_formula():
    windows = lf.lap_windows(_LapStore(), "1", 10, 11)
    assert windows[0]["start_s"] == pytest.approx(3485.3 - 84.1)
    assert windows[0]["end_s"] == 3485.3


def test_consecutive_lap_windows_are_contiguous():
    windows = lf.lap_windows(_LapStore(), "1", 10, 11)
    assert windows[0]["end_s"] == windows[1]["start_s"]


def test_lap_windows_attach_tyre_and_position_context():
    window = lf.lap_windows(_LapStore(), "1", 10, 10)[0]
    assert window["compound"] == "MEDIUM"
    assert window["tyre_life"] == 15
    assert window["position"] == 3


def test_lap_windows_skip_unusable_records():
    assert [w["number"] for w in lf.lap_windows(_LapStore(), "1", 10, 12)] == [10, 11]


def test_lap_windows_carry_pit_flags():
    assert lf.lap_windows(_LapStore(), "1", 11, 11)[0]["pit_in"] is True


def test_resolve_driver_rejects_an_unknown_tla():
    from src.core.exceptions import DataNotAvailableError

    with pytest.raises(DataNotAvailableError):
        helpers.resolve_driver(_LapStore(), "HAM")
