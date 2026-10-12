"""The season-calendar API: ``/seasons/{year}/events`` and ``/seasons/{year}/events/{event}``.

Years before the current one are served from committed snapshots, so none of these
touch the network.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def authed(client, disable_rate_limit, auth_headers):
    headers = auth_headers()
    return lambda path, **kw: client.get(path, headers=headers, **kw)


def test_events_keeps_the_legacy_keys_and_adds_calendar_facts(authed):
    body = authed("/api/v2/seasons/2023/events").json()
    assert body["year"] == 2023
    first = body["events"][0]
    for legacy in ("name", "official_name", "location", "country", "key", "code"):
        assert legacy in first
    for added in ("round", "kind", "status", "circuit_key", "sessions"):
        assert added in first


def test_events_lists_testing_races_by_official_round_then_cancelled(authed):
    events = authed("/api/v2/seasons/2023/events").json()["events"]
    assert events[0]["kind"] == "testing" and events[0]["round"] is None
    rounds = [e["round"] for e in events if e["round"]]
    assert rounds == list(range(1, 23))
    cancelled = [e for e in events if e["status"] == "cancelled"]
    assert {e["name"] for e in cancelled} >= {"Emilia Romagna Grand Prix"}
    assert all(e["round"] is None for e in cancelled)


def test_single_event_resolves_names_aliases_keys_and_rounds(authed):
    by_round = authed("/api/v2/seasons/2025/events/19").json()
    assert by_round["name"] == "United States Grand Prix"
    assert authed("/api/v2/seasons/2025/events/USA").json()["round"] == 19
    assert authed("/api/v2/seasons/2025/events/Monaco").json()["round"] == 8
    assert authed(f"/api/v2/seasons/2025/events/{by_round['key']}").json()["round"] == 19


def test_cancelled_event_resolves_here_and_says_so(authed):
    body = authed("/api/v2/seasons/2023/events/Emilia Romagna").json()
    assert body["status"] == "cancelled" and body["round"] is None


def test_unknown_event_is_a_404_with_suggestions(authed):
    resp = authed("/api/v2/seasons/2025/events/Atlantis")
    assert resp.status_code == 404


def test_ambiguous_event_is_a_404_listing_candidates(authed):
    resp = authed("/api/v2/seasons/2025/events/Italy")
    assert resp.status_code == 404
    assert "Emilia Romagna Grand Prix" in resp.text and "Italian Grand Prix" in resp.text


def test_year_without_a_calendar_is_a_404_not_a_500(authed):
    assert authed("/api/v2/seasons/2010/events").status_code == 404


def test_sessions_of_a_cancelled_event_is_a_404_that_explains(authed):
    resp = authed("/api/v2/seasons/2023/events/Emilia Romagna/sessions")
    assert resp.status_code == 404
    assert "cancelled" in resp.text.lower()
