"""Unit tests for the ``tables``-tier dataset export builders.

A ``_FakeStore`` stands in for :class:`SessionDataStore`, exposing the same
accessor surface the builders consume (``timing_data``, ``stints``,
``pit_stops``, ``track_status_periods``, ``lap_times``, ``weather_data``,
``race_control``, ``driver_list``, ``session_info``, ``is_session_complete``,
``base_url``, ``client``). No network or MongoDB is involved.
"""

import pandas as pd
import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.dataset_export import builders
from src.services.dataset_export.schema import TABLES, tables_for_tier

TARGET_R = (2024, 1, "Test Grand Prix", "R")
TARGET_Q = (2024, 1, "Test Grand Prix", "Q")
TARGET_FP1 = (2024, 1, "Test Grand Prix", "FP1")


def _e(ts, num, **line):
    return {"_timestamp": ts, "Lines": {num: line}}


def _sector(idx, value):
    return {"Sectors": {str(idx): {"Value": value}}}


# Driver "1": two laps (28.000/35.000/27.500 -> 1:30.500, then a pit-in lap at
# 1:31.000). Driver "44": one lap at 1:33.000. Three laps total.
_TIMING_ENTRIES = [
    _e("00:01:00.000", "1", Position="1"),
    _e("00:01:30.000", "1", **_sector(0, "28.000")),
    _e("00:02:05.000", "1", **_sector(1, "35.000")),
    _e("00:02:30.500", "1", Sectors={"2": {"Value": "27.500"}},
       LastLapTime={"Value": "1:30.500", "PersonalFastest": True, "OverallFastest": True},
       NumberOfLaps=1, Position="1"),
    _e("00:02:31.000", "1", InPit=True),
    _e("00:03:00.000", "1", **_sector(0, "28.500")),
    _e("00:03:35.000", "1", **_sector(1, "35.500")),
    _e("00:04:02.000", "1", Sectors={"2": {"Value": "27.000"}},
       LastLapTime={"Value": "1:31.000"}, NumberOfLaps=2, Position="1"),
    _e("00:01:00.000", "44", Position="2", GapToLeader="+2.500"),
    _e("00:01:31.000", "44", **_sector(0, "29.000")),
    _e("00:02:07.000", "44", **_sector(1, "36.000")),
    _e("00:02:33.000", "44", Sectors={"2": {"Value": "28.000"}},
       LastLapTime={"Value": "1:33.000"}, NumberOfLaps=1, Position="2"),
]

_PIT_STOPS = {"1": [{"lap": 2, "pit_lane_time_s": 23.5, "stop_n": 1}]}

# Shape of the *derived* SessionDataStore.lap_times() accessor
# (_race_helpers._compute_lap_times): {lap, time_s, timestamp_s, pit_in, pit_out}.
# Distinct from dataset_export.laps.extract_lap_records's richer per-lap shape.
_LAP_TIMES = {
    "1": [
        {"lap": 1, "time_s": 90.5, "timestamp_s": 150.5, "pit_in": False, "pit_out": False},
        {"lap": 2, "time_s": 91.0, "timestamp_s": 242.0, "pit_in": True, "pit_out": False},
    ],
    "44": [
        {"lap": 1, "time_s": 93.0, "timestamp_s": 153.0, "pit_in": False, "pit_out": False},
    ],
}

# Driver "1": a fresh tyre stint (tyre_life_start == 0 -> is_new). Driver "44":
# a stint that started on a used set (tyre_life_start == 2 -> not new).
_STINTS = {
    "1": [{"stint_number": 1, "compound": "SOFT", "start_lap": 1, "end_lap": 2,
           "lap_count": 2, "tyre_life_end": 2}],
    "44": [{"stint_number": 1, "compound": "MEDIUM", "start_lap": 1, "end_lap": 1,
            "lap_count": 1, "tyre_life_end": 3}],
}

