"""Offline tests for :mod:`src.ingestion.calendar` -- the season-calendar builder.

Pins the rules that make the calendar trustworthy: official round numbers (no
testing, no cancelled slots, no livetiming ``Number`` gaps or duplicates), the
replaced / cancelled events, and the committed historical snapshots.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.ingestion import calendar as cal


def _meeting(key, number, name, day, *, location="", country="", circuit="", circuit_key=None, sprint=False):
    sessions = [
        {"Name": "Qualifying", "Type": "Qualifying", "StartDate": f"{day[:8]}{int(day[8:]) - 1:02d}T15:00:00",
         "EndDate": f"{day[:8]}{int(day[8:]) - 1:02d}T16:00:00", "GmtOffset": "02:00:00",
         "Path": f"2026/{day}_{name.replace(' ', '_')}/Q/"},
        {"Name": "Race", "Type": "Race", "StartDate": f"{day}T15:00:00", "EndDate": f"{day}T17:00:00",
         "GmtOffset": "02:00:00", "Path": f"2026/{day}_{name.replace(' ', '_')}/R/"},
    ]
    if sprint:
        sessions.insert(0, {"Name": "Sprint", "Type": "Race", "StartDate": f"{day[:8]}{int(day[8:]) - 1:02d}T10:00:00",
                            "EndDate": f"{day[:8]}{int(day[8:]) - 1:02d}T11:00:00", "GmtOffset": "02:00:00", "Path": "x/S/"})
    return {
        "Key": key, "Number": number, "Name": name, "OfficialName": name.upper(), "Code": f"C{key}",
        "Location": location or name, "Country": {"Name": country or name},
        "Circuit": {"Key": circuit_key or key, "ShortName": circuit or name}, "Sessions": sessions,
    }


def _live(*meetings):
    return cal.adapt_livetiming_index({"Meetings": list(meetings)})


def _by_name(events, name):
    return [e for e in events if e["grandPrix"] == name]


# --------------------------------------------------------------------------- rounds

def test_official_rounds_ignore_testing_and_the_livetiming_number():
    live = _live(
        _meeting(1, 1, "Pre-Season Testing", "2026-02-13"),
        _meeting(2, 1, "Australian Grand Prix", "2026-03-08"),
        _meeting(3, 6, "Miami Grand Prix", "2026-05-03"),     # livetiming says 6 ...
        _meeting(4, 6, "Monaco Grand Prix", "2026-06-07"),    # ... for both
    )
    events = cal.build_calendar(2026, live=live, overrides={})
    rounds = {e["grandPrix"]: e["round"] for e in events}
    assert rounds == {"Pre-Season Testing": None, "Australian Grand Prix": 1, "Miami Grand Prix": 2, "Monaco Grand Prix": 3}
    assert _by_name(events, "Pre-Season Testing")[0]["kind"] == cal.KIND_TESTING


def test_rounds_follow_race_date_not_index_order():
    live = _live(
        _meeting(1, 1, "Late Grand Prix", "2026-09-06"),
        _meeting(2, 2, "Early Grand Prix", "2026-03-08"),
    )
    events = cal.build_calendar(2026, live=live, overrides={})
    assert [(e["grandPrix"], e["round"]) for e in events] == [("Early Grand Prix", 1), ("Late Grand Prix", 2)]


def test_reference_schedule_decides_rounds_and_fills_rounds_livetiming_lacks():
    """A truncated livetiming window (2024 starts at round 10) still yields the full calendar."""
    live = _live(_meeting(10, 10, "Spanish Grand Prix", "2026-06-21"))
    reference = cal.adapt_reference_schedule([
        {"round": "1", "raceName": "Bahrain Grand Prix", "date": "2026-03-01",
         "Circuit": {"circuitName": "Sakhir", "Location": {"locality": "Sakhir", "country": "Bahrain"}}},
        {"round": "2", "raceName": "Spanish Grand Prix", "date": "2026-06-21",
         "Circuit": {"circuitName": "Catalunya", "Location": {"locality": "Barcelona", "country": "Spain"}}},
    ])
    events = cal.build_calendar(2026, live=live, reference=reference, overrides={})
    assert [(e["round"], e["grandPrix"], e["key"]) for e in events] == [
        (1, "Bahrain Grand Prix", None), (2, "Spanish Grand Prix", 10),
    ]


# --------------------------------------------------------------------------- 2026 edge cases

@pytest.fixture
def curated_2026():
    from src.domain.data.seasons import f1_2026_races_data
    return cal.adapt_curated(f1_2026_races_data, cal.load_overrides())


def _live_2026():
    return _live(
        _meeting(1279, 1, "Australian Grand Prix", "2026-03-08", location="Melbourne", country="Australia", circuit="Melbourne"),
        _meeting(1287, 7, "Barcelona Grand Prix", "2026-06-14", location="Barcelona", country="Spain", circuit="Catalunya"),
        _meeting(1294, 14, "Spanish Grand Prix", "2026-09-13", location="Madrid", country="Spain", circuit="Madring", circuit_key=153),
        _meeting(1308, 16, "Bahrain Grand Prix", "2026-10-04", location="Kuala Lumpur", country="Bahrain", circuit="Kuala Lumpur", circuit_key=12),
        _meeting(1296, 17, "Singapore Grand Prix", "2026-10-11", location="Marina Bay", country="Singapore", circuit="Singapore"),
    )


def test_bahrain_held_in_malaysia_replaces_the_cancelled_sakhir_round(curated_2026):
    events = cal.build_calendar(2026, live=_live_2026(), curated=curated_2026)
    bahrain = _by_name(events, "Bahrain Grand Prix")
    assert len(bahrain) == 2

    held = next(e for e in bahrain if e["status"] != cal.STATUS_CANCELLED)
    assert held["key"] == 1308 and held["round"] is not None
    assert held["circuitKey"] == 12                      # the Sepang layout, not Sakhir's 63
    assert held["replaces"] == "Bahrain Grand Prix"
    assert {"Malaysia", "Sepang", "Kuala Lumpur"} <= set(held["aliases"])

    cancelled = next(e for e in bahrain if e["status"] == cal.STATUS_CANCELLED)
    assert cancelled["round"] is None and cancelled["key"] is None
    assert cancelled["originalRound"] == 4
    assert cancelled["replacedByKey"] == 1308


def test_saudi_arabian_gp_is_cancelled_and_cannot_claim_a_meeting(curated_2026):
    events = cal.build_calendar(2026, live=_live_2026(), curated=curated_2026)
    saudi = _by_name(events, "Saudi Arabian Grand Prix")
    assert len(saudi) == 1
    assert saudi[0]["status"] == cal.STATUS_CANCELLED and saudi[0]["round"] is None


def test_barcelona_and_madrid_join_the_right_meetings_and_get_distinct_gp_ids(curated_2026):
    events = cal.build_calendar(2026, live=_live_2026(), curated=curated_2026)
    barcelona = _by_name(events, "Barcelona Grand Prix")[0]
    madrid = _by_name(events, "Spanish Grand Prix")[0]
    assert (barcelona["key"], madrid["key"]) == (1287, 1294)
    assert "Gran Premio de Barcelona-Catalunya" in barcelona["aliases"]
    assert barcelona["gp_id"] != madrid["gp_id"]


def test_future_events_exist_without_a_key_until_livetiming_lists_them(curated_2026):
    events = cal.build_calendar(2026, live=_live_2026(), curated=curated_2026)
    usa = _by_name(events, "United States Grand Prix")[0]      # curated "USA Grand Prix", canonicalised
    assert usa["key"] is None and usa["status"] == cal.STATUS_SCHEDULED
    assert usa["sessions"], "future sessions come from the curated schedule"
    assert usa["round"] > _by_name(events, "Singapore Grand Prix")[0]["round"]
    assert "USA" in usa["aliases"] and "USA Grand Prix" in usa["aliases"]


def test_official_2026_round_count(curated_2026):
    events = cal.build_calendar(2026, live=_live_2026(), curated=curated_2026)
    rounds = sorted(e["round"] for e in events if e["round"])
    assert rounds == list(range(1, 24))  # 22 curated (24 - Sakhir - Saudi) + the Malaysia replacement


# --------------------------------------------------------------------------- runtime status

def test_runtime_status_marks_finished_events_completed():
    live = _live(_meeting(1, 1, "Past Grand Prix", "2026-03-08"), _meeting(2, 2, "Future Grand Prix", "2026-09-06"))
    events = cal.build_calendar(2026, live=live, overrides={})
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    status = {e["grandPrix"]: e["status"] for e in cal.apply_runtime_status(events, now)}
    assert status == {"Past Grand Prix": cal.STATUS_COMPLETED, "Future Grand Prix": cal.STATUS_SCHEDULED}


def test_runtime_status_never_resurrects_a_cancelled_event(curated_2026):
    events = cal.build_calendar(2026, live=_live_2026(), curated=curated_2026)
    out = cal.apply_runtime_status(events, datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert all(e["status"] == cal.STATUS_CANCELLED for e in out if e["round"] is None and e["kind"] == cal.KIND_RACE)


def test_sprint_weekends_are_flagged_from_the_sessions():
    live = _live(_meeting(1, 1, "Sprint Grand Prix", "2026-03-15", sprint=True), _meeting(2, 2, "Plain Grand Prix", "2026-03-22"))
    events = {e["grandPrix"]: e for e in cal.build_calendar(2026, live=live, overrides={})}
    assert events["Sprint Grand Prix"]["hasSprint"] is True
    assert events["Plain Grand Prix"]["hasSprint"] is False


# --------------------------------------------------------------------------- gp_id

@pytest.mark.parametrize("name,expected", [
    ("Australian Grand Prix", "AUS"), ("United States Grand Prix", "USA"), ("USA Grand Prix", "USA"),
    ("Las Vegas Grand Prix", "LVG"), ("Spanish Grand Prix", "ESP"), ("Miami Grand Prix", "MIA"),
])
def test_gp_id_codes_are_unchanged_for_stored_documents(name, expected):
    """Stored Mongo documents are keyed ``{year}_{code}``; the rule must not drift."""
    assert cal.country_code_from_event_name(name) == expected


# --------------------------------------------------------------------------- committed snapshots

SNAPSHOT_YEARS = range(2018, 2026)


@pytest.mark.parametrize("year", SNAPSHOT_YEARS)
def test_snapshot_rounds_are_contiguous_and_gp_ids_unique(year):
    events = cal.load_snapshot(year)
    assert events, f"missing calendars/{year}.json -- run src.workers.build_calendar_snapshots"
    races = [e for e in events if e["kind"] == cal.KIND_RACE and e["status"] != cal.STATUS_CANCELLED]
    assert sorted(e["round"] for e in races) == list(range(1, len(races) + 1))
    ids = [e["gp_id"] for e in races]
    assert len(ids) == len(set(ids)), f"{year}: gp_id collision"


def test_2023_emilia_romagna_is_listed_as_cancelled_and_monaco_is_round_6():
    events = cal.load_snapshot(2023)
    emilia = _by_name(events, "Emilia Romagna Grand Prix")[0]
    assert emilia["status"] == cal.STATUS_CANCELLED and emilia["round"] is None
    assert _by_name(events, "Monaco Grand Prix")[0]["round"] == 6   # livetiming's Number says 7


def test_2022_has_a_calendar_although_livetiming_serves_no_index():
    events = cal.load_snapshot(2022)
    races = [e for e in events if e["status"] != cal.STATUS_CANCELLED]
    assert len(races) == 22
    russia = _by_name(events, "Russian Grand Prix")[0]
    assert russia["status"] == cal.STATUS_CANCELLED


def test_2024_snapshot_is_complete_although_livetiming_starts_at_round_10():
    races = [e for e in cal.load_snapshot(2024) if e["kind"] == cal.KIND_RACE]
    assert len(races) == 24
    assert races[0]["grandPrix"] == "Bahrain Grand Prix" and races[0]["key"] is None
    assert [e["round"] for e in races if e["key"]][0] == 10


def test_2020_covid_calendar_lists_the_cancelled_races():
    events = cal.load_snapshot(2020)
    held = [e for e in events if e["status"] != cal.STATUS_CANCELLED]
    cancelled = {e["grandPrix"] for e in events if e["status"] == cal.STATUS_CANCELLED}
    assert len(held) == 17
    assert {"Australian Grand Prix", "Monaco Grand Prix", "Japanese Grand Prix"} <= cancelled
    assert {e["grandPrix"] for e in held} >= {"Sakhir Grand Prix", "Bahrain Grand Prix", "Eifel Grand Prix"}


def test_2018_snapshot_has_round_one_even_though_livetiming_starts_at_two():
    races = [e for e in cal.load_snapshot(2018) if e["kind"] == cal.KIND_RACE]
    assert races[0]["round"] == 1 and races[0]["grandPrix"] == "Australian Grand Prix"


def test_snapshot_round_trip_keeps_aware_datetimes(tmp_path, monkeypatch):
    monkeypatch.setattr(cal, "SNAPSHOT_DIR", str(tmp_path))
    events = cal.build_calendar(2026, live=_live(_meeting(1, 1, "Round Trip Grand Prix", "2026-03-08")), overrides={})
    cal.dump_snapshot(2026, events)
    loaded = cal.load_snapshot(2026)
    assert loaded[0]["sessions"][0]["startTime"].tzinfo is not None
    assert loaded[0]["grandPrix"] == "Round Trip Grand Prix"
