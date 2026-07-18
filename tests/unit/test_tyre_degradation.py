"""Offline unit tests for Tyre Degradation (V2).

Uses synthetic in-memory streams (no network, no Redis/Mongo) so the
lap<->stint join, filtering, linear fit, and fuel-correction math are exercised
deterministically. cached_or_generate's Mongo layer is monkeypatched off.
"""
from __future__ import annotations

import os

import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.analysis.v2 import tyre_degradation as td


# ---------------------------------------------------------------------------
# Synthetic fake store
# ---------------------------------------------------------------------------
def _make_timing_app_entry(driver_num, compound, total_laps, start_laps=0, stint_idx="0"):
    return {
        "_timestamp": "00:00:00.000",
        "Lines": {
            driver_num: {
                "Stints": {
                    stint_idx: {
                        "Compound": compound,
                        "TotalLaps": total_laps,
                        "StartLaps": start_laps,
                    }
                }
            }
        },
    }


class _FakeStore:
    """Feeds hand-built timing_data / timing_app_data / driver_list / track_status."""

    year = 2025
    identifier = 1
    session_name = "Race"
    event_name = "Synthetic Grand Prix"

    def __init__(self, timing_data, timing_app_data, drivers, track_status=None):
        self._timing = timing_data
        self._app = timing_app_data
        self._drivers = drivers
        self._track = track_status or []

    def timing_data(self):
        return self._timing

    def timing_app_data(self):
        return self._app

    def driver_list(self):
        return self._drivers

    def track_status(self):
        return self._track


def _linear_stint_store(slope=0.10, base=90.0, n_laps=10, compound="SOFT",
                        driver_num="44", tla="HAM"):
    """One driver, one stint: lap time = base + slope * (lap-1)."""
    timing = []
    for lap in range(1, n_laps + 1):
        t = base + slope * (lap - 1)
        timing.append({
            "_timestamp": f"00:{lap:02d}:00.000",
            "Lines": {driver_num: {
                "NumberOfLaps": lap,
                "LastLapTime": {"Value": f"{int(t // 60)}:{t % 60:06.3f}"},
            }},
        })
    app = [_make_timing_app_entry(driver_num, compound, total_laps=n_laps)]
    drivers = {driver_num: {"tla": tla, "team": "Ferrari", "color": "E80020"}}
    return _FakeStore(timing, app, drivers)


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setattr(
        "src.services.analysis.base.get_plot_data_from_mongo",
        lambda *a, **k: None,
    )


# ---------------------------------------------------------------------------
# Session validation
# ---------------------------------------------------------------------------
def test_assert_valid_session_rejects_qualifying():
    with pytest.raises(DataNotAvailableError):
        td._assert_valid_session("Q", 2025, 1)


def test_data_call_rejects_qualifying():
    with pytest.raises(DataNotAvailableError):
        td.TyreDegradationData()(2025, 1, "Q")


def test_assert_valid_session_accepts_race_and_sprint():
    td._assert_valid_session("R", 2025, 1)
    td._assert_valid_session("Sprint", 2025, 1)


# ---------------------------------------------------------------------------
# Lap <-> stint join
# ---------------------------------------------------------------------------
def test_lap_stint_join_assigns_compound_and_age():
    store = _linear_stint_store(slope=0.0, n_laps=8, compound="MEDIUM")
    payload = td._build_payload(store, False, None, 2025, 1, "R")
    assert len(payload) == 1
    comp = payload[0]
    assert comp["compound"] == "MEDIUM"
    ages = sorted(p["tyre_age"] for p in comp["points"])
    # Fresh tyre (StartLaps=0): completing race laps 1..8 -> tyre age 1..8.
    assert ages == list(range(1, 9))


def test_used_tyre_start_age_offsets_ages():
    driver_num = "44"
    timing = []
    for lap in range(1, 6):
        timing.append({
            "_timestamp": f"00:{lap:02d}:00.000",
            "Lines": {driver_num: {
                "NumberOfLaps": lap,
                "LastLapTime": {"Value": "1:30.000"},
            }},
        })
    # Tyre started with 3 laps already on it (StartLaps=3), TotalLaps=8 -> 5 laps.
    app = [_make_timing_app_entry(driver_num, "HARD", total_laps=8, start_laps=3)]
    drivers = {driver_num: {"tla": "HAM", "team": "Ferrari", "color": "E80020"}}
    store = _FakeStore(timing, app, drivers)
    payload = td._build_payload(store, False, None, 2025, 1, "R")
    ages = sorted(p["tyre_age"] for p in payload[0]["points"])
    # 5 race laps on a tyre that ended at life 8 -> ages 4,5,6,7,8.
    assert ages == [4, 5, 6, 7, 8]