_TRACK_STATUS_PERIODS = [
    {"status": "GREEN", "start_lap": 1, "end_lap": 1, "start_time_s": 0.0, "end_time_s": 150.5},
    {"status": "VSC", "start_lap": 2, "end_lap": 2, "start_time_s": 150.5, "end_time_s": None},
]

_WEATHER = [
    {"_timestamp": "00:00:10.000", "AirTemp": "25.3", "TrackTemp": "32.1", "Humidity": "45",
     "Pressure": "1013.2", "WindSpeed": "1.5", "WindDirection": "180", "Rainfall": "0"},
    {"_timestamp": "00:10:10.000", "AirTemp": "25.5", "TrackTemp": "33.0", "Humidity": "44",
     "Pressure": "1013.0", "WindSpeed": "2.0", "WindDirection": "190", "Rainfall": "1"},
]

_RACE_CONTROL = [
    {"Utc": "2024-01-01T12:02:30Z", "Lap": 2, "Category": "Other",
     "Message": "CAR 1 (VER) TIME 1:31.000 DELETED - TRACK LIMITS AT TURN 4 LAP 2",
     "Flag": None, "Scope": None, "Sector": None, "RacingNumber": "1"},
    {"Utc": "2024-01-01T12:03:00Z", "Lap": 2, "Category": "Flag", "Message": "YELLOW FLAG",
     "Flag": "YELLOW", "Scope": "Track", "Sector": None, "RacingNumber": None},
]

_DRIVER_LIST = {
    "1": {"tla": "VER", "team": "Red Bull Racing", "color": "#3671C6",
          "name": "Max Verstappen", "line": 1, "racing_number": "1"},
    "44": {"tla": "HAM", "team": "Mercedes", "color": "#27F4D2",
           "name": "Lewis Hamilton", "line": 2, "racing_number": "44"},
}

_SESSION_INFO = {
    "Name": "Race",
    "StartDate": "2024-01-01T12:00:00",
    "EndDate": "2024-01-01T14:00:00",
    "GmtOffset": "00:00:00",
    "Path": "2024/2024-01-01_Test_Grand_Prix/2024-01-01_Race/",
    "Meeting": {
        "Name": "Test Grand Prix",
        "OfficialName": "FORMULA 1 TEST GRAND PRIX 2024",
        "Location": "Test City",
        "Country": {"Name": "Testland"},
        "Circuit": {"Key": 999, "ShortName": "Test"},
    },
}


class _FakeStore:
    """Stand-in for SessionDataStore backed entirely by in-memory fixtures."""

    def __init__(self, fail_weather: bool = False, fail_timing_app_data: bool = False):
        self._fail_weather = fail_weather
        self._fail_timing_app_data = fail_timing_app_data
        self.year = 2024
        self.identifier = "Test Grand Prix"
        self.session_name = "Race"
        self.event_name = "Test Grand Prix"
        self.official_name = "FORMULA 1 TEST GRAND PRIX 2024"
        self.base_url = "https://example.test/2024/Test/Race/"
        self.client = object()

    def timing_data(self):
        return _TIMING_ENTRIES

    def timing_app_data(self):
        if self._fail_timing_app_data:
            raise DataNotAvailableError(reason="TimingAppData.jsonStream not published (404)")
        return []

    def stints(self):
        self.timing_app_data()
        return _STINTS

    def pit_stops(self):
        return _PIT_STOPS

    def track_status_periods(self):
        return _TRACK_STATUS_PERIODS

    def lap_times(self):
        return _LAP_TIMES

    def weather_data(self):
        if self._fail_weather:
            raise DataNotAvailableError(reason="WeatherData.jsonStream not published (404)")
        return _WEATHER

    def race_control(self):
        return _RACE_CONTROL

    def driver_list(self):
        return _DRIVER_LIST

    def session_info(self):
        return _SESSION_INFO

    def is_session_complete(self):
        return True


def _assert_schema(df: pd.DataFrame, name: str):
    spec = TABLES[name]
    assert list(df.columns) == spec.column_names
    for col in spec.columns:
        assert str(df[col.name].dtype) == col.dtype, f"{name}.{col.name}"


