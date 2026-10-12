"""The mapping audit: identical payloads under different events are the signature of the
round-vs-meeting mix-up (Japan Q holding Australia's laps)."""
from __future__ import annotations

from src.services import mapping_audit
from src.services.mapping_audit import audit_year

AUS_LAPS = [{"Driver": "RUS", "LapTime": "1:18.518", "Team": "Mercedes"}, {"Driver": "ANT", "LapTime": "1:18.811", "Team": "Mercedes"}]
JPN_LAPS = [{"Driver": "RUS", "LapTime": "1:28.100", "Team": "Mercedes"}, {"Driver": "ANT", "LapTime": "1:28.300", "Team": "Mercedes"}]


def _gp(gp_id, name, *entries):
    return {"gp_id": gp_id, "name": name, "sessions": [
        {"session_type": session, "data": [{"data_type": dtype, "data": data, **({"source": src} if src else {})}]}
        for session, dtype, data, src in entries
    ]}


def test_same_payload_under_two_events_is_flagged_with_a_ready_scope(monkeypatch):
    monkeypatch.setattr(mapping_audit, "_calendar_gp_ids", lambda y: {"2026_AUS": "Australian", "2026_JPN": "Japanese", "2026_CHN": "Chinese"})
    gps = [
        _gp("2026_AUS", "AustralianGrandPrix", ("Q", "qualifying_results", AUS_LAPS, None)),
        _gp("2026_JPN", "JapaneseGrandPrix", ("Q", "qualifying_results", AUS_LAPS, None)),     # poisoned
        _gp("2026_CHN", "ChineseGrandPrix", ("Q", "qualifying_results", JPN_LAPS, None)),
    ]
    report = audit_year(2026, gps)

    assert len(report["duplicates"]) == 1
    dup = report["duplicates"][0]
    assert dup["gp_ids"] == ["2026_AUS", "2026_JPN"]
    assert {"year": 2026, "gp": "2026_JPN", "session": "Q", "data_type": "qualifying_results"} in dup["scopes"]
    assert report["entries_without_source"] == 3 and report["entries_with_source"] == 0


def test_distinct_payloads_and_trivial_payloads_are_not_flagged(monkeypatch):
    monkeypatch.setattr(mapping_audit, "_calendar_gp_ids", lambda y: {"2026_AUS": "A", "2026_JPN": "J"})
    gps = [
        _gp("2026_AUS", "A", ("Q", "qualifying_results", AUS_LAPS, "s/a"), ("R", "positions", [], "s/a")),
        _gp("2026_JPN", "J", ("Q", "qualifying_results", JPN_LAPS, "s/j"), ("R", "positions", [], "s/j")),
    ]
    report = audit_year(2026, gps)
    assert report["duplicates"] == []
    assert report["entries_with_source"] == 4


def test_gp_ids_the_calendar_does_not_know_are_reported(monkeypatch):
    monkeypatch.setattr(mapping_audit, "_calendar_gp_ids", lambda y: {"2026_AUS": "A"})
    report = audit_year(2026, [_gp("2026_AUS", "A"), _gp("2026_GRA", "Gran Premio de Barcelona")])
    assert report["unknown_gp_ids"] == ["2026_GRA"]


def test_missing_calendar_is_reported_not_fatal(monkeypatch):
    monkeypatch.setattr(mapping_audit, "_calendar_gp_ids", lambda y: None)
    report = audit_year(2026, [_gp("2026_AUS", "A")])
    assert report["calendar_available"] is False and report["unknown_gp_ids"] == []
