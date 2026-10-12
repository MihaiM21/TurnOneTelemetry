"""Unit tests for :mod:`src.ingestion.event_resolver`.

The resolver is the single seam every V2 caller goes through to translate
``Union[int, str]`` request inputs into a canonical event. These tests pin
its behavior across round numbers, livetiming Keys, exact matches, fuzzy
matches, ambiguity, and out-of-range inputs.
"""
from __future__ import annotations

import pytest

from src.core.exceptions import SessionNotFoundError


SAMPLE_EVENTS = [
    {
        "round": 1,
        "grandPrix": "Australian Grand Prix",
        "officialName": "FORMULA 1 AUSTRALIAN GRAND PRIX 2025",
        "code": "AUS",
        "key": 1300,
        "circuit": "Melbourne Grand Prix Circuit",
        "country": "Australia",
    },
    {
        "round": 2,
        "grandPrix": "Chinese Grand Prix",
        "officialName": "FORMULA 1 HEINEKEN CHINESE GRAND PRIX 2025",
        "code": "CHN",
        "key": 1301,
        "circuit": "Shanghai International Circuit",
        "country": "China",
    },
    {
        "round": 3,
        "grandPrix": "São Paulo Grand Prix",
        "officialName": "FORMULA 1 SAO PAULO GRAND PRIX",
        "code": "SAP",
        "key": 1302,
        "circuit": "Autódromo José Carlos Pace",
        "country": "Brazil",
    },
]


@pytest.fixture
def resolver(monkeypatch):
    """Return an :class:`EventResolver` seeded with the sample season."""
    from src.ingestion import event_resolver as mod

    monkeypatch.setattr(mod, "get_season_calendar", lambda year: SAMPLE_EVENTS)
    return mod.EventResolver(2025)


def test_round_number_happy_path(resolver):
    info = resolver.resolve(1)
    assert info.round_nr == 1
    assert info.name == "Australian Grand Prix"
    assert info.code == "AUS"
    assert info.key == 1300


def test_round_number_as_digit_string(resolver):
    info = resolver.resolve("2")
    assert info.round_nr == 2
    assert info.name == "Chinese Grand Prix"


def test_round_number_out_of_range_carries_valid_rounds(resolver):
    with pytest.raises(SessionNotFoundError) as exc_info:
        resolver.resolve(99)
    assert exc_info.value.valid_rounds == [1, 2, 3]


def test_meeting_key_lookup(resolver):
    info = resolver.resolve(1301)
    assert info.round_nr == 2
    assert info.name == "Chinese Grand Prix"


def test_exact_code_match(resolver):
    info = resolver.resolve("AUS")
    assert info.name == "Australian Grand Prix"


def test_exact_country_match(resolver):
    info = resolver.resolve("China")
    assert info.name == "Chinese Grand Prix"


def test_substring_match(resolver):
    info = resolver.resolve("Chinese")
    assert info.name == "Chinese Grand Prix"


def test_diacritic_stripping(resolver):
    # "Sao Paulo" should match "São Paulo" through fold normalization.
    info = resolver.resolve("Sao Paulo")
    assert info.name == "São Paulo Grand Prix"


def test_unknown_string_carries_suggestions(resolver):
    with pytest.raises(SessionNotFoundError) as exc_info:
        resolver.resolve("Mars Grand Prix")
    # Suggestions are best-effort but should be present.
    assert isinstance(exc_info.value.suggestions, list)


def test_empty_identifier_rejected(resolver):
    with pytest.raises(SessionNotFoundError):
        resolver.resolve("")


def test_ambiguous_match_lists_candidates(monkeypatch):
    """Multiple substring matches should raise with the candidates listed."""
    from src.ingestion import event_resolver as mod

    events = [
        {"round": 1, "grandPrix": "Grand Prix of Argentina", "code": "ARG"},
        {"round": 2, "grandPrix": "Grand Prix of Australia", "code": "AUS"},
    ]
    monkeypatch.setattr(mod, "get_season_calendar", lambda year: events)
    r = mod.EventResolver(2025)

    with pytest.raises(SessionNotFoundError) as exc_info:
        r.resolve("Grand")
    assert len(exc_info.value.suggestions) == 2


def test_convenience_resolve_event(monkeypatch):
    from src.ingestion import event_resolver as mod

    monkeypatch.setattr(mod, "get_season_calendar", lambda year: SAMPLE_EVENTS)
    info = mod.resolve_event(2025, "AUS")
    assert info.name == "Australian Grand Prix"


