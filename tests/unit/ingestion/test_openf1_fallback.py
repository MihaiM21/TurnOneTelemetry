"""OpenF1 fallback wired through F1StaticClient and SessionDataStore (fully offline).

livetiming answers 401 (no F1 TV token); the rebuilt streams must come from the
mocked api.openf1.org, and durable caches must keep the two sources apart.
"""
import base64
import json
import re
import zlib
from urllib.parse import urlparse

import pytest
import responses

from src.core.config import settings
from src.core.exceptions import LivetimingAccessError
from src.ingestion import openf1_client, openf1_streams, static_client
from src.ingestion.static_client import F1StaticClient
from src.services.analysis.v2 import session_store as store_module
from src.services.analysis.v2.session_store import SessionDataStore
from tests.unit.ingestion.test_openf1_streams import LIVETIMING, QUALI_PATH, FakeOpenF1

CAR_URL = LIVETIMING + QUALI_PATH + "CarData.z.jsonStream"
LIVETIMING_ANY = re.compile(r"https://livetiming\.formula1\.com/static/.*")
SENTINEL = [{"_timestamp": "00:00:01.000", "Entries": [], "sentinel": True}]


@pytest.fixture(autouse=True)
def _fresh_caches():
    openf1_client.clear_cache()
    openf1_streams.clear_cache()
    static_client.clear_index_cache()
    yield
    openf1_client.clear_cache()
    openf1_streams.clear_cache()
    static_client.clear_index_cache()


@pytest.fixture
def fallback_on(monkeypatch):
    monkeypatch.setattr(settings, "openf1_fallback", True)


@pytest.fixture
def rsps():
    fake = FakeOpenF1()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        fake.install(mock)
        yield mock


def calls_to_host(mock, host):
    return [c for c in mock.calls if urlparse(c.request.url).netloc == host]


def openf1_calls(mock):
    return calls_to_host(mock, "api.openf1.org")


def livetiming_calls(mock):
    return calls_to_host(mock, "livetiming.formula1.com")


# --------------------------------------------------------------------------- season index
def test_index_401_falls_back_to_openf1_and_locks_the_season(fallback_on, rsps):
    rsps.add(responses.GET, f"{LIVETIMING}2026/Index.json", status=401)
    client = F1StaticClient()
    assert client.livetiming_locked(2026) is False
    index = client.fetch_season_index(2026)
    assert index["_source"] == "openf1"
    assert [m["Key"] for m in index["Meetings"]] == [1300]
    assert index["Meetings"][0]["Sessions"][0]["Path"] == QUALI_PATH
    assert client.livetiming_locked(2026) is True


def test_fallback_index_is_cached_so_livetiming_is_not_hammered(fallback_on, rsps):
    rsps.add(responses.GET, f"{LIVETIMING}2026/Index.json", status=401)
    client = F1StaticClient()
    client.fetch_season_index(2026)
    client.fetch_season_index(2026)
    assert len(livetiming_calls(rsps)) == 1


def test_index_401_with_fallback_off_raises(rsps):
    assert settings.openf1_fallback is False  # tests/conftest.py default
    rsps.add(responses.GET, f"{LIVETIMING}2026/Index.json", status=401)
    with pytest.raises(LivetimingAccessError):
        F1StaticClient().fetch_season_index(2026)
    assert openf1_calls(rsps) == []


def test_index_401_before_2023_raises_even_with_fallback(fallback_on, rsps):
    rsps.add(responses.GET, f"{LIVETIMING}2022/Index.json", status=401)
    with pytest.raises(LivetimingAccessError):
        F1StaticClient().fetch_season_index(2022)
    assert openf1_calls(rsps) == []


def test_working_livetiming_index_is_not_locked(fallback_on, rsps):
    rsps.add(responses.GET, f"{LIVETIMING}2026/Index.json", json={"Year": 2026, "Meetings": []})
    client = F1StaticClient()
    assert client.fetch_season_index(2026)["Meetings"] == []
    assert client.livetiming_locked(2026) is False
    assert openf1_calls(rsps) == []


