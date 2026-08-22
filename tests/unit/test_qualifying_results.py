"""Offline unit tests for V2 qualifying classification ordering.

Regression coverage for two bugs found while investigating a report that the
2026 Dutch GP (a sprint weekend) qualifying-results endpoint returned Russell
as pole instead of Norris:

  1. Session-name resolution: covered separately in
     tests/unit/ingestion/test_static_client.py (``_session_matches`` wrongly
     matched "Qualifying" against "Sprint Qualifying").
  2. Ordering: ``get_fastest_lap_windows`` alone ranks drivers by the single
     fastest ``LastLapTime`` seen anywhere in a combined Q1/Q2/Q3 stream, so a
     driver eliminated early on a fast banker lap can outrank a driver who
     legitimately set pole in a later segment. ``get_qualifying_classification``
     fixes this by ordering on live timing's officially maintained ``Position``
     field instead (the same field ``get_finishing_order`` already trusts for
     race results), falling back to raw lap time only for ties/missing data.
"""
from __future__ import annotations

import json

from src.services.analysis.v2._helpers import get_qualifying_classification
from src.services.analysis.v2.qualifying_results import _process_data


class _FakeClient:
    """Minimal stand-in for F1StaticClient's stream + DriverList access."""

    def __init__(self, timing_entries, driver_list=None):
        self._timing_entries = timing_entries
        self._driver_list = driver_list or {}
        self.session = self

    def parse_jsonstream_simple(self, url):
        assert url.endswith("TimingData.jsonStream")
        return self._timing_entries

    # Stand-in for ``client.session.get(...)`` used by get_driver_team_from_list.
    def get(self, url):
        assert url.endswith("DriverList.json")
        return _FakeResponse(self._driver_list)


class _FakeResponse:
    def __init__(self, payload):
        self.content = json.dumps(payload).encode("utf-8-sig")


def _entries():
    """Driver 63 sets the outright fastest lap early, then is knocked out
    (classified P15 and never updates again); driver 1 sets a slower lap but
    is the genuine session pole (final Position 1)."""
    return [
        {
            "_timestamp": "100",
            "Lines": {
                "63": {"LastLapTime": {"Value": "1:10.000"}, "Position": "1"},
            },
        },
        {
            "_timestamp": "200",
            "Lines": {
                "63": {"Position": "15"},
            },
        },
        {
            "_timestamp": "300",
            "Lines": {
                "1": {"LastLapTime": {"Value": "1:11.163"}, "Position": "1"},
            },
        },
    ]


def test_get_qualifying_classification_orders_by_position_not_lap_time():
    client = _FakeClient(_entries())

    df = get_qualifying_classification("https://example/", client)

    assert list(df["DriverNum"]) == ["1", "63"]
    # Driver 63 still has the faster raw lap time on record.
    assert df.loc[df["DriverNum"] == "63", "LapTime"].iloc[0] < df.loc[df["DriverNum"] == "1", "LapTime"].iloc[0]


def test_process_data_ranks_pole_by_classification():
    driver_list = {
        "1": {"Tla": "NOR", "TeamName": "McLaren"},
        "63": {"Tla": "RUS", "TeamName": "Mercedes"},
    }
    client = _FakeClient(_entries(), driver_list=driver_list)

    results = _process_data("https://example/", client)

    assert [r["Driver"] for r in results] == ["NOR", "RUS"]
    assert results[0]["LapTimeDelta"] == 0.0
    # RUS's raw LapTime is actually faster than pole's (eliminated early on a
    # banker lap), so the delta must be clamped at zero, not negative.
    assert results[1]["LapTimeDelta"] == 0.0