# ---------------------------------------------------------------------------
# Filtering: pit laps and SC laps excluded
# ---------------------------------------------------------------------------
def test_out_lap_excluded():
    driver_num = "44"
    timing = []
    for lap in range(1, 7):
        line = {"NumberOfLaps": lap, "LastLapTime": {"Value": "1:30.000"}}
        if lap == 3:
            line["PitOut"] = True  # out-lap
        timing.append({"_timestamp": f"00:{lap:02d}:00.000", "Lines": {driver_num: line}})
    app = [_make_timing_app_entry(driver_num, "SOFT", total_laps=6)]
    drivers = {driver_num: {"tla": "HAM", "team": "Ferrari", "color": "E80020"}}
    store = _FakeStore(timing, app, drivers)
    payload = td._build_payload(store, False, None, 2025, 1, "R")
    laps_kept = {p["tyre_age"] for p in payload[0]["points"]}
    # Lap 3 is the out-lap; fresh tyre -> tyre_age 3 must be absent.
    assert 3 not in laps_kept
    assert 1 in laps_kept and 6 in laps_kept


def test_sc_laps_excluded():
    store = _linear_stint_store(slope=0.0, n_laps=8, compound="SOFT")
    # SC across laps 3-4.
    periods = [{"status": "SC", "start_lap": 3, "end_lap": 4}]
    non_green = td._non_green_laps(periods)
    assert non_green == {3, 4}

    store._track = []  # store has no real track status; feed periods via monkeypath
    # Directly test _collect_driver_points filtering with the SC set.
    from src.services.analysis.v2._race_helpers import extract_lap_times
    from src.services.analysis.v2._helpers import extract_stints_from_data
    recs = extract_lap_times(store)["44"]
    stints = extract_stints_from_data(store.timing_app_data(), "44")
    pts = td._collect_driver_points(recs, stints, non_green, "HAM")
    kept_laps = {p["lap"] for p in pts}
    assert 3 not in kept_laps and 4 not in kept_laps
    assert 1 in kept_laps and 8 in kept_laps


# ---------------------------------------------------------------------------
# Degradation fit
# ---------------------------------------------------------------------------
def test_deg_rate_matches_known_slope():
    store = _linear_stint_store(slope=0.08, base=90.0, n_laps=12, compound="SOFT")
    payload = td._build_payload(store, False, None, 2025, 1, "R")
    comp = payload[0]
    assert comp["deg_rate_s_per_lap"] == pytest.approx(0.08, abs=0.005)
    assert comp["r_squared"] == pytest.approx(1.0, abs=1e-6)
    assert comp["n_points"] == 12


def test_short_stint_excluded_from_fit_but_points_kept():
    # Only 3 clean laps -> below _MIN_STINT_LAPS_FOR_FIT (4): deg_rate None,
    # but points still present.
    store = _linear_stint_store(slope=0.10, n_laps=3, compound="HARD")
    payload = td._build_payload(store, False, None, 2025, 1, "R")
    comp = payload[0]
    assert comp["deg_rate_s_per_lap"] is None
    assert comp["n_points"] == 3


# ---------------------------------------------------------------------------
# Fuel correction math
# ---------------------------------------------------------------------------
def test_fuel_correction_math():
    store = _linear_stint_store(slope=0.0, base=90.0, n_laps=10, compound="SOFT")
    payload = td._build_payload(store, True, None, 2025, 1, "R")
    comp = payload[0]
    total_laps = 10
    for p in comp["points"]:
        laps_remaining = total_laps - p["tyre_age"]  # fresh tyre: race lap == tyre_age
        expected = round(90.0 - td.FUEL_EFFECT_S_PER_LAP * laps_remaining, 3)
        assert p["fuel_corrected_s"] == pytest.approx(expected, abs=1e-3)


# ---------------------------------------------------------------------------
# Driver filter
# ---------------------------------------------------------------------------
def test_unknown_driver_raises():
    store = _linear_stint_store()
    with pytest.raises(DataNotAvailableError) as exc:
        td._build_payload(store, False, "ZZZ", 2025, 1, "R")
    assert "ZZZ" in str(exc.value.reason) or "Unknown" in str(exc.value.reason)


def test_driver_filter_selects_one():
    store = _linear_stint_store(driver_num="44", tla="HAM")
    payload = td._build_payload(store, False, "HAM", 2025, 1, "R")
    assert payload
    drivers_in = {p["driver"] for c in payload for p in c["points"]}
    assert drivers_in == {"HAM"}


# ---------------------------------------------------------------------------
# Unknown compound bucketing
# ---------------------------------------------------------------------------
def test_unknown_compound_bucketed():
    store = _linear_stint_store(compound="EXPERIMENTAL", n_laps=6)
    payload = td._build_payload(store, False, None, 2025, 1, "R")
    assert payload[0]["compound"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# PNG rendering
# ---------------------------------------------------------------------------
def test_plot_renders_png(monkeypatch, tmp_path):
    store = _linear_stint_store(slope=0.08, n_laps=12)
    payload = td._build_payload(store, False, None, 2025, 1, "R")
    monkeypatch.chdir(tmp_path)
    out = td.TyreDegradationPlot._render(
        payload, 2025, "Synthetic Grand Prix", "Race", None, False
    )
    assert os.path.isfile(out)
    assert out.endswith(".png")


def test_plot_renders_png_fuel_corrected(monkeypatch, tmp_path):
    store = _linear_stint_store(slope=0.08, n_laps=12)
    payload = td._build_payload(store, True, None, 2025, 1, "R")
    monkeypatch.chdir(tmp_path)
    out = td.TyreDegradationPlot._render(
        payload, 2025, "Synthetic Grand Prix", "Race", None, True
    )
    assert os.path.isfile(out)