# ----------------------------------------------------------------------
# Two rounds in the same country (2026: Barcelona and Madrid are both
# "Spain") -- a plain country match must not silently pick one.
# ----------------------------------------------------------------------

SPAIN_EVENTS = [
    {
        "round": 1,
        "grandPrix": "Gran Premio de Barcelona-Catalunya",
        "circuit": "Circuit de Barcelona-Catalunya",
        "country": "Spain",
        "location": "Barcelona",
    },
    {
        "round": 2,
        "grandPrix": "Spanish Grand Prix",
        "circuit": "Madring",
        "country": "Spain",
        "location": "Madrid",
        "circuitKey": 153,
    },
]


@pytest.fixture
def spain_resolver(monkeypatch):
    from src.ingestion import event_resolver as mod

    monkeypatch.setattr(mod, "get_season_calendar", lambda year: SPAIN_EVENTS)
    return mod.EventResolver(2026)


def test_country_only_match_is_ambiguous_lists_both(spain_resolver):
    with pytest.raises(SessionNotFoundError) as exc_info:
        spain_resolver.resolve("Spain")
    assert set(exc_info.value.suggestions) == {
        "Gran Premio de Barcelona-Catalunya", "Spanish Grand Prix",
    }


def test_exact_grand_prix_name_still_resolves(spain_resolver):
    info = spain_resolver.resolve("Spanish Grand Prix")
    assert info.name == "Spanish Grand Prix"


def test_resolve_via_location_field(spain_resolver):
    info = spain_resolver.resolve("Madrid")
    assert info.name == "Spanish Grand Prix"


def test_event_info_circuit_key_populated_from_circuit_key(spain_resolver):
    info = spain_resolver.resolve("Spanish Grand Prix")
    assert info.circuit_key == 153


# ----------------------------------------------------------------------
# Real calendars (committed snapshots / 2026 built from the overrides)
# ----------------------------------------------------------------------

def _calendar_2026():
    """2026 as the live build produces it, from offline inputs."""
    from src.domain.data.seasons import f1_2026_races_data
    from src.ingestion import calendar as cal

    def meeting(key, number, name, location, country, circuit, circuit_key, day, official=""):
        return {
            "Key": key, "Number": number, "Name": name, "OfficialName": official or name.upper(),
            "Location": location, "Country": {"Name": country}, "Circuit": {"Key": circuit_key, "ShortName": circuit},
            "Code": f"F1{key}",
            "Sessions": [{"Name": "Race", "Type": "Race", "StartDate": f"{day}T14:00:00", "EndDate": f"{day}T16:00:00",
                          "GmtOffset": "00:00:00", "Path": f"2026/{day}_{name.replace(' ', '_')}/{day}_Race/"}],
        }

    index = {"Meetings": [
        {**meeting(1304, 1, "Pre-Season Testing", "Bahrain", "Bahrain", "Sakhir", 63, "2026-02-11"), "Sessions": []},
        meeting(1279, 1, "Australian Grand Prix", "Melbourne", "Australia", "Melbourne", 10, "2026-03-08"),
        meeting(1280, 2, "Chinese Grand Prix", "Shanghai", "China", "Shanghai", 49, "2026-03-15"),
        meeting(1281, 3, "Japanese Grand Prix", "Suzuka", "Japan", "Suzuka", 46, "2026-03-29"),
        meeting(1284, 6, "Miami Grand Prix", "Miami Gardens", "United States", "Miami", 151, "2026-05-03"),
        meeting(1285, 5, "Canadian Grand Prix", "Montreal", "Canada", "Montreal", 23, "2026-05-24"),
        meeting(1286, 6, "Monaco Grand Prix", "Monte Carlo", "Monaco", "Monte Carlo", 22, "2026-06-07"),
        meeting(1287, 7, "Barcelona Grand Prix", "Barcelona", "Spain", "Catalunya", 15, "2026-06-14"),
        meeting(1294, 14, "Spanish Grand Prix", "Madrid", "Spain", "Madring", 153, "2026-09-13"),
        meeting(1308, 16, "Bahrain Grand Prix", "Kuala Lumpur", "Bahrain", "Kuala Lumpur", 12, "2026-10-04"),
        meeting(1296, 17, "Singapore Grand Prix", "Marina Bay", "Singapore", "Singapore", 61, "2026-10-11"),
    ]}
    live = cal.adapt_livetiming_index(index)
    curated = cal.adapt_curated(f1_2026_races_data, cal.load_overrides())
    return cal.apply_runtime_status(cal.build_calendar(2026, live=live, curated=curated))


