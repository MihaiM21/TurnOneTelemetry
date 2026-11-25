"""Offline tests for src/ingestion/openf1_client.py (OpenF1 catalogue, index and path synthesis)."""
import pytest
import responses

from src.core.config import settings
from src.core.exceptions import DataNotAvailableError, UpstreamUnavailableError
from src.ingestion import openf1_client, openf1_streams
from src.ingestion import static_client
from src.ingestion.calendar import adapt_livetiming_index

BASE = "https://api.openf1.org/v1"

LV_MEETING = {
    "meeting_key": 1300, "meeting_name": "Las Vegas Grand Prix",
    "meeting_official_name": "FORMULA 1 HEINEKEN LAS VEGAS GRAND PRIX 2026",
    "location": "Las Vegas", "country_key": 19, "country_code": "USA", "country_name": "United States",
    "circuit_key": 152, "circuit_short_name": "Las Vegas", "gmt_offset": "-08:00:00",
    "date_start": "2026-11-19T21:30:00+00:00", "year": 2026,
}
SP_MEETING = {
    "meeting_key": 1310, "meeting_name": "São Paulo Grand Prix",
    "meeting_official_name": "FORMULA 1 MSC CRUISES GRANDE PRÊMIO DE SÃO PAULO 2026",
    "location": "São Paulo", "country_key": 20, "country_code": "BRA", "country_name": "Brazil",
    "circuit_key": 14, "circuit_short_name": "Interlagos", "gmt_offset": "-03:00:00",
    "date_start": "2026-11-06T13:30:00+00:00", "year": 2026,
}


def _session(key, meeting_key, name, stype, start, end, offset="-08:00:00", cancelled=False, loc="Las Vegas"):
    return {
        "session_key": key, "session_type": stype, "session_name": name,
        "date_start": start, "date_end": end, "meeting_key": meeting_key, "circuit_key": 152,
        "circuit_short_name": loc, "country_code": "USA", "country_name": "United States",
        "location": loc, "gmt_offset": offset, "year": 2026, "is_cancelled": cancelled,
    }


LV_SESSIONS = [
    _session(11412, 1300, "Practice 1", "Practice", "2026-11-20T02:30:00+00:00", "2026-11-20T03:30:00+00:00"),
    _session(11414, 1300, "Practice 3", "Practice", "2026-11-21T02:30:00+00:00", "2026-11-21T03:30:00+00:00",
             cancelled=True),
    _session(11416, 1300, "Qualifying", "Qualifying", "2026-11-21T04:00:00+00:00", "2026-11-21T05:00:00+00:00"),
    _session(11420, 1300, "Race", "Race", "2026-11-22T04:00:00+00:00", "2026-11-22T06:00:00+00:00"),
]
SP_SESSIONS = [
    _session(11500, 1310, "Race", "Race", "2026-11-08T17:00:00+00:00", "2026-11-08T19:00:00+00:00",
             offset="-03:00:00", loc="São Paulo"),
]

QUALI_PATH = "2026/2026-11-21_Las_Vegas_Grand_Prix/2026-11-20_Qualifying/"
SAO_PAULO_PATH = "2026/2026-11-08_São_Paulo_Grand_Prix/2026-11-08_Race/"


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
def catalog():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, f"{BASE}/meetings", json=[LV_MEETING, SP_MEETING])
        rsps.add(responses.GET, f"{BASE}/sessions", json=LV_SESSIONS + SP_SESSIONS)
        yield rsps


# --------------------------------------------------------------------------- names
def test_sprint_qualifying_is_sprint_shootout_only_in_2023():
    assert openf1_client.livetiming_session_name("Sprint Qualifying", 2023) == "Sprint Shootout"
    assert openf1_client.livetiming_session_name("Sprint Qualifying", 2024) == "Sprint Qualifying"
    assert openf1_client.livetiming_session_name("Race", 2023) == "Race"


def test_testing_days_become_practice_sessions():
    assert openf1_client.livetiming_session_name("Day 2", 2026) == "Practice 2"
    assert openf1_client.livetiming_session_name("Day Off", 2026) == "Day Off"


# --------------------------------------------------------------------------- paths
def test_synth_path_las_vegas_uses_local_dates():
    path = openf1_client.synth_session_path("Las Vegas Grand Prix", LV_SESSIONS, LV_SESSIONS[2], 2026)
    assert path == QUALI_PATH


