"""Unit tests for src.ingestion.static_client.F1StaticClient."""
import json

import pytest
import responses

from src.ingestion import calendar as cal
from src.ingestion import event_resolver as resolver_mod
from src.ingestion.static_client import F1StaticClient, source_token


def _meeting(key, name, sessions, number=None, location="", country="", circuit=""):
    return {
        "Key": key, "Number": number, "Name": name, "OfficialName": name.upper(), "Code": str(key),
        "Location": location or name, "Country": {"Name": country or name}, "Circuit": {"Key": key, "ShortName": circuit or name},
        "Sessions": sessions,
    }


def _session(name, path, start="2024-03-01T12:00:00"):
    return {"Name": name, "Path": path, "StartDate": start, "EndDate": start, "GmtOffset": "00:00:00"}


SAMPLE_INDEX = {
    "Year": 2024,
    "Meetings": [
        _meeting(1234, "Bahrain Grand Prix", [
            _session("Practice 1", "2024/2024-03-02_Bahrain_Grand_Prix/2024-03-01_Practice_1/", "2024-03-01T12:00:00"),
            _session("Qualifying", "2024/2024-03-02_Bahrain_Grand_Prix/2024-03-01_Qualifying/", "2024-03-01T16:00:00"),
            _session("Race", "2024/2024-03-02_Bahrain_Grand_Prix/2024-03-02_Race/", "2024-03-02T15:00:00"),
        ], 1),
        _meeting(1235, "Saudi Arabian Grand Prix", [
            _session("Race", "2024/2024-03-09_Saudi/2024-03-09_Race/", "2024-03-09T17:00:00"),
        ], 2),
    ],
}


def _use_calendar(monkeypatch, year, index, **kw):
    """Make the resolver see the calendar built from ``index`` (no network, no snapshot)."""
    events = cal.build_calendar(year, live=cal.adapt_livetiming_index(index), overrides=kw.pop("overrides", {}), **kw)
    monkeypatch.setattr(resolver_mod, "get_season_calendar", lambda y: events)
    return events


@pytest.fixture
def client():
    return F1StaticClient()


def _add_index(year, index):
    responses.add(responses.GET, f"https://livetiming.formula1.com/static/{year}/Index.json", json=index, status=200)


@responses.activate
def test_fetch_season_index_decodes_utf8_bom(client):
    # Real F1 responses arrive with a single UTF-8 BOM prefix.
    body_bytes = b"\xef\xbb\xbf" + json.dumps(SAMPLE_INDEX).encode("utf-8")
    responses.add(
        responses.GET,
        "https://livetiming.formula1.com/static/2024/Index.json",
        body=body_bytes,
        status=200,
    )
    result = client.fetch_season_index(2024)
    assert result["Year"] == 2024
    assert len(result["Meetings"]) == 2


@responses.activate
def test_season_index_is_fetched_once_per_ttl(client):
    _add_index(2024, SAMPLE_INDEX)
    client.fetch_season_index(2024)
    client.fetch_season_index(2024)
    assert len(responses.calls) == 1


@responses.activate
def test_forbidden_index_is_not_found_not_an_outage(client):
    """livetiming answers 403 for the 2022 index: no data, not an upstream failure."""
    from src.core.exceptions import SessionNotFoundError

    responses.add(responses.GET, "https://livetiming.formula1.com/static/2022/Index.json", status=403)
    with pytest.raises(SessionNotFoundError):
        client.fetch_season_index(2022)


@responses.activate
def test_get_event_session_url_resolves_qualifying(client, monkeypatch):
    _add_index(2024, SAMPLE_INDEX)
    _use_calendar(monkeypatch, 2024, SAMPLE_INDEX)
    url = client.get_event_session_url(2024, "Bahrain", "Q")
    assert url is not None
    assert "Qualifying" in url


@responses.activate
def test_get_event_session_url_returns_none_for_unknown_event(client, monkeypatch):
    _add_index(2024, SAMPLE_INDEX)
    _use_calendar(monkeypatch, 2024, SAMPLE_INDEX)
    assert client.get_event_session_url(2024, "Atlantis", "Race") is None


