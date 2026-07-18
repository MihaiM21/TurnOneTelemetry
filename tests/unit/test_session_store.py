"""Offline unit tests for SessionDataStore.

The livetiming network is fully stubbed: the F1StaticClient's event resolution
is monkeypatched and its ``session.get`` returns captured fixtures. No real HTTP
happens.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.exceptions import DataNotAvailableError, SessionNotFoundError
from src.ingestion.static_client import F1StaticClient
from src.services.analysis.v2 import session_store as store_module
from src.services.analysis.v2.session_store import SessionDataStore

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


class _DisabledCache:
    """Stand-in for the Redis sync cache that is always disabled."""

    enabled = False

    def get_json(self, *a, **k):
        return None

    def set_json(self, *a, **k):
        return False


@pytest.fixture
def stubbed_store(monkeypatch):
    """A SessionDataStore whose event resolution, HTTP, Redis and Mongo are stubbed.

    Redis is forced disabled and the durable Mongo bundle cache is replaced with
    an in-memory dict so the suite is fully offline. The fake Mongo store is
    exposed as ``store._fake_mongo`` (keyed by ``(year, round_nr, session)``).
    """
    base = "https://example.test/2025/AUS/Race/"

    # Keep the suite offline: no real Redis, no real MongoDB.
    monkeypatch.setattr(store_module, "get_sync_cache", lambda: _DisabledCache())

    fake_mongo: dict = {}

    def fake_get_bundle(year, round_nr, session):
        return fake_mongo.get((year, round_nr, session))

    def fake_store_bundle(year, round_nr, session, bundle, meta=None):
        fake_mongo[(year, round_nr, session)] = bundle
        return True

    monkeypatch.setattr(store_module, "get_session_bundle", fake_get_bundle)
    monkeypatch.setattr(store_module, "store_session_bundle", fake_store_bundle)

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

    # Map stream filename -> fixture file (or 404).
    file_map = {
        "TimingData.jsonStream": "timing_data_sample.jsonStream",
        "TrackStatus.jsonStream": "track_status_sample.json",
        "WeatherData.jsonStream": None,  # handled below (json list of dicts)
        "DriverList.json": "driver_list_sample.json",
        "SessionInfo.json": "session_info_sample.json",
        "RaceControlMessages.json": "race_control_sample.json",
    }

    call_counts: dict[str, int] = {}

    def fake_get(url, timeout=None, **kwargs):
        filename = url[len(base):]
        call_counts[filename] = call_counts.get(filename, 0) + 1
        if filename == "TimingData.jsonStream":
            return _FakeResponse(_read_bytes("timing_data_sample.jsonStream"))
        if filename == "TimingAppData.jsonStream":
            return _FakeResponse(_read_bytes("timing_app_data_sample.jsonStream"))
        if filename == "TrackStatus.jsonStream":
            # Fixture is a JSON list of parsed dicts; re-serialize as a jsonStream.
            data = json.loads(_read_bytes("track_status_sample.json"))
            lines = []
            for e in data:
                ts = e.get("_timestamp", "")
                payload = {k: v for k, v in e.items() if k != "_timestamp"}
                lines.append(ts + json.dumps(payload))
            return _FakeResponse("\n".join(lines).encode("utf-8"))
        if filename == "WeatherData.jsonStream":
            data = json.loads(_read_bytes("weather_sample.json"))
            lines = []
            for e in data:
                ts = e.get("_timestamp", "")
                payload = {k: v for k, v in e.items() if k != "_timestamp"}
                lines.append(ts + json.dumps(payload))
            return _FakeResponse("\n".join(lines).encode("utf-8"))
        if filename == "MissingStream.jsonStream":
            return _FakeResponse(b"", status_code=404)
        mapped = file_map.get(filename)
        if mapped:
            return _FakeResponse(_read_bytes(mapped))
        return _FakeResponse(b"", status_code=404)

    store = SessionDataStore(2025, 1, "Race")
    monkeypatch.setattr(store.client.session, "get", fake_get)
    store._call_counts = call_counts  # expose for assertions
    store._fake_mongo = fake_mongo  # expose durable-cache double for assertions
    return store


def test_resolves_event_attributes(stubbed_store):
    assert stubbed_store.event_name == "Australian Grand Prix"
    assert stubbed_store.round_nr == 1
    assert stubbed_store.base_url.endswith("/Race/")


def test_unresolvable_event_raises(monkeypatch):
    monkeypatch.setattr(
        F1StaticClient, "get_event_info", lambda self, y, i: None
    )
    with pytest.raises(SessionNotFoundError):
        SessionDataStore(2025, 999, "Race")


def test_timing_data_parses(stubbed_store):
    entries = stubbed_store.timing_data()
    assert len(entries) > 0
    assert any("Lines" in e for e in entries)


def test_stream_caching_second_call_no_refetch(stubbed_store):
    stubbed_store.timing_data()
    stubbed_store.timing_data()
    stubbed_store.timing_data()
    # Only the first call hit the network.
    assert stubbed_store._call_counts["TimingData.jsonStream"] == 1


def test_driver_list_shape(stubbed_store):
    drivers = stubbed_store.driver_list()
    assert len(drivers) >= 1
    sample = next(iter(drivers.values()))
    assert "tla" in sample and "team" in sample and "color" in sample


def test_track_status_accessor(stubbed_store):
    ts = stubbed_store.track_status()
    assert ts and all("Status" in e for e in ts)


def test_race_control_normalizes_to_list(stubbed_store):
    msgs = stubbed_store.race_control()
    assert isinstance(msgs, list) and len(msgs) >= 1
    assert "Message" in msgs[0]


def test_session_info_accessor(stubbed_store):
    info = stubbed_store.session_info()
    assert info.get("Name") == "Race"


def test_missing_stream_maps_to_data_not_available(stubbed_store):
    with pytest.raises(DataNotAvailableError):
        stubbed_store._fetch_stream("MissingStream.jsonStream")


def test_missing_json_maps_to_data_not_available(stubbed_store):
    with pytest.raises(DataNotAvailableError):
        stubbed_store._fetch_json("Nonexistent.json")


# ----------------------------------------------------------------------
# Durable derived-data (MongoDB bundle) cache
# ----------------------------------------------------------------------
_BUNDLE_KEYS = (
    "lap_times", "positions_by_lap", "pit_stops",
    "best_sectors", "track_status_periods", "stints",
)


def test_past_season_is_complete(stubbed_store):
    # Fixture year (2025) is before the current year, so it's always cacheable.
    assert stubbed_store.is_session_complete() is True


def test_derived_bundle_built_and_persisted(stubbed_store):
    laps = stubbed_store.lap_times()
    assert laps  # computed on the miss

    key = (2025, 1, "Race")
    assert key in stubbed_store._fake_mongo
    bundle = stubbed_store._fake_mongo[key]
    for k in _BUNDLE_KEYS:
        assert k in bundle, f"bundle missing {k}"
    assert bundle["lap_times"] == laps


def test_bundle_build_downloads_timing_data_once(stubbed_store):
    # Five derived accessors, but the multi-MB TimingData stream is fetched once.
    stubbed_store.lap_times()
    stubbed_store.positions_by_lap()
    stubbed_store.pit_stops()
    stubbed_store.best_sectors()
    stubbed_store.track_status_periods()
    assert stubbed_store._call_counts.get("TimingData.jsonStream") == 1


def test_warm_store_serves_from_mongo_without_network(stubbed_store, monkeypatch):
    # First store builds and persists the bundle to the (fake) Mongo.
    expected = stubbed_store.lap_times()

    # A second store for the same session must serve every derived accessor from
    # the persisted bundle without touching the livetiming network at all.
    store2 = SessionDataStore(2025, 1, "Race")

    def boom(*a, **k):
        raise AssertionError("network hit despite warm Mongo bundle")

    monkeypatch.setattr(store2.client.session, "get", boom)

    assert store2.lap_times() == expected
    assert store2.pit_stops() is not None
    assert store2.track_status_periods() is not None
    assert store2.stints() is not None


def test_incomplete_session_not_persisted(stubbed_store, monkeypatch):
    # A live/ongoing session must not be cached with partial data.
    monkeypatch.setattr(stubbed_store, "is_session_complete", lambda: False)
    laps = stubbed_store.lap_times()
    assert laps  # still computed in-process for the request
    assert (2025, 1, "Race") not in stubbed_store._fake_mongo


def test_derived_accessor_matches_direct_compute(stubbed_store):
    # The public accessor returns exactly what the raw compute produces.
    from src.services.analysis.v2._race_helpers import _compute_lap_times
    direct = _compute_lap_times(stubbed_store)
    assert stubbed_store.lap_times() == direct