def _assert_keys_filled(df: pd.DataFrame, target):
    year, round_nr, _gp, session = target
    assert (df["year"] == year).all()
    assert (df["round"] == round_nr).all()
    assert (df["session"] == session).all()
    assert (df["session_key"] == f"{year}_{round_nr:02d}_{session}").all()


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

def test_build_sessions():
    store = _FakeStore()
    df = builders.build_sessions(store, TARGET_R)
    _assert_schema(df, "sessions")
    assert len(df) == 1
    _assert_keys_filled(df, TARGET_R)
    row = df.iloc[0]
    assert row["gp_name"] == "Test Grand Prix"
    assert row["official_name"] == "FORMULA 1 TEST GRAND PRIX 2024"
    assert row["circuit_key"] == 999
    assert row["circuit_short_name"] == "Test"
    assert row["country"] == "Testland"
    assert row["session_type"] == "Race"
    assert row["total_laps"] == 2  # driver "1"'s max lap
    assert bool(row["is_complete"]) is True


def test_build_sessions_falls_back_without_session_info():
    class _NoInfoStore(_FakeStore):
        def session_info(self):
            raise DataNotAvailableError(reason="SessionInfo.json not published (404)")

    df = builders.build_sessions(_NoInfoStore(), TARGET_R)
    assert df.iloc[0]["gp_name"] == "Test Grand Prix"


# ---------------------------------------------------------------------------
# drivers
# ---------------------------------------------------------------------------

def test_build_drivers():
    store = _FakeStore()
    df = builders.build_drivers(store, TARGET_R)
    _assert_schema(df, "drivers")
    _assert_keys_filled(df, TARGET_R)
    assert set(df["driver_number"]) == {"1", "44"}
    ver = df[df["driver_number"] == "1"].iloc[0]
    assert ver["driver_code"] == "VER"
    assert ver["full_name"] == "Max Verstappen"
    assert ver["team_name"] == "Red Bull Racing"
    assert ver["line"] == 1


# ---------------------------------------------------------------------------
# laps
# ---------------------------------------------------------------------------

def test_build_laps():
    store = _FakeStore()
    df = builders.build_laps(store, TARGET_R)
    _assert_schema(df, "laps")
    _assert_keys_filled(df, TARGET_R)
    assert len(df) == 3

    ver_laps = df[df["driver_number"] == "1"].sort_values("lap")
    assert list(ver_laps["lap"]) == [1, 2]
    assert list(ver_laps["driver_code"]) == ["VER", "VER"]

    lap1 = ver_laps.iloc[0]
    assert lap1["lap_time_s"] == pytest.approx(90.5)
    assert lap1["sector1_s"] == pytest.approx(28.0)
    assert lap1["sector2_s"] == pytest.approx(35.0)
    assert lap1["sector3_s"] == pytest.approx(27.5)
    assert lap1["compound"] == "SOFT"
    assert lap1["stint_number"] == 1
    assert bool(lap1["is_deleted"]) is False

    lap2 = ver_laps.iloc[1]
    assert lap2["lap_time_s"] == pytest.approx(91.0)
    assert bool(lap2["is_pit_in"]) is True
    assert bool(lap2["is_pit_lap"]) is True
    assert lap2["compound"] == "SOFT"
    assert bool(lap2["is_deleted"]) is True  # matched by the DELETED race-control message

    ham_lap = df[df["driver_number"] == "44"].iloc[0]
    assert ham_lap["driver_code"] == "HAM"
    assert ham_lap["compound"] == "MEDIUM"
    assert ham_lap["lap_time_s"] == pytest.approx(93.0)


def test_build_laps_survives_missing_stints_and_track_status():
    """Stints/track-status/RCM joins are best-effort; laps still build."""

    class _NoStintsStore(_FakeStore):
        def stints(self):
            raise DataNotAvailableError(reason="TimingAppData.jsonStream not published (404)")

        def track_status_periods(self):
            raise DataNotAvailableError(reason="TrackStatus.jsonStream not published (404)")

    df = builders.build_laps(_NoStintsStore(), TARGET_R)
    _assert_schema(df, "laps")
    assert len(df) == 3
    assert df["compound"].isna().all()
    assert df["track_status_label"].isna().all()