# --------------------------------------------------------------------------- stream parsers
def test_compressed_stream_401_returns_rebuilt_car_data(fallback_on, rsps):
    rsps.add(responses.GET, CAR_URL, status=401)
    packets = F1StaticClient().parse_compressed_stream(CAR_URL)
    assert packets and "Entries" in packets[0] and "_timestamp" in packets[0]
    assert set(packets[0]["Entries"][0]["Cars"]) == {"63", "12"}


def test_compressed_stream_without_fallback_kwarg_raises(fallback_on, rsps):
    rsps.add(responses.GET, CAR_URL, status=401)
    with pytest.raises(LivetimingAccessError):
        F1StaticClient().parse_compressed_stream(CAR_URL, fallback=False)
    assert openf1_calls(rsps) == []


def test_compressed_stream_401_with_setting_off_raises(rsps):
    rsps.add(responses.GET, CAR_URL, status=401)
    with pytest.raises(LivetimingAccessError):
        F1StaticClient().parse_compressed_stream(CAR_URL)


def test_unsupported_file_reraises_the_original_access_error(fallback_on, rsps):
    url = LIVETIMING + QUALI_PATH + "TeamRadio.jsonStream"
    rsps.add(responses.GET, url, status=401)
    with pytest.raises(LivetimingAccessError) as exc:
        F1StaticClient().parse_jsonstream(url)
    assert "401" in exc.value.reason


def test_json_file_401_falls_back_and_stream_after_index_lock_skips_livetiming(fallback_on, rsps):
    rsps.add(responses.GET, f"{LIVETIMING}2026/Index.json", status=401)
    driver_url = LIVETIMING + QUALI_PATH + "DriverList.json"
    rsps.add(responses.GET, driver_url, status=401)
    client = F1StaticClient()
    assert client.fetch_json(driver_url)["63"]["Tla"] == "RUS"  # 401 -> OpenF1
    client.fetch_season_index(2026)  # lock the season
    before = len(livetiming_calls(rsps))
    assert client.parse_jsonstream_simple(LIVETIMING + QUALI_PATH + "TrackStatus.jsonStream") is not None
    assert len(livetiming_calls(rsps)) == before  # went straight to OpenF1


# --------------------------------------------------------------------------- SessionDataStore provenance
class _DisabledCache:
    enabled = False

    def get_json(self, *a, **k):
        return None

    def set_json(self, *a, **k):
        return False


@pytest.fixture
def make_store(monkeypatch):
    """Factory for a store on the qualifying session with in-memory durable tiers."""
    raw = {}

    def get_raw(year, round_nr, session, stream, source=None):
        hit = raw.get((year, round_nr, session, stream))
        return hit[1] if hit and hit[0] == source else None

    def store_raw(year, round_nr, session, stream, data, source=None):
        raw[(year, round_nr, session, stream)] = (source, data)
        return True

    monkeypatch.setattr(store_module, "get_sync_cache", lambda: _DisabledCache())
    monkeypatch.setattr(store_module, "get_raw_stream", get_raw)
    monkeypatch.setattr(store_module, "store_raw_stream", store_raw)
    monkeypatch.setattr(store_module, "get_session_bundle", lambda *a, **k: None)
    monkeypatch.setattr(store_module, "store_session_bundle",
                        lambda year, rnd, session, bundle, meta=None, source=None: True)
    monkeypatch.setattr(F1StaticClient, "get_event_info", lambda self, year, ident: {
        "round_nr": 24, "name": "Las Vegas Grand Prix", "key": 1300,
        "official_name": "FORMULA 1 HEINEKEN LAS VEGAS GRAND PRIX 2026"})
    monkeypatch.setattr(F1StaticClient, "get_event_session_url",
                        lambda self, *a, **k: LIVETIMING + QUALI_PATH)

    def build():
        store = SessionDataStore(2026, 24, "Q")
        store.raw = raw
        return store

    return build


def _raw_entry(store, stream="car_data"):
    return store.raw[(2026, 24, "Q", stream)]