def test_meeting_folder_dated_by_last_session_in_local_time():
    race = LV_SESSIONS[3]
    # 04:00 UTC on the 22nd is still the 21st in Las Vegas (UTC-8).
    path = openf1_client.synth_session_path("Las Vegas Grand Prix", LV_SESSIONS, race, 2026)
    assert path == "2026/2026-11-21_Las_Vegas_Grand_Prix/2026-11-21_Race/"


def test_accents_are_kept_and_spaces_become_underscores():
    path = openf1_client.synth_session_path("São Paulo Grand Prix", SP_SESSIONS, SP_SESSIONS[0], 2026)
    assert path == SAO_PAULO_PATH


def test_session_path_from_url_plain_and_proxied():
    url = f"https://livetiming.formula1.com/static/{QUALI_PATH}TimingData.jsonStream"
    assert openf1_client.session_path_from_url(url) == QUALI_PATH
    proxied = f"https://f1-proxy.example.workers.dev/static/{QUALI_PATH}CarData.z.jsonStream"
    assert openf1_client.session_path_from_url(proxied) == QUALI_PATH
    bare_proxy = f"https://f1-proxy.example.workers.dev/{QUALI_PATH}DriverList.json"
    assert openf1_client.session_path_from_url(bare_proxy) == QUALI_PATH


def test_session_path_from_url_decodes_percent_encoding():
    url = ("https://f1-proxy.example.workers.dev/2026/2026-11-08_S%C3%A3o_Paulo_Grand_Prix/"
           "2026-11-08_Race/DriverList.json")
    assert openf1_client.session_path_from_url(url) == SAO_PAULO_PATH


def test_session_path_from_url_without_session_returns_none():
    assert openf1_client.session_path_from_url("https://livetiming.formula1.com/static/2026/Index.json") is None


# --------------------------------------------------------------------------- index
def test_build_index_has_livetiming_shape(catalog):
    index = openf1_client.build_index(2026)
    assert index["_source"] == "openf1"
    assert index["Year"] == 2026
    meetings = {m["Key"]: m for m in index["Meetings"]}
    assert set(meetings) == {1300, 1310}

    lv = meetings[1300]
    assert lv["Name"] == "Las Vegas Grand Prix"
    assert lv["OfficialName"] == LV_MEETING["meeting_official_name"]
    assert lv["Location"] == "Las Vegas"
    assert lv["Country"]["Name"] == "United States"
    assert lv["Circuit"] == {"Key": 152, "ShortName": "Las Vegas"}

    # The cancelled Practice 3 is dropped; the rest stay in start order.
    assert [s["Name"] for s in lv["Sessions"]] == ["Practice 1", "Qualifying", "Race"]
    quali = lv["Sessions"][1]
    assert quali["Key"] == 11416
    assert quali["Type"] == "Qualifying"
    assert quali["StartDate"] == "2026-11-20T20:00:00"
    assert quali["EndDate"] == "2026-11-20T21:00:00"
    assert quali["GmtOffset"] == "-08:00:00"
    assert quali["Path"] == QUALI_PATH


def test_build_index_meetings_ordered_by_first_session(catalog):
    index = openf1_client.build_index(2026)
    assert [m["Key"] for m in index["Meetings"]] == [1310, 1300]


def test_calendar_adapter_accepts_the_rebuilt_index(catalog):
    events = adapt_livetiming_index(openf1_client.build_index(2026))
    by_key = {e["key"]: e for e in events}
    lv = by_key[1300]
    assert lv["grandPrix"] == "Las Vegas Grand Prix"
    assert lv["circuitKey"] == 152
    assert lv["location"] == "Las Vegas"
    paths = {s["name"]: s["path"] for s in lv["sessions"]}
    assert paths["Qualifying"] == QUALI_PATH
    assert paths["Race"] == "2026/2026-11-21_Las_Vegas_Grand_Prix/2026-11-21_Race/"
    quali = next(s for s in lv["sessions"] if s["name"] == "Qualifying")
    assert quali["startTime"].isoformat().startswith("2026-11-21T04:00:00")  # local + offset -> UTC again


def test_index_before_2023_is_refused():
    with pytest.raises(DataNotAvailableError):
        openf1_client.build_index(2022)


# --------------------------------------------------------------------------- reverse lookup
def test_session_key_for_path_exact(catalog):
    assert openf1_client.session_key_for_path(QUALI_PATH)["session_key"] == 11416


