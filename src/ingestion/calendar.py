"""
Single source of truth for season calendars.

Three inputs describe a season and none of them is right on its own:

* **livetiming ``Index.json``** (2018+) -- authoritative identity (meeting ``Key``,
  session paths) but its ``Number`` is unreliable: pre-season testing is
  numbered 1, two meetings can share a number (2026 Miami and Monaco), cancelled
  rounds leave gaps (2023), the index can be a truncated window (2024 starts at
  round 10) or be blocked outright (2022 answers 403).
* **curated schedule** (``seasons.py``, 2025/2026) -- the only place future
  sessions exist, but numbered by the *original* planned calendar, so every
  cancelled race shifts everything after it.
* **Jolpica / Ergast schedule** -- the official championship round numbers (the
  same ones FastF1 uses), but no livetiming identity.

:func:`build_calendar` merges them into one list of event dicts. The rules that
matter:

* ``round`` is the *official* championship round: pre-season testing and
  cancelled events have none, and it equals FastF1's ``RoundNumber``. When a
  reference (Jolpica) schedule is supplied it decides; otherwise races are
  numbered by date.
* Events are joined to livetiming meetings by explicit name mapping, circuit and
  unique country -- never by position.
* Cancelled / replaced events (``calendar_overrides.json``) stay in the list with
  ``status="cancelled"`` and ``round=None`` so a request for one can be answered
  precisely instead of resolving to a neighbour.

Historical seasons are served from committed snapshots (``calendars/{year}.json``)
so resolving ``gp`` needs no network call and the seasons livetiming no longer
serves (2022) or truncates (2018, 2024) are still complete.
"""
from __future__ import annotations

import json
import os
import unicodedata
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Iterable, Optional

from src.core.logging import get_logger

logger = get_logger(__name__)

KIND_RACE = "race"
KIND_TESTING = "testing"

STATUS_SCHEDULED = "scheduled"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "domain", "data")
OVERRIDES_PATH = os.path.join(_DATA_DIR, "calendar_overrides.json")
SNAPSHOT_DIR = os.path.join(_DATA_DIR, "calendars")

# GP id code table (``{year}_{code}`` keys the Mongo documents). Moved here from
# ``repositories/plots.py`` so there is exactly one copy; the order matters (first
# substring hit wins) and existing stored documents depend on it.
COUNTRY_CODES = {
    'Australian': 'AUS',
    'Bahrain': 'BHR',
    'Chinese': 'CHN',
    'Japanese': 'JPN',
    'Saudi Arabian': 'SAU',
    'Miami': 'MIA',
    'Emilia Romagna': 'EMI',
    'Monaco': 'MON',
    'Spanish': 'ESP',
    'Canadian': 'CAN',
    'Austrian': 'AUT',
    'British': 'GBR',
    'Belgian': 'BEL',
    'Dutch': 'NED',
    'Italian': 'ITA',
    'Azerbaijan': 'AZE',
    'Singapore': 'SGP',
    'United States': 'USA',
    'Mexico': 'MEX',
    'Brazil': 'BRA',
    'Las Vegas': 'LVG',
    'Qatar': 'QAT',
    'Abu Dhabi': 'ABU',
}