@pytest.fixture
def r2026(monkeypatch):
    from src.ingestion import event_resolver as mod

    events = _calendar_2026()
    monkeypatch.setattr(mod, "get_season_calendar", lambda year: events)
    return mod.EventResolver(2026)


def test_official_round_numbers_skip_cancelled_and_testing(r2026):
    assert r2026.resolve(3).name == "Japanese Grand Prix"
    assert r2026.resolve(4).name == "Miami Grand Prix"
    assert r2026.resolve(5).name == "Canadian Grand Prix"
    assert r2026.resolve(7).name == "Barcelona Grand Prix"


def test_bahrain_held_in_malaysia_wins_over_the_cancelled_sakhir_round(r2026):
    for ident in ("Bahrain", "Malaysia", "Sepang", "Kuala Lumpur", "Bahrain Grand Prix", 1308):
        info = r2026.resolve(ident)
        assert info.key == 1308, ident
        assert info.status != "cancelled"
        assert info.round_nr == r2026.resolve(1308).round_nr


def test_cancelled_event_resolves_only_when_asked_and_explains_itself(r2026):
    with pytest.raises(SessionNotFoundError) as exc_info:
        r2026.resolve("Sakhir")
    assert "cancelled" in exc_info.value.reason.lower()
    assert "Malaysia" in exc_info.value.reason

    info = r2026.resolve("Sakhir", allow_cancelled=True)
    assert info.status == "cancelled"
    assert info.replaced_by == "Bahrain Grand Prix"


def test_saudi_2026_is_cancelled(r2026):
    with pytest.raises(SessionNotFoundError) as exc_info:
        r2026.resolve("Saudi Arabian Grand Prix")
    assert "cancelled" in exc_info.value.reason.lower()


def test_barcelona_and_madrid_are_distinct_and_spain_is_ambiguous(r2026):
    assert r2026.resolve("Barcelona").key == 1287
    assert r2026.resolve("Gran Premio de Barcelona-Catalunya").key == 1287
    assert r2026.resolve("Madrid").key == 1294
    assert r2026.resolve("Barcelona").gp_id != r2026.resolve("Madrid").gp_id
    with pytest.raises(SessionNotFoundError):
        r2026.resolve("Spain")


def test_usa_and_united_states_reach_the_us_grand_prix_before_livetiming_lists_it(r2026):
    for ident in ("USA", "United States", "USA Grand Prix", "United States Grand Prix", "Austin"):
        info = r2026.resolve(ident)
        assert info.name == "United States Grand Prix", ident
        assert info.key is None  # livetiming has not published it yet


def test_testing_is_reachable_by_key_never_by_round(r2026):
    info = r2026.resolve(1304)
    assert info.kind == "testing"
    assert info.round_nr == 0
    assert r2026.resolve(1).name == "Australian Grand Prix"


def test_2025_snapshot_us_grand_prix_has_a_key_and_official_round(monkeypatch):
    from src.ingestion import calendar as cal
    from src.ingestion import event_resolver as mod

    events = cal.apply_runtime_status(cal.load_snapshot(2025))
    monkeypatch.setattr(mod, "get_season_calendar", lambda year: events)
    resolver = mod.EventResolver(2025)
    assert resolver.resolve("USA").round_nr == 19
    assert resolver.resolve(19).key is not None
    assert resolver.resolve(8).name == "Monaco Grand Prix"
    assert resolver.resolve(7).name == "Emilia Romagna Grand Prix"


def test_2020_bahrain_and_sakhir_are_different_events(monkeypatch):
    from src.ingestion import calendar as cal
    from src.ingestion import event_resolver as mod

    events = cal.apply_runtime_status(cal.load_snapshot(2020))
    monkeypatch.setattr(mod, "get_season_calendar", lambda year: events)
    resolver = mod.EventResolver(2020)
    assert resolver.resolve("Bahrain").name == "Bahrain Grand Prix"
    assert resolver.resolve("Sakhir").name == "Sakhir Grand Prix"
    with pytest.raises(SessionNotFoundError) as exc_info:
        resolver.resolve("Australian Grand Prix")
    assert "cancelled" in exc_info.value.reason.lower()