# ---------------------------------------------------------------------------
# stints
# ---------------------------------------------------------------------------

def test_build_stints():
    store = _FakeStore()
    df = builders.build_stints(store, TARGET_R)
    _assert_schema(df, "stints")
    _assert_keys_filled(df, TARGET_R)
    assert len(df) == 2

    ver = df[df["driver_number"] == "1"].iloc[0]
    assert ver["tyre_life_start"] == 0
    assert bool(ver["is_new"]) is True

    ham = df[df["driver_number"] == "44"].iloc[0]
    assert ham["tyre_life_start"] == 2
    assert bool(ham["is_new"]) is False


# ---------------------------------------------------------------------------
# pit_stops
# ---------------------------------------------------------------------------

def test_build_pit_stops():
    store = _FakeStore()
    df = builders.build_pit_stops(store, TARGET_R)
    _assert_schema(df, "pit_stops")
    _assert_keys_filled(df, TARGET_R)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["driver_number"] == "1"
    assert row["lap"] == 2
    assert row["pit_lane_time_s"] == pytest.approx(23.5)
    assert row["timestamp_s"] == pytest.approx(242.0)  # driver "1" lap 2's timestamp_end_s


# ---------------------------------------------------------------------------
# track_status
# ---------------------------------------------------------------------------

def test_build_track_status():
    store = _FakeStore()
    df = builders.build_track_status(store, TARGET_R)
    _assert_schema(df, "track_status")
    _assert_keys_filled(df, TARGET_R)
    assert len(df) == 2
    codes = dict(zip(df["status_label"], df["status_code"]))
    assert codes["GREEN"] == 1
    assert codes["VSC"] == 6


# ---------------------------------------------------------------------------
# weather
# ---------------------------------------------------------------------------

def test_build_weather():
    store = _FakeStore()
    df = builders.build_weather(store, TARGET_R)
    _assert_schema(df, "weather")
    _assert_keys_filled(df, TARGET_R)
    assert len(df) == 2
    row0 = df.iloc[0]
    assert row0["timestamp_s"] == pytest.approx(10.0)
    assert row0["air_temp"] == pytest.approx(25.3)
    assert bool(row0["rainfall"]) is False
    row1 = df.iloc[1]
    assert row1["timestamp_s"] == pytest.approx(610.0)
    assert bool(row1["rainfall"]) is True


# ---------------------------------------------------------------------------
# race_control
# ---------------------------------------------------------------------------

def test_build_race_control():
    store = _FakeStore()
    df = builders.build_race_control(store, TARGET_R)
    _assert_schema(df, "race_control")
    _assert_keys_filled(df, TARGET_R)
    assert len(df) == 2
    deleted_row = df[df["category"] == "Other"].iloc[0]
    assert deleted_row["lap"] == 2
    assert deleted_row["driver_number"] == "1"
    assert "DELETED" in deleted_row["message"]
    flag_row = df[df["category"] == "Flag"].iloc[0]
    assert flag_row["flag"] == "YELLOW"
    assert flag_row["scope"] == "Track"


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------

def test_build_results_race(monkeypatch):
    monkeypatch.setattr(
        builders, "get_finishing_order",
        lambda base_url, client, store=None: {"1": 1, "44": 2},
    )
    store = _FakeStore()
    df = builders.build_results(store, TARGET_R)
    _assert_schema(df, "results")
    _assert_keys_filled(df, TARGET_R)
    assert len(df) == 2

    ver = df[df["driver_number"] == "1"].iloc[0]
    assert ver["position"] == 1
    assert ver["driver_code"] == "VER"
    assert ver["laps_completed"] == 2
    assert ver["best_lap_time_s"] == pytest.approx(90.5)
    assert ver["classified_status"] == "FINISHED"

    ham = df[df["driver_number"] == "44"].iloc[0]
    assert ham["position"] == 2
    assert ham["laps_completed"] == 1
    # No Retired/Stopped flag in the fixture -> lap-count fallback: 1 of 2 laps is a DNF
    assert ham["classified_status"] == "DNF"