def test_session_key_for_path_loose_match_ignores_session_folder_date(catalog):
    moved = "2026/2026-11-21_Las_Vegas_Grand_Prix/2026-11-19_Qualifying/"
    assert openf1_client.session_key_for_path(moved)["session_key"] == 11416


def test_session_key_for_path_unknown_raises(catalog):
    with pytest.raises(DataNotAvailableError):
        openf1_client.session_key_for_path("2026/2026-03-01_Nowhere_Grand_Prix/2026-03-01_Race/")
    with pytest.raises(DataNotAvailableError):
        # right meeting, no such session
        openf1_client.session_key_for_path("2026/2026-11-21_Las_Vegas_Grand_Prix/2026-11-20_Sprint/")


# --------------------------------------------------------------------------- get()
@responses.activate
def test_get_404_is_an_empty_result():
    responses.add(responses.GET, f"{BASE}/pit", json={"detail": "No results found."}, status=404)
    assert openf1_client.OpenF1Client().get("pit", session_key=1) == []


@responses.activate
def test_get_sends_params_and_returns_rows():
    responses.add(responses.GET, f"{BASE}/laps", json=[{"lap_number": 1}])
    rows = openf1_client.OpenF1Client().get("laps", session_key=11416, driver_number=63)
    assert rows == [{"lap_number": 1}]
    query = responses.calls[0].request.url.split("?", 1)[1]
    assert "session_key=11416" in query and "driver_number=63" in query


@responses.activate
def test_get_retries_429_honouring_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr(openf1_client.time, "sleep", lambda s: sleeps.append(s))
    responses.add(responses.GET, f"{BASE}/laps", status=429, headers={"Retry-After": "7"})
    responses.add(responses.GET, f"{BASE}/laps", json=[{"lap_number": 1}])
    assert openf1_client.OpenF1Client().get("laps", session_key=1) == [{"lap_number": 1}]
    assert len(responses.calls) == 2
    assert 7.0 in sleeps


@responses.activate
def test_get_retries_5xx_with_backoff_then_gives_up(monkeypatch):
    sleeps = []
    monkeypatch.setattr(openf1_client.time, "sleep", lambda s: sleeps.append(s))
    responses.add(responses.GET, f"{BASE}/laps", status=503)
    with pytest.raises(UpstreamUnavailableError):
        openf1_client.OpenF1Client().get("laps", session_key=1)
    assert len(responses.calls) == 5  # first try + 4 retries
    assert sleeps == [2.0, 4.0, 6.0, 8.0]


@responses.activate
def test_get_422_is_an_outage_not_a_retry(monkeypatch):
    monkeypatch.setattr(openf1_client.time, "sleep", lambda s: None)
    responses.add(responses.GET, f"{BASE}/car_data", status=422, json={"detail": "too much data"})
    with pytest.raises(UpstreamUnavailableError) as exc:
        openf1_client.OpenF1Client().get("car_data", session_key=1)
    assert "too large" in exc.value.reason
    assert len(responses.calls) == 1


@responses.activate
def test_get_non_json_body_is_an_outage():
    responses.add(responses.GET, f"{BASE}/laps", body="<html>oops</html>", status=200)
    with pytest.raises(UpstreamUnavailableError):
        openf1_client.OpenF1Client().get("laps", session_key=1)


@responses.activate
def test_get_non_list_json_is_empty():
    responses.add(responses.GET, f"{BASE}/laps", json={"detail": "x"}, status=200)
    assert openf1_client.OpenF1Client().get("laps", session_key=1) == []


# --------------------------------------------------------------------------- catalogue cache
def test_season_catalog_is_cached_and_drops_cancelled(catalog):
    first = openf1_client.season_catalog(2026)
    second = openf1_client.season_catalog(2026)
    assert first is second
    assert len(catalog.calls) == 2  # one meetings + one sessions, not four
    assert all(not s["is_cancelled"] for s in first["sessions"])
    assert set(first["meetings"]) == {1300, 1310}


def test_season_catalog_before_2023_raises():
    with pytest.raises(DataNotAvailableError):
        openf1_client.season_catalog(2022)


def test_parse_offset_variants():
    assert openf1_client._parse_offset("-08:00:00").total_seconds() == -8 * 3600
    assert openf1_client._parse_offset("+05:30:00").total_seconds() == 5.5 * 3600
    assert openf1_client._parse_offset(None).total_seconds() == 0


def test_settings_default_base_url_is_openf1():
    assert settings.openf1_base_url.rstrip("/") == BASE