@responses.activate
def test_get_timing_data_url_appends_filename(client, monkeypatch):
    _add_index(2024, SAMPLE_INDEX)
    _use_calendar(monkeypatch, 2024, SAMPLE_INDEX)
    url = client.get_timing_data_url(2024, "Bahrain", "Race")
    assert url is not None
    assert url.endswith("TimingData.jsonStream")


# Livetiming index where pre-season testing occupies the first slot (2026-style).
TESTING_OFFSET_INDEX = {
    "Year": 2026,
    "Meetings": [
        _meeting(1000, "Pre-Season Testing", [_session("Practice 1", "2026/testing/2026-01-01_Practice_1/", "2026-02-11T10:00:00")], 1),
        _meeting(1279, "Australian Grand Prix", [
            _session("Practice 1", "2026/aus/2026-03-06_Practice_1/", "2026-03-06T10:00:00"),
            _session("Qualifying", "2026/aus/2026-03-07_Qualifying/", "2026-03-07T10:00:00"),
            _session("Race", "2026/aus/2026-03-08_Race/", "2026-03-08T10:00:00"),
        ], 1),
    ],
}


@responses.activate
def test_official_round_one_is_the_race_not_pre_season_testing(client, monkeypatch):
    _add_index(2026, TESTING_OFFSET_INDEX)
    _use_calendar(monkeypatch, 2026, TESTING_OFFSET_INDEX)
    url = client.get_event_session_url(2026, "Australian Grand Prix", "R", round_nr=1)
    assert url is not None
    assert "aus" in url and "Race" in url


@responses.activate
def test_testing_is_selected_by_key(client, monkeypatch):
    _add_index(2026, TESTING_OFFSET_INDEX)
    _use_calendar(monkeypatch, 2026, TESTING_OFFSET_INDEX)
    url = client.get_event_session_url(2026, "Pre-Season Testing", "FP1", round_nr=0, event_key=1000)
    assert url is not None
    assert "testing" in url


# ---- the bug: one event's data returned for another ----------------------------------------------

def _season(names_and_keys, year=2026, testing=1):
    """A livetiming index shaped like the real one: ``testing`` leading meetings, then the races."""
    meetings = [
        _meeting(900 + i, "Pre-Season Testing", [_session("Practice 1", f"{year}/testing{i}/P1/", f"{year}-02-0{i + 1}T10:00:00")], 1)
        for i in range(testing)
    ]
    for n, (name, key) in enumerate(names_and_keys, start=1):
        slug = name.replace(" ", "_")
        day = f"{year}-{2 + n:02d}-14"
        meetings.append(_meeting(key, name, [
            _session("Qualifying", f"{year}/{slug}/Q/", f"{year}-{2 + n:02d}-13T10:00:00"),
            _session("Race", f"{year}/{slug}/R/", f"{day}T10:00:00"),
        ], n))
    return {"Year": year, "Meetings": meetings}


@responses.activate
@pytest.mark.parametrize("testing", [0, 1, 2])
def test_every_round_resolves_to_its_own_meeting_whatever_precedes_it_in_the_index(client, monkeypatch, testing):
    """Replay of the reported bug: round N must never land on the meeting at array index N-1.

    Japan (R3) used to return Australia's laps because two testing meetings sit before
    the first race; Emilia-Romagna (R7) returned Miami's; and so on.
    """
    races = [("Australian Grand Prix", 11), ("Chinese Grand Prix", 12), ("Japanese Grand Prix", 13),
             ("Miami Grand Prix", 14), ("Monaco Grand Prix", 15)]
    index = _season(races, testing=testing)
    _add_index(2026, index)
    _use_calendar(monkeypatch, 2026, index)

    for round_nr, (name, _key) in enumerate(races, start=1):
        for session in ("Q", "R"):
            url = client.get_event_session_url(2026, name, session, round_nr=round_nr)
            assert url is not None
            assert name.replace(" ", "_") in url, (round_nr, name, url)
            assert client.get_event_session_url(2026, name, session, round_nr=round_nr, event_key=_key) == url


@responses.activate
def test_round_and_name_that_disagree_are_refused_not_guessed(client, monkeypatch):
    index = _season([("Australian Grand Prix", 11), ("Chinese Grand Prix", 12)], testing=1)
    _add_index(2026, index)
    _use_calendar(monkeypatch, 2026, index)
    assert client.get_event_session_url(2026, "Chinese Grand Prix", "R", round_nr=1) is None


