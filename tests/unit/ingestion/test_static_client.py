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
