"""Unit tests for the livetiming-enrichment helpers in :mod:`src.ingestion.reference`.

``_match_curated_to_livetiming`` is the seam that used to get Barcelona and
Madrid (both ``country == "Spain"`` in 2026) mixed up by keying a lookup dict
on country alone. These tests pin its three-pass claim order directly, then
check the wrapper functions that build on it.
"""
from __future__ import annotations

from src.ingestion import reference as ref

CURATED = [
    {"grandPrix": "Gran Premio de Barcelona-Catalunya", "circuit": "Circuit de Barcelona-Catalunya",
     "country": "Spain"},
    {"grandPrix": "Spanish Grand Prix", "circuit": "Madring", "country": "Spain"},
    {"grandPrix": "Bahrain Grand Prix", "circuit": "Bahrain International Circuit", "country": "Bahrain"},
]

LIVE = [
    {"grandPrix": "Pre-Season Testing", "circuit": "Sakhir", "country": "Bahrain", "key": 1304},
    {"grandPrix": "Pre-Season Testing", "circuit": "Sakhir", "country": "Bahrain", "key": 1305},
    {"grandPrix": "Barcelona Grand Prix", "circuit": "Catalunya", "country": "Spain",
     "key": 1287, "circuitKey": 15},
    {"grandPrix": "Spanish Grand Prix", "circuit": "Madring", "country": "Spain",
     "key": 1294, "circuitKey": 153},
]


# ---------------------------------------------------------------------------
# _match_curated_to_livetiming
# ---------------------------------------------------------------------------

def test_match_barcelona_via_circuit_containment():
    matches = ref._match_curated_to_livetiming(CURATED, LIVE)
    assert matches[0]["key"] == 1287


def test_match_madrid_via_exact_name():
    matches = ref._match_curated_to_livetiming(CURATED, LIVE)
    assert matches[1]["key"] == 1294


def test_match_bahrain_ambiguous_testing_meetings_is_none():
    matches = ref._match_curated_to_livetiming(CURATED, LIVE)
    assert matches[2] is None


def test_match_single_spain_season_matches_by_country_alone():
    curated = [
        {"grandPrix": "Gran Premio de Barcelona-Catalunya", "circuit": "Circuit de Barcelona-Catalunya",
         "country": "Spain"},
    ]
    live = [{"grandPrix": "Some Other Name", "circuit": "Some Other Circuit", "country": "Spain", "key": 42}]

    matches = ref._match_curated_to_livetiming(curated, live)
    assert matches[0]["key"] == 42


def test_match_empty_circuit_names_never_match():
    curated = [{"grandPrix": "Unknown Race", "circuit": "", "country": "Nowhere"}]
    live = [{"grandPrix": "Other Race", "circuit": "", "country": "Elsewhere", "key": 1}]

    matches = ref._match_curated_to_livetiming(curated, live)
    assert matches[0] is None


# ---------------------------------------------------------------------------
# _enrich_curated_with_livetiming
# ---------------------------------------------------------------------------

def test_enrich_curated_with_livetiming_fills_gaps_keeps_curated_circuit(monkeypatch):
    live = [
        {"grandPrix": "Barcelona Grand Prix", "circuit": "Catalunya", "country": "Spain",
         "key": 1287, "circuitKey": 15, "location": "Barcelona"},
    ]
    curated = [
        {"grandPrix": "Gran Premio de Barcelona-Catalunya", "circuit": "Circuit de Barcelona-Catalunya",
         "country": "Spain"},
    ]
    monkeypatch.setattr(ref, "_fetch_livetiming_meetings_safe", lambda year: live)

    enriched = ref._enrich_curated_with_livetiming(2026, curated)

    assert len(enriched) == 1
    record = enriched[0]
    assert record["key"] == 1287
    assert record["circuitKey"] == 15
    assert record["location"] == "Barcelona"
    # Curated circuit string is never overwritten by livetiming's.
    assert record["circuit"] == "Circuit de Barcelona-Catalunya"


# ---------------------------------------------------------------------------
# _adapt_livetiming_index_to_season_events
# ---------------------------------------------------------------------------

def test_adapt_livetiming_index_carries_circuit_key_and_location():
    index = {"Meetings": [{
        "Number": 5, "Name": "Test GP", "OfficialName": "OFFICIAL TEST GP",
        "Circuit": {"Key": 99, "ShortName": "Testcirc"},
        "Location": "Testville", "Country": {"Name": "Testland"},
        "Code": "TST", "Key": 5000,
        "Sessions": [{"Name": "Race", "StartDate": "2026-01-01T10:00:00", "EndDate": "2026-01-01T12:00:00"}],
    }]}

    events = ref._adapt_livetiming_index_to_season_events(2026, index)

    assert len(events) == 1
    event = events[0]
    assert event["circuitKey"] == 99
    assert event["location"] == "Testville"
    assert event["round"] == 5
    assert event["grandPrix"] == "Test GP"