def test_store_serves_openf1_when_livetiming_401_and_tags_provenance(fallback_on, rsps, make_store):
    rsps.add(responses.GET, LIVETIMING_ANY, status=401)
    store = make_store()
    assert store.source == "2026/2026-11-21_Las_Vegas_Grand_Prix/2026-11-20_Qualifying"
    data = store.car_data()
    assert set(data[0]["Entries"][0]["Cars"]) == {"63", "12"}
    source, stored = _raw_entry(store)
    assert source == "openf1:" + store.source
    assert stored == data
    assert len(store.raw) >= 1 and all(src.startswith("openf1:") for src, _ in store.raw.values())


def test_real_gridfs_entry_wins_and_openf1_is_never_called(fallback_on, rsps, make_store):
    rsps.add(responses.GET, LIVETIMING_ANY, status=401)
    store = make_store()
    store.raw[(2026, 24, "Q", "car_data")] = (store.source, SENTINEL)
    assert store.car_data() == SENTINEL
    assert openf1_calls(rsps) == []
    assert livetiming_calls(rsps) == []


def test_openf1_entry_is_reused_from_gridfs_without_new_requests(fallback_on, rsps, make_store):
    rsps.add(responses.GET, LIVETIMING_ANY, status=401)
    store = make_store()
    store.raw[(2026, 24, "Q", "car_data")] = ("openf1:" + store.source, SENTINEL)
    assert store.car_data() == SENTINEL
    assert openf1_calls(rsps) == []


def test_openf1_entry_is_not_served_as_real(fallback_on, rsps, make_store):
    """A stored OpenF1 entry never satisfies the real-source lookup (provenance must match exactly)."""
    store = make_store()
    store.raw[(2026, 24, "Q", "car_data")] = ("openf1:" + store.source, SENTINEL)
    assert store_module.get_raw_stream(2026, 24, "Q", "car_data", source=store.source) is None


def _z_line(payload):
    comp = zlib.compressobj(wbits=-15)
    body = comp.compress(json.dumps(payload).encode()) + comp.flush()
    return '00:00:01.000"' + base64.b64encode(body).decode() + '"'


def test_livetiming_200_is_stored_under_the_real_source(fallback_on, rsps, make_store):
    packet = {"Entries": [{"Utc": "2026-11-21T04:13:00+00:00", "Cars": {"63": {"Channels": {"2": 301}}}}]}
    rsps.add(responses.GET, CAR_URL, body=_z_line(packet))
    rsps.add(responses.GET, LIVETIMING + QUALI_PATH + "SessionInfo.json",
             json={"StartDate": "2026-11-20T20:00:00", "GmtOffset": "-08:00:00", "Type": "Qualifying"})
    store = make_store()
    data = store.car_data()
    assert data[0]["Entries"][0]["Cars"]["63"]["Channels"]["2"] == 301
    source, stored = _raw_entry(store)
    assert source == store.source
    assert stored == data
    assert openf1_calls(rsps) == []


def test_locked_season_skips_livetiming_for_streams(fallback_on, rsps, make_store):
    rsps.add(responses.GET, f"{LIVETIMING}2026/Index.json", status=401)
    rsps.add(responses.GET, LIVETIMING_ANY, status=401)
    F1StaticClient().fetch_season_index(2026)  # locks 2026 (shared process-wide index cache)
    store = make_store()
    assert store._livetiming_locked is True
    before = len(livetiming_calls(rsps))
    data = store.car_data()
    assert data and len(livetiming_calls(rsps)) == before
    assert _raw_entry(store)[0] == "openf1:" + store.source


def test_unsupported_stream_in_store_surfaces_the_access_error(fallback_on, rsps, make_store):
    rsps.add(responses.GET, LIVETIMING_ANY, status=401)
    store = make_store()
    with pytest.raises(LivetimingAccessError):
        store._cached("team_radio", lambda: store.client.parse_jsonstream(
            store.base_url + "TeamRadio.jsonStream", fallback=False))


def test_store_with_fallback_off_raises_access_error(rsps, make_store):
    rsps.add(responses.GET, LIVETIMING_ANY, status=401)
    store = make_store()
    with pytest.raises(LivetimingAccessError):
        store.car_data()
    assert openf1_calls(rsps) == []
