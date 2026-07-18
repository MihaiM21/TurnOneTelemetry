"""Offline unit tests for session_weather.py (F5: Weather & Session Timeline).

Follows the ``stubbed_store`` fixture pattern from ``test_session_store.py``:
the F1StaticClient event resolution and HTTP layer are fully monkeypatched
against the real 2025 Australian GP fixtures. No network happens.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.exceptions import DataNotAvailableError
from src.ingestion.static_client import F1StaticClient
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.analysis.v2.session_weather import (
    SessionWeatherData,
    SessionWeatherPlot,
    _build_race_control,
    _build_weather_series,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _read_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class _FakeResponse:
    def __init__(self, content: bytes = b"", status_code: int = 200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"status {self.status_code}")


def _make_store(monkeypatch, session_name="Race", weather_present=True):
    base = f"https://example.test/2025/AUS/{session_name}/"

    monkeypatch.setattr(
        F1StaticClient, "get_event_info",
        lambda self, year, ident: {
            "round_nr": 1, "name": "Australian Grand Prix",
            "official_name": "FORMULA 1 AUSTRALIAN GRAND PRIX 2025",
            "key": 1254,
        },
    )
    monkeypatch.setattr(
        F1StaticClient, "get_event_session_url",
        lambda self, *a, **k: base,
    )

    def fake_get(url, timeout=None, **kwargs):
        filename = url[len(base):]
        if filename == "TimingData.jsonStream":
            return _FakeResponse(_read_bytes("timing_data_sample.jsonStream"))
        if filename == "TrackStatus.jsonStream":
            data = json.loads(_read_bytes("track_status_sample.json"))
            lines = []
            for e in data:
                ts = e.get("_timestamp", "")
                payload = {k: v for k, v in e.items() if k != "_timestamp"}
                lines.append(ts + json.dumps(payload))
            return _FakeResponse("\n".join(lines).encode("utf-8"))
        if filename == "WeatherData.jsonStream":
            if not weather_present:
                return _FakeResponse(b"", status_code=404)
            data = json.loads(_read_bytes("weather_sample.json"))
            lines = []
            for e in data:
                ts = e.get("_timestamp", "")
                payload = {k: v for k, v in e.items() if k != "_timestamp"}
                lines.append(ts + json.dumps(payload))
            return _FakeResponse("\n".join(lines).encode("utf-8"))
        if filename == "DriverList.json":
            return _FakeResponse(_read_bytes("driver_list_sample.json"))
        if filename == "SessionInfo.json":
            return _FakeResponse(_read_bytes("session_info_sample.json"))
        if filename == "RaceControlMessages.json":
            return _FakeResponse(_read_bytes("race_control_sample.json"))
        return _FakeResponse(b"", status_code=404)

    store = SessionDataStore(2025, 1, session_name)
    monkeypatch.setattr(store.client.session, "get", fake_get)
    return store


@pytest.fixture
def race_store(monkeypatch):
    return _make_store(monkeypatch, session_name="Race")


@pytest.fixture
def quali_store(monkeypatch):
    return _make_store(monkeypatch, session_name="Qualifying")


@pytest.fixture
def no_weather_store(monkeypatch):
    return _make_store(monkeypatch, session_name="Race", weather_present=False)


# ---------------------------------------------------------------------
# Weather series parsing
# ---------------------------------------------------------------------

def test_weather_series_parses_floats_and_rainfall_flag(race_store):
    series = _build_weather_series(race_store, "R")
    assert len(series) == 5
    first = series[0]
    assert isinstance(first["air_temp"], float)
    assert first["air_temp"] == pytest.approx(15.8)
    assert isinstance(first["track_temp"], float)
    assert isinstance(first["humidity"], float)
    assert isinstance(first["wind_speed"], float)
    assert isinstance(first["wind_dir"], float)
    # Rainfall fixture value is "1" for every sample -> flag of 1, not mm.
    assert all(w["rainfall"] == 1 for w in series)
    assert all(w["rainfall"] in (0, 1) for w in series)


def test_weather_series_sorted_by_time(race_store):
    series = _build_weather_series(race_store, "R")
    times = [w["time_s"] for w in series]
    assert times == sorted(times)


# ---------------------------------------------------------------------
# Lap mapping: present for R, null for Q
# ---------------------------------------------------------------------

def test_lap_mapping_present_for_race(race_store):
    series = _build_weather_series(race_store, "R")
    # At least the later samples (after lap 1/2 boundaries in the fixture)
    # should have a resolved integer lap.
    assert any(w["lap"] is not None for w in series)
    for w in series:
        if w["lap"] is not None:
            assert isinstance(w["lap"], int)


def test_lap_mapping_null_for_qualifying(quali_store):
    series = _build_weather_series(quali_store, "Q")
    assert all(w["lap"] is None for w in series)


# ---------------------------------------------------------------------
# Race control message extraction
# ---------------------------------------------------------------------

def test_race_control_extracts_expected_fields(race_store):
    messages = _build_race_control(race_store)
    assert len(messages) == 8
    sample = messages[0]
    assert set(["time_s", "lap", "category", "flag", "message"]).issubset(sample.keys())
    assert any(m["category"] == "Flag" for m in messages)
    assert any(m["flag"] == "YELLOW" for m in messages)
    assert all(m["message"] for m in messages)


def test_race_control_laps_are_ints(race_store):
    messages = _build_race_control(race_store)
    assert all(m["lap"] == 1 for m in messages)


# ---------------------------------------------------------------------
# Missing weather -> DataNotAvailableError
# ---------------------------------------------------------------------

def test_missing_weather_raises_data_not_available(no_weather_store, monkeypatch):
    monkeypatch.setattr(
        "src.services.analysis.v2.session_weather.SessionDataStore",
        lambda *a, **k: no_weather_store,
    )
    with pytest.raises(DataNotAvailableError):
        SessionWeatherData()(2025, 1, "R")


# ---------------------------------------------------------------------
# Full payload + plot smoke (offline)
# ---------------------------------------------------------------------

def test_session_weather_data_full_payload(monkeypatch, race_store):
    monkeypatch.setattr(
        "src.services.analysis.v2.session_weather.SessionDataStore",
        lambda *a, **k: race_store,
    )
    payload = SessionWeatherData()(2025, 1, "R")
    assert "weather" in payload
    assert "track_status_periods" in payload
    assert "race_control" in payload
    assert len(payload["weather"]) == 5
    assert len(payload["track_status_periods"]) > 0
    assert len(payload["race_control"]) == 8


def test_session_weather_plot_produces_png(monkeypatch, race_store, tmp_path):
    monkeypatch.setattr(
        "src.services.analysis.v2.session_weather.SessionDataStore",
        lambda *a, **k: race_store,
    )
    monkeypatch.chdir(tmp_path)
    plot_path = SessionWeatherPlot()(2025, 1, "R")
    assert Path(plot_path).is_file()
    assert Path(plot_path).stat().st_size > 0
