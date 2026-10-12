"""V1 (FastF1) and V2 (calendar) must mean the same race by the same round number."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from src.services import orchestrator_helpers
from src.workers import processor as processor_mod
from src.workers.processor import BackgroundProcessor


def _schedule(now):
    """FastF1-shaped schedule: testing is round 0; round 2 is a sprint weekend."""
    recent = now - timedelta(hours=3)
    rows = [
        {"RoundNumber": 0, "EventName": "Pre-Season Testing",
         "Session1": "Practice 1", "Session1Date": recent, "Session2": None, "Session2Date": pd.NaT,
         "Session3": None, "Session3Date": pd.NaT, "Session4": None, "Session4Date": pd.NaT,
         "Session5": None, "Session5Date": pd.NaT},
        {"RoundNumber": 2, "EventName": "Chinese Grand Prix",
         "Session1": "Practice 1", "Session1Date": now - timedelta(days=3),
         "Session2": "Sprint Qualifying", "Session2Date": recent,
         "Session3": "Sprint", "Session3Date": now - timedelta(days=2),
         "Session4": "Qualifying", "Session4Date": now - timedelta(days=2),
         "Session5": "Race", "Session5Date": now + timedelta(days=1)},
    ]
    return pd.DataFrame(rows)


def test_upcoming_sessions_use_the_official_round_and_real_session_names(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(processor_mod.fastf1, "get_event_schedule", lambda year: _schedule(now))
    proc = BackgroundProcessor.__new__(BackgroundProcessor)   # no Mongo / generator needed

    upcoming = proc.get_upcoming_sessions()

    assert [(gp, session, name) for _y, gp, session, _d, name in upcoming] == [(2, "SQ", "Chinese Grand Prix")]
    # pre-season testing (round 0) is never processed, and the sprint-qualifying slot is not mislabelled "FP2"


def _event(round_nr, name, end, sprint=False):
    return {
        "round": round_nr, "grandPrix": name, "circuit": "c", "country": "x", "hasSprint": sprint, "key": 1000 + round_nr,
        "sessions": [{"name": "Qualifying", "startTime": end - timedelta(hours=1), "endTime": end},
                     {"name": "Race", "startTime": end, "endTime": end + timedelta(hours=2)}],
    }


def test_latest_finished_session_reports_the_official_round(monkeypatch):
    now = datetime.now(timezone.utc)
    events = [_event(3, "Japanese Grand Prix", now - timedelta(days=9)),
              _event(4, "Miami Grand Prix", now - timedelta(days=2)),
              _event(5, "Canadian Grand Prix", now + timedelta(days=10))]
    monkeypatch.setattr(orchestrator_helpers, "get_season_events", lambda y: events if y == now.year else [])

    for latest in (orchestrator_helpers.get_latest_finished_session(), orchestrator_helpers.get_latest_finished_session_v2()):
        assert latest["round"] == 4
        assert latest["grandPrix"] == "Miami Grand Prix"
        assert latest["session_name"] == "R"


def test_v2_latest_session_skips_events_livetiming_has_not_listed(monkeypatch):
    now = datetime.now(timezone.utc)
    unlisted = {**_event(6, "Monaco Grand Prix", now - timedelta(hours=5)), "key": None}
    listed = _event(5, "Canadian Grand Prix", now - timedelta(days=7))
    monkeypatch.setattr(orchestrator_helpers, "get_season_events", lambda y: [listed, unlisted] if y == now.year else [])
    assert orchestrator_helpers.get_latest_finished_session_v2()["round"] == 5