def test_race_results_use_retired_flag(monkeypatch):
    monkeypatch.setattr(
        builders, "get_finishing_order",
        lambda base_url, client, store=None: {"1": 1, "44": 2},
    )
    store = _FakeStore()
    entries = list(_TIMING_ENTRIES) + [
        {"_timestamp": "01:59:00.000", "Lines": {"1": {"Retired": True}, "44": {"Stopped": False}}},
    ]
    monkeypatch.setattr(store, "timing_data", lambda: entries)
    df = builders.build_results(store, TARGET_R)
    by_num = df.set_index("driver_number")["classified_status"]
    assert by_num["1"] == "DNF"
    assert by_num["44"] == "FINISHED"


def test_build_results_qualifying(monkeypatch):
    quali_df = pd.DataFrame([
        {"DriverNum": "1", "StartTime": 10.0, "EndTime": 100.0, "LapTime": 90.0, "Position": 1},
        {"DriverNum": "44", "StartTime": 10.0, "EndTime": 103.0, "LapTime": 93.0, "Position": 2},
    ])
    monkeypatch.setattr(
        builders, "get_qualifying_classification",
        lambda base_url, client, store=None: quali_df,
    )
    store = _FakeStore()
    df = builders.build_results(store, TARGET_Q)
    _assert_schema(df, "results")
    _assert_keys_filled(df, TARGET_Q)
    assert len(df) == 2
    ver = df[df["driver_number"] == "1"].iloc[0]
    assert ver["position"] == 1
    assert ver["best_lap_time_s"] == pytest.approx(90.0)
    assert pd.isna(ver["laps_completed"])


def test_build_results_practice_ranks_by_best_lap():
    store = _FakeStore()
    df = builders.build_results(store, TARGET_FP1)
    _assert_schema(df, "results")
    _assert_keys_filled(df, TARGET_FP1)
    assert len(df) == 2
    # VER's best lap (90.5s) beats HAM's (93.0s).
    assert list(df.sort_values("position")["driver_number"]) == ["1", "44"]


# ---------------------------------------------------------------------------
# build_tables
# ---------------------------------------------------------------------------

def test_build_tables_happy_path(monkeypatch):
    monkeypatch.setattr(
        builders, "get_finishing_order",
        lambda base_url, client, store=None: {"1": 1, "44": 2},
    )
    frames, warnings = builders.build_tables(_FakeStore(), TARGET_R)
    expected_names = {spec.name for spec in tables_for_tier("tables")}
    assert set(frames.keys()) == expected_names
    assert warnings == []
    for name, df in frames.items():
        _assert_schema(df, name)


def test_build_tables_never_aborts_on_failures(monkeypatch):
    monkeypatch.setattr(
        builders, "get_finishing_order",
        lambda base_url, client, store=None: {"1": 1, "44": 2},
    )
    store = _FakeStore(fail_weather=True, fail_timing_app_data=True)
    frames, warnings = builders.build_tables(store, TARGET_R)

    expected_names = {spec.name for spec in tables_for_tier("tables")}
    assert set(frames.keys()) == expected_names

    # weather and stints fail outright and come back empty, with a warning each.
    assert frames["weather"].empty
    assert frames["stints"].empty
    warned_tables = {w.split(":", 1)[0] for w in warnings}
    assert {"weather", "stints"} <= warned_tables

    # laps is a best-effort join around the same failure and still has rows.
    assert not frames["laps"].empty
    assert not frames["sessions"].empty
    assert not frames["drivers"].empty
    assert not frames["results"].empty

    for name, df in frames.items():
        _assert_schema(df, name)
