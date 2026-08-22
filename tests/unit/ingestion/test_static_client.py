"""Unit tests for src.ingestion.static_client.F1StaticClient."""
import json

import pytest
import responses

from src.ingestion.static_client import F1StaticClient


SAMPLE_INDEX = {
    "Year": 2024,
    "Meetings": [
        {
            "Key": 1234,
            "Name": "Bahrain Grand Prix",
            "OfficialName": "FORMULA 1 GULF AIR BAHRAIN GRAND PRIX 2024",
            "Code": "BHR",
            "Sessions": [
                {"Name": "Practice 1", "Path": "2024/2024-03-01_Bahrain_Grand_Prix/2024-03-01_Practice_1/"},
                {"Name": "Qualifying", "Path": "2024/2024-03-01_Bahrain_Grand_Prix/2024-03-01_Qualifying/"},
                {"Name": "Race", "Path": "2024/2024-03-02_Bahrain_Grand_Prix/2024-03-02_Race/"},
            ],
        },
        {
            "Key": 1235,
            "Name": "Saudi Arabian Grand Prix",
            "OfficialName": "STC SAUDI ARABIAN GRAND PRIX 2024",
            "Code": "KSA",
            "Sessions": [
                {"Name": "Race", "Path": "2024/some-saudi-path/Race/"},
            ],
        },
    ],
}


@pytest.fixture
def client():
    return F1StaticClient()


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
def test_get_event_session_url_resolves_qualifying(client):
    responses.add(
        responses.GET,
        "https://livetiming.formula1.com/static/2024/Index.json",
        json=SAMPLE_INDEX,
        status=200,
    )
    url = client.get_event_session_url(2024, "Bahrain", "Q")
    assert url is not None
    assert "Qualifying" in url


@responses.activate
def test_get_event_session_url_returns_none_for_unknown_event(client):
    responses.add(
        responses.GET,
        "https://livetiming.formula1.com/static/2024/Index.json",
        json=SAMPLE_INDEX,
        status=200,
    )
    assert client.get_event_session_url(2024, "Atlantis", "Race") is None


# Livetiming index where pre-season testing occupies the first slot, shifting
# positional round numbers out of sync with curated round numbers (2026-style).
TESTING_OFFSET_INDEX = {
    "Year": 2026,
    "Meetings": [
        {
            "Key": 1000,
            "Name": "Pre-Season Testing",
            "Sessions": [
                {"Name": "Practice 1", "Path": "2026/testing/2026-01-01_Practice_1/"},
            ],
        },
        {
            "Key": 1279,
            "Name": "Australian Grand Prix",
            "Sessions": [
                {"Name": "Practice 1", "Path": "2026/aus/2026-03-06_Practice_1/"},
                {"Name": "Qualifying", "Path": "2026/aus/2026-03-07_Qualifying/"},
                {"Name": "Race", "Path": "2026/aus/2026-03-08_Race/"},
            ],
        },
    ],
}


@responses.activate
def test_positional_round_falls_back_to_name_on_offset(client):
    # Curated round 1 (Australian GP) collides positionally with Pre-Season
    # Testing; the URL must resolve to the real GP session, not testing.
    responses.add(
        responses.GET,
        "https://livetiming.formula1.com/static/2026/Index.json",
        json=TESTING_OFFSET_INDEX,
        status=200,
    )
    url = client.get_event_session_url(2026, "Australian Grand Prix", "R", round_nr=1)
    assert url is not None
    assert "aus" in url and "Race" in url


@responses.activate
def test_positional_round_trusted_when_it_matches(client):
    # When the positional meeting *does* match the requested event, use it.
    responses.add(
        responses.GET,
        "https://livetiming.formula1.com/static/2026/Index.json",
        json=TESTING_OFFSET_INDEX,
        status=200,
    )
    url = client.get_event_session_url(2026, "Pre-Season Testing", "FP1", round_nr=1)
    assert url is not None
    assert "testing" in url


@responses.activate
def test_get_timing_data_url_appends_filename(client):
    responses.add(
        responses.GET,
        "https://livetiming.formula1.com/static/2024/Index.json",
        json=SAMPLE_INDEX,
        status=200,
    )
    url = client.get_timing_data_url(2024, "Bahrain", "Race")
    assert url is not None
    assert url.endswith("TimingData.jsonStream")


@responses.activate
def test_get_event_name_by_round(client):
    responses.add(
        responses.GET,
        "https://livetiming.formula1.com/static/2024/Index.json",
        json=SAMPLE_INDEX,
        status=200,
    )
    assert client.get_event_name(2024, 1) == "Bahrain Grand Prix"
    assert client.get_event_name(2024, 2) == "Saudi Arabian Grand Prix"
    assert client.get_event_name(2024, 99) is None


@responses.activate
def test_get_event_info_by_key(client):
    responses.add(
        responses.GET,
        "https://livetiming.formula1.com/static/2024/Index.json",
        json=SAMPLE_INDEX,
        status=200,
    )
    info = client.get_event_info(2024, 1235)
    assert info is not None
    assert info["name"] == "Saudi Arabian Grand Prix"
    assert info["round_nr"] == 2


@responses.activate
def test_get_event_info_by_string_name(client):
    responses.add(
        responses.GET,
        "https://livetiming.formula1.com/static/2024/Index.json",
        json=SAMPLE_INDEX,
        status=200,
    )
    info = client.get_event_info(2024, "saudi")
    assert info is not None
    assert info["name"] == "Saudi Arabian Grand Prix"


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
        {
            "Key": 1292,
            "Name": "Dutch Grand Prix",
            "Sessions": [
                {"Name": "Practice 1", "Path": "2026/nl/2026-08-21_Practice_1/"},
                {"Name": "Sprint Qualifying", "Path": "2026/nl/2026-08-21_Sprint_Qualifying/"},
                {"Name": "Sprint", "Path": "2026/nl/2026-08-22_Sprint/"},
                {"Name": "Qualifying", "Path": "2026/nl/2026-08-22_Qualifying/"},
            ],
        },
    ],
}


@responses.activate
def test_get_event_session_url_resolves_real_qualifying_on_sprint_weekend(client):
    responses.add(
        responses.GET,
        "https://livetiming.formula1.com/static/2026/Index.json",
        json=SPRINT_WEEKEND_INDEX,
        status=200,
    )
    url = client.get_event_session_url(2026, "Dutch Grand Prix", "Qualifying", round_nr=1)
    assert url is not None
    assert "Sprint" not in url
    assert "2026-08-22_Qualifying" in url
