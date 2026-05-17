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

    monkeypatch.setattr(mod, "get_season_events", lambda year: SAMPLE_EVENTS)
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
    monkeypatch.setattr(mod, "get_season_events", lambda year: events)
    r = mod.EventResolver(2025)

    with pytest.raises(SessionNotFoundError) as exc_info:
        r.resolve("Grand")
    assert len(exc_info.value.suggestions) == 2


def test_convenience_resolve_event(monkeypatch):
    from src.ingestion import event_resolver as mod

    monkeypatch.setattr(mod, "get_season_events", lambda year: SAMPLE_EVENTS)
    info = mod.resolve_event(2025, "AUS")
    assert info.name == "Australian Grand Prix"