def fold(value: Any) -> str:
    """Lowercase, strip diacritics, keep only letters and digits."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(ch for ch in text.lower() if ch.isalnum())


def country_code_from_event_name(event_name: str) -> str:
    """Legacy ``gp_id`` code for an event name (first matching table key, else 3 letters)."""
    for key, code in COUNTRY_CODES.items():
        if key.lower() in event_name.lower():
            return code
    return event_name.replace(' ', '').replace('Grand', '').replace('Prix', '')[:3].upper()


# --------------------------------------------------------------------------- overrides

@lru_cache(maxsize=1)
def load_overrides() -> dict:
    try:
        with open(OVERRIDES_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        logger.warning("Calendar overrides file missing: %s", OVERRIDES_PATH)
        return {}


def _year_overrides(overrides: dict, year: int) -> dict:
    return (overrides.get("years") or {}).get(str(year)) or {}


def canonical_name(name: str, overrides: dict) -> str:
    """Map a curated / alternate GP name to the name livetiming uses."""
    mapping = overrides.get("name_map") or {}
    folded = {fold(k): v for k, v in mapping.items()}
    return folded.get(fold(name), name)


# --------------------------------------------------------------------------- adapters

def _parse_offset(raw: Any) -> timedelta:
    if not raw or not isinstance(raw, str):
        return timedelta(0)
    sign = -1 if raw.startswith("-") else 1
    parts = raw.lstrip("+-").split(":")
    try:
        hours, minutes = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return timedelta(0)
    return sign * timedelta(hours=hours, minutes=minutes)


def _to_utc(local: Any, offset: Any) -> Optional[datetime]:
    """livetiming gives wall-clock ``StartDate`` plus ``GmtOffset``; return aware UTC."""
    if not local or not isinstance(local, str):
        return None
    try:
        naive = datetime.fromisoformat(local)
    except ValueError:
        return None
    if naive.tzinfo is not None:
        return naive.astimezone(timezone.utc)
    return (naive - _parse_offset(offset)).replace(tzinfo=timezone.utc)


def _aware(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _is_testing(name: str, official: str = "") -> bool:
    return "testing" in f"{name} {official}".lower()


def adapt_livetiming_index(index: dict) -> list[dict]:
    """livetiming ``Index.json`` -> event dicts (round/status are assigned later)."""
    events: list[dict] = []
    for meeting in index.get("Meetings", []):
        circuit = meeting.get("Circuit") if isinstance(meeting.get("Circuit"), dict) else {}
        country = meeting.get("Country") if isinstance(meeting.get("Country"), dict) else {}
        name = meeting.get("Name", "")
        official = meeting.get("OfficialName", "")
        sessions = []
        for s in meeting.get("Sessions", []):
            start = _to_utc(s.get("StartDate"), s.get("GmtOffset"))
            end = _to_utc(s.get("EndDate"), s.get("GmtOffset"))
            sessions.append({
                "name": s.get("Name", ""),
                "type": s.get("Type", ""),
                "startTime": start,
                "endTime": end,
                "startDate": s.get("StartDate", ""),
                "endDate": s.get("EndDate", ""),
                "path": s.get("Path"),
            })
        events.append({
            "grandPrix": name,
            "officialName": official,
            "circuit": circuit.get("ShortName", ""),
            # Livetiming's Circuit.Key doubles as the stored circuit-layout id.
            "circuitKey": circuit.get("Key"),
            "location": meeting.get("Location", ""),
            "country": country.get("Name", ""),
            "code": meeting.get("Code", ""),
            "key": meeting.get("Key"),
            "livetimingNumber": meeting.get("Number"),
            "kind": KIND_TESTING if _is_testing(name, official) else KIND_RACE,
            "sessions": sessions,
            "source": "livetiming",
        })
    return events


def adapt_curated(curated: list[dict], overrides: dict) -> list[dict]:
    """Curated schedule -> event dicts. ``originalRound`` is the planned slot."""
    out: list[dict] = []
    for idx, race in enumerate(curated, start=1):
        name = race.get("grandPrix", "")
        canon = canonical_name(name, overrides)
        sessions = [
            {
                "name": s.get("name", ""),
                "type": "",
                "startTime": _aware(s.get("startTime")),
                "endTime": _aware(s.get("endTime")),
                "startDate": "",
                "endDate": "",
                "path": None,
            }
            for s in race.get("sessions", [])
        ]
        out.append({
            "grandPrix": canon,
            "curatedName": name,
            "officialName": race.get("officialName", ""),
            "circuit": race.get("circuit", ""),
            "circuitKey": race.get("circuitKey"),
            "location": race.get("location", ""),
            "country": race.get("country", ""),
            "code": race.get("code", ""),
            "key": race.get("key"),
            "kind": KIND_RACE,
            "hasSprint": race.get("hasSprint"),
            "originalRound": idx,
            "sessions": sessions,
            "source": "curated",
        })
    return out


def adapt_reference_schedule(races: Iterable[dict]) -> list[dict]:
    """Jolpica/Ergast ``RaceTable.Races`` -> reference rows (round, name, date, ...)."""
    rows: list[dict] = []
    for r in races:
        loc = (r.get("Circuit") or {}).get("Location") or {}
        try:
            rnd = int(r.get("round"))
        except (TypeError, ValueError):
            continue
        race_date = None
        if r.get("date"):
            try:
                race_date = date.fromisoformat(r["date"])
            except ValueError:
                race_date = None
        rows.append({
            "round": rnd,
            "grandPrix": r.get("raceName", ""),
            "circuit": (r.get("Circuit") or {}).get("circuitName", ""),
            "location": loc.get("locality", ""),
            "country": loc.get("country", ""),
            "date": race_date,
        })
    return rows


# --------------------------------------------------------------------------- merge

def _match_curated_to_live(curated: list[dict], live: list[dict]) -> list[Optional[dict]]:
    """Pair each curated race with at most one livetiming meeting (claiming each once).

    Pass 1 exact (canonical) name, pass 2 unique circuit containment, pass 3 unique
    country. Cancelled races are removed from ``curated`` *before* this runs --
    otherwise the cancelled 2026 Bahrain GP claims the Bahrain-in-Malaysia meeting
    by name.
    """
    matches: list[Optional[dict]] = [None] * len(curated)
    claimed: set[int] = set()

    def claim(i: int, m: dict) -> None:
        matches[i] = m
        claimed.add(id(m))

    def free() -> list[dict]:
        return [m for m in live if id(m) not in claimed and m.get("kind") == KIND_RACE]

    by_name: dict[str, list[dict]] = {}
    for m in live:
        by_name.setdefault(fold(m.get("grandPrix")), []).append(m)
    for i, race in enumerate(curated):
        for m in by_name.get(fold(race.get("grandPrix")), []):
            if id(m) not in claimed:
                claim(i, m)
                break

    for i, race in enumerate(curated):
        if matches[i] is not None:
            continue
        wanted = fold(race.get("circuit"))
        if not wanted:
            continue
        cands = [m for m in free() if fold(m.get("circuit")) and
                 (fold(m.get("circuit")) in wanted or wanted in fold(m.get("circuit")))]
        if len(cands) == 1:
            claim(i, cands[0])

    for i, race in enumerate(curated):
        if matches[i] is not None:
            continue
        country = fold(race.get("country"))
        if not country:
            continue
        cands = [m for m in free() if fold(m.get("country")) == country]
        if len(cands) == 1:
            claim(i, cands[0])
    return matches


def _event_day(event: dict) -> Optional[date]:
    """The race day (Race session, else latest session) in UTC."""
    sessions = event.get("sessions") or []
    race = [s for s in sessions if fold(s.get("name")) == "race" and s.get("startTime")]
    pool = race or [s for s in sessions if s.get("startTime")]
    if not pool:
        return None
    return max(s["startTime"] for s in pool).date()


def _has_sprint(event: dict) -> bool:
    return any("sprint" in (s.get("name") or "").lower() for s in event.get("sessions") or [])


def _assign_rounds_by_date(races: list[dict]) -> None:
    ordered = sorted(
        races,
        key=lambda e: (_event_day(e) or date.max, e.get("originalRound") or 0, e.get("key") or 0),
    )
    for n, event in enumerate(ordered, start=1):
        event["round"] = n


def _assign_rounds_from_reference(races: list[dict], reference: list[dict]) -> list[dict]:
    """Take official rounds from the reference schedule; return reference rows with no event."""
    unmatched_events = list(races)
    leftover_rows: list[dict] = []
    for row in sorted(reference, key=lambda r: r["round"]):
        best, best_score = None, None
        for event in unmatched_events:
            day = _event_day(event)
            if day is None or row["date"] is None:
                continue
            delta = abs((day - row["date"]).days)
            if delta > 1:
                continue
            name_bonus = 0 if fold(event.get("grandPrix")) == fold(row["grandPrix"]) else 1
            score = (delta, name_bonus)
            if best_score is None or score < best_score:
                best, best_score = event, score
        if best is None:
            leftover_rows.append(row)
        else:
            best["round"] = row["round"]
            unmatched_events.remove(best)
    for event in unmatched_events:
        logger.warning("Calendar: %s (%s) has no reference round", event.get("grandPrix"), event.get("key"))
        event["round"] = None
    return leftover_rows


def _event_from_reference(row: dict) -> dict:
    return {
        "grandPrix": row["grandPrix"],
        "officialName": "",
        "circuit": row["circuit"],
        "circuitKey": None,
        "location": row["location"],
        "country": row["country"],
        "code": "",
        "key": None,
        "kind": KIND_RACE,
        "round": row["round"],
        "sessions": [],
        "source": "reference",
        "referenceDate": row["date"],
    }


def _gp_id(event: dict, year: int, overrides: dict) -> str:
    codes = {fold(k): v for k, v in (overrides.get("gp_codes") or {}).items()}
    code = codes.get(fold(event.get("grandPrix")))
    return f"{year}_{code or country_code_from_event_name(event.get('grandPrix', ''))}"


def build_calendar(
    year: int,
    *,
    live: Optional[list[dict]] = None,
    curated: Optional[list[dict]] = None,
    reference: Optional[list[dict]] = None,
    overrides: Optional[dict] = None,
) -> list[dict]:
    """Merge livetiming / curated / reference inputs into the season's event list.

    Output order: testing (by date), races by round, then cancelled events by their
    original slot. ``live`` is :func:`adapt_livetiming_index` output, ``curated`` is
    :func:`adapt_curated` output, ``reference`` is :func:`adapt_reference_schedule`.
    """
    overrides = load_overrides() if overrides is None else overrides
    yo = _year_overrides(overrides, year)
    live = [dict(e) for e in (live or [])]
    curated = [dict(e) for e in (curated or [])]
    cancelled_specs = list(yo.get("cancelled") or [])

    # 1. Drop curated entries that were cancelled so they cannot claim a meeting.
    def _is_cancelled(race: dict) -> bool:
        for spec in cancelled_specs:
            if fold(spec.get("grandPrix")) != fold(race.get("grandPrix")):
                continue
            wanted = spec.get("original_round")
            if wanted is None or wanted == race.get("originalRound"):
                return True
        return False

    curated = [c for c in curated if not _is_cancelled(c)]

    # 2. Join curated <-> livetiming.
    matches = _match_curated_to_live(curated, live)
    events: list[dict] = []
    used_live: set[int] = set()
    for race, meeting in zip(curated, matches):
        if meeting is None:
            events.append(race)
            continue
        used_live.add(id(meeting))
        merged = dict(meeting)
        for k in ("hasSprint", "originalRound", "curatedName"):
            if race.get(k) is not None:
                merged[k] = race[k]
        if not merged.get("circuit"):
            merged["circuit"] = race.get("circuit", "")
        if not merged.get("sessions"):
            merged["sessions"] = race.get("sessions", [])
        events.append(merged)
    for meeting in live:
        if id(meeting) not in used_live:
            events.append(meeting)

    # 3. Rounds.
    races = [e for e in events if e.get("kind") == KIND_RACE]
    testing = [e for e in events if e.get("kind") == KIND_TESTING]
    if reference:
        for row in _assign_rounds_from_reference(races, reference):
            extra = _event_from_reference(row)
            races.append(extra)
            events.append(extra)
    else:
        _assign_rounds_by_date(races)
    for t in testing:
        t["round"] = None

    for e in events:
        livetiming_number = e.get("livetimingNumber")
        if e.get("round") and livetiming_number and e["round"] != livetiming_number and e.get("kind") == KIND_RACE:
            logger.debug("Calendar %s %s: official round %s, livetiming Number %s",
                         year, e.get("grandPrix"), e["round"], livetiming_number)

    # 4. Overrides: per-event aliases / replacements, then cancelled events.
    by_key = {str(e.get("key")): e for e in events if e.get("key") is not None}
    for key, spec in (yo.get("events") or {}).items():
        target = by_key.get(str(key))
        if target is None:
            continue
        if spec.get("aliases"):
            target.setdefault("aliases", [])
            target["aliases"] = list(dict.fromkeys(list(target["aliases"]) + spec["aliases"]))
        for field in ("location", "circuit", "country"):
            if spec.get(field):
                target[field] = spec[field]
        if spec.get("replaces"):
            target["replaces"] = spec["replaces"]
            if spec.get("note"):
                target["note"] = spec["note"]

    for spec in cancelled_specs:
        ev = {
            "grandPrix": spec.get("grandPrix", ""),
            "officialName": spec.get("officialName", ""),
            "circuit": spec.get("circuit", ""),
            "circuitKey": None,
            "location": spec.get("location", ""),
            "country": spec.get("country", ""),
            "code": "",
            "key": None,
            "kind": KIND_RACE,
            "round": None,
            "originalRound": spec.get("original_round"),
            "sessions": [],
            "source": "overrides",
            "status": STATUS_CANCELLED,
            "reason": spec.get("reason", ""),
            "aliases": list(spec.get("aliases") or []),
        }
        replaced_by = by_key.get(str(spec.get("replaced_by_key")))
        if replaced_by is not None:
            ev["replacedBy"] = replaced_by.get("grandPrix")
            ev["replacedByKey"] = replaced_by.get("key")
        events.append(ev)

    # 5. Aliases, sprint flag, gp_id.
    global_aliases = {fold(k): v for k, v in (overrides.get("aliases") or {}).items()}
    for e in events:
        aliases = list(e.get("aliases") or [])
        aliases += global_aliases.get(fold(e.get("grandPrix")), [])
        if e.get("curatedName") and fold(e["curatedName"]) != fold(e.get("grandPrix")):
            aliases.append(e["curatedName"])
        e["aliases"] = list(dict.fromkeys(a for a in aliases if a))
        e.setdefault("status", STATUS_SCHEDULED)
        if e.get("hasSprint") is None:
            e["hasSprint"] = _has_sprint(e)
        e["gp_id"] = _gp_id(e, year, overrides) if e["kind"] == KIND_RACE else None

    def _order(e: dict) -> tuple:
        if e["status"] == STATUS_CANCELLED:
            return (2, e.get("originalRound") or 99, 0)
        if e["kind"] == KIND_TESTING:
            return (0, 0, (_event_day(e) or date.max).toordinal())
        return (1, e.get("round") if e.get("round") is not None else 999, 0)

    events.sort(key=_order)
    _check_gp_ids(events, year)
    return events


def _check_gp_ids(events: list[dict], year: int) -> None:
    seen: dict[str, str] = {}
    for e in events:
        gid = e.get("gp_id")
        if not gid or e["status"] == STATUS_CANCELLED:
            continue
        if gid in seen:
            logger.error("Calendar %s: gp_id %s used by both %r and %r -- add a gp_codes override",
                         year, gid, seen[gid], e.get("grandPrix"))
        seen[gid] = e.get("grandPrix", "")


def apply_runtime_status(events: list[dict], now: Optional[datetime] = None) -> list[dict]:
    """Mark scheduled events whose last session has ended as completed (returns new dicts)."""
    now = now or datetime.now(timezone.utc)
    out = []
    for e in events:
        e = dict(e)
        if e["status"] == STATUS_SCHEDULED:
            ends = [
                s.get("endTime") or s.get("startTime")
                for s in e.get("sessions") or []
                if s.get("endTime") or s.get("startTime")
            ]
            ref_day = e.get("referenceDate")
            if ends and max(ends) < now:
                e["status"] = STATUS_COMPLETED
            elif not ends and ref_day and ref_day < now.date():
                e["status"] = STATUS_COMPLETED
        out.append(e)
    return out


# --------------------------------------------------------------------------- snapshots

def snapshot_path(year: int) -> str:
    return os.path.join(SNAPSHOT_DIR, f"{int(year)}.json")


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"not serializable: {type(value)}")


def dump_snapshot(year: int, events: list[dict]) -> str:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = snapshot_path(year)
    # Runtime status (completed / scheduled) is computed on load; only "cancelled" is a stored fact.
    stored = [
        {k: v for k, v in e.items() if k != "status" or e["status"] == STATUS_CANCELLED}
        for e in events
    ]
    payload = {"year": int(year), "events": stored}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, default=_encode, ensure_ascii=False, indent=1)
        fh.write("\n")
    return path


def load_snapshot(year: int) -> Optional[list[dict]]:
    try:
        with open(snapshot_path(year), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return None
    events = payload.get("events") or []
    for e in events:
        e.setdefault("status", STATUS_SCHEDULED)
        for s in e.get("sessions") or []:
            s["startTime"] = _aware(s.get("startTime"))
            s["endTime"] = _aware(s.get("endTime"))
        if e.get("referenceDate"):
            try:
                e["referenceDate"] = date.fromisoformat(e["referenceDate"])
            except ValueError:
                e["referenceDate"] = None
    return events


# --------------------------------------------------------------------------- public shape

def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value or None


def public_event(event: dict) -> dict:
    """The calendar event as the API serves it.

    Keeps the keys ``/seasons/{year}/events`` has always returned (``name``,
    ``official_name``, ``location``, ``country``, ``key``, ``code``) and adds the
    calendar facts: official ``round`` (``None`` for testing and cancelled events),
    ``kind``, ``status`` and, for cancelled / replacement events, what replaced what.
    """
    out = {
        "name": event.get("grandPrix"),
        "official_name": event.get("officialName") or None,
        "location": event.get("location") or None,
        "country": event.get("country") or None,
        "key": event.get("key"),
        "code": event.get("code") or None,
        "round": event.get("round"),
        "kind": event.get("kind"),
        "status": event.get("status"),
        "circuit": event.get("circuit") or None,
        "circuit_key": event.get("circuitKey"),
        "has_sprint": bool(event.get("hasSprint")),
        "original_round": event.get("originalRound"),
        "aliases": list(event.get("aliases") or []),
        "sessions": [
            {"name": s.get("name"), "start": _iso(s.get("startTime")), "end": _iso(s.get("endTime"))}
            for s in event.get("sessions") or []
        ],
    }
    if event.get("replaces"):
        out["replaces"] = event["replaces"]
    if event.get("replacedBy"):
        out["replaced_by"] = event["replacedBy"]
        out["replaced_by_key"] = event.get("replacedByKey")
    if event.get("reason"):
        out["reason"] = event["reason"]
    if event.get("note"):
        out["note"] = event["note"]
    return out