@responses.activate
def test_event_livetiming_has_not_listed_yet_has_no_session(client, monkeypatch):
    """A scheduled-but-unlisted event (the 2026 US GP before its weekend) must be 'not found'."""
    index = _season([("Australian Grand Prix", 11)], testing=0)
    _add_index(2026, index)
    events = _use_calendar(monkeypatch, 2026, index)
    events.append({"grandPrix": "United States Grand Prix", "kind": "race", "status": "scheduled", "round": 2,
                   "key": None, "aliases": ["USA"], "sessions": []})
    assert client.get_event_session_url(2026, "United States Grand Prix", "R", round_nr=2) is None


@responses.activate
def test_get_event_info_by_key(client, monkeypatch):
    _add_index(2024, SAMPLE_INDEX)
    _use_calendar(monkeypatch, 2024, SAMPLE_INDEX)
    info = client.get_event_info(2024, 1235)
    assert info is not None
    assert info["name"] == "Saudi Arabian Grand Prix"
    assert info["round_nr"] == 2


@responses.activate
def test_get_event_info_by_string_name(client, monkeypatch):
    _add_index(2024, SAMPLE_INDEX)
    _use_calendar(monkeypatch, 2024, SAMPLE_INDEX)
    info = client.get_event_info(2024, "saudi")
    assert info is not None
    assert info["name"] == "Saudi Arabian Grand Prix"


def test_source_token_identifies_the_session_whatever_the_host():
    origin = "https://livetiming.formula1.com/static/2026/2026-03-29_Japanese_Grand_Prix/2026-03-28_Qualifying/"
    proxy = "https://f1-proxy.example.workers.dev/2026/2026-03-29_Japanese_Grand_Prix/2026-03-28_Qualifying/"
    assert source_token(origin) == source_token(proxy) == "2026/2026-03-29_Japanese_Grand_Prix/2026-03-28_Qualifying"
    assert source_token(None) is None


def test_session_aliases_match_case_insensitively(client):
    assert client._session_matches("Q", "Qualifying")
    assert client._session_matches("FP1", "Practice 1")
    assert client._session_matches("R", "Race")
    assert not client._session_matches("Q", "Race")


def test_session_token_builder_emits_acronyms(client):
    tokens = client._build_session_tokens("Practice 1")
    assert "practice 1" in tokens
    assert "practice1" in tokens


def test_qualifying_does_not_match_sprint_qualifying(client):
    # "qualifying" is a literal substring of "sprint qualifying", but they are
    # distinct sessions -- regression test for a bug where requests for
    # "Qualifying" on a sprint weekend silently resolved to Sprint Qualifying's
    # data instead (reported: 2026 Dutch GP quali results showing the sprint
    # shootout's classification).
    assert not client._session_matches("Qualifying", "Sprint Qualifying")
    assert not client._session_matches("Q", "Sprint Qualifying")
    assert client._session_matches("Sprint Qualifying", "Sprint Qualifying")
    assert client._session_matches("SQ", "Sprint Qualifying")


# Sprint weekend where "Sprint Qualifying" is listed before "Qualifying" --
# the ordering under which the substring-matching bug picked the wrong session.
SPRINT_WEEKEND_INDEX = {
    "Year": 2026,
    "Meetings": [
        _meeting(1292, "Dutch Grand Prix", [
            _session("Practice 1", "2026/nl/2026-08-21_Practice_1/", "2026-08-21T10:00:00"),
            _session("Sprint Qualifying", "2026/nl/2026-08-21_Sprint_Qualifying/", "2026-08-21T14:00:00"),
            _session("Sprint", "2026/nl/2026-08-22_Sprint/", "2026-08-22T10:00:00"),
            _session("Qualifying", "2026/nl/2026-08-22_Qualifying/", "2026-08-22T14:00:00"),
        ], 12),
    ],
}


@responses.activate
def test_get_event_session_url_resolves_real_qualifying_on_sprint_weekend(client, monkeypatch):
    _add_index(2026, SPRINT_WEEKEND_INDEX)
    _use_calendar(monkeypatch, 2026, SPRINT_WEEKEND_INDEX)
    url = client.get_event_session_url(2026, "Dutch Grand Prix", "Qualifying", round_nr=1)
    assert url is not None
    assert "Sprint" not in url
    assert "2026-08-22_Qualifying" in url
