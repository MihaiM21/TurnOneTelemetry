"""Rebuild livetiming streams from OpenF1 rows, in the shapes the parsers return.

``load(url)`` takes the livetiming URL a feature would have fetched
(``.../{session path}/TimingData.jsonStream``) and returns what
``F1StaticClient.parse_jsonstream_simple`` / ``parse_compressed_stream`` (or a
``.json`` decode) would have produced, so callers cannot tell the difference.

One clock for every stream
--------------------------
livetiming stamps every stream line with ``_timestamp`` -- seconds since one
session origin -- and features join streams on it: a lap window comes from the
``_timestamp`` of the ``LastLapTime`` update, CarData samples are placed on the
same axis via ``Utc - (first Utc - first _timestamp)``. Every builder here uses
the same origin, :data:`ORIGIN_BEFORE_START` before the session's scheduled start,
so those joins line up exactly. Rows before the origin are dropped (telemetry) or
clamped to 0 (timing state such as the starting order).

Fidelity notes (vs. the real stream)
------------------------------------
* TimingData carries the fields the features read: ``NumberOfLaps``,
  ``LastLapTime`` (+ ``PersonalFastest``/``OverallFastest``), ``Sectors``,
  ``Speeds`` (I1/I2/ST -- OpenF1 has no FL speed), ``Position``, ``GapToLeader``,
  ``IntervalToPositionAhead``, ``InPit``/``PitOut``/``NumberOfPitStops``,
  ``Retired``. Sector/speed updates are emitted at their real crossing times.
* TrackStatus is *derived* from race-control messages (SC/VSC/red/yellow); a
  safety car ends at the first lap completion after "SAFETY CAR IN THIS LAP".
* SessionData only carries ``QualifyingPart`` (from race control's
  ``qualifying_phase``) and ``SessionStatus``.
* Streams OpenF1 cannot rebuild (TimingStats, TeamRadio, ...) raise
  :class:`UnsupportedStream`; callers then re-raise the original livetiming error.
"""
from __future__ import annotations

import time
from collections import OrderedDict, defaultdict
from datetime import timedelta
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.ingestion.openf1_client import (
    OpenF1Client,
    livetiming_session_name,
    local_naive,
    parse_utc,
    season_catalog,
    session_key_for_path,
    session_path_from_url,
)

logger = get_logger(__name__)

#: Session-time origin: this long before the scheduled start.
ORIGIN_BEFORE_START = timedelta(hours=1)

_CTX_CACHE_MAX = 6
_CTX_TTL_S = 600

_RACE_TYPES = {"Race"}


class UnsupportedStream(Exception):
    """OpenF1 has nothing to rebuild this livetiming file from."""


def fmt_session_time(seconds: float) -> str:
    """Seconds -> livetiming ``HH:MM:SS.mmm`` (clamped at 0)."""
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def fmt_lap_time(seconds: float) -> str:
    """``83.456`` -> ``"1:23.456"`` (livetiming ``LastLapTime`` spelling)."""
    m, s = divmod(round(seconds, 3), 60)
    return f"{int(m)}:{s:06.3f}" if m else f"{s:.3f}"


def _fmt_gap(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"+{value:.3f}"
    return str(value)


def _num(value: Any) -> str:
    return "" if value is None else str(value)


# ---------------------------------------------------------------------------
# Per-session context: the session row, the clock and a small row cache
# ---------------------------------------------------------------------------
class _SessionCtx:
    def __init__(self, session: Dict[str, Any], meeting: Dict[str, Any], client: OpenF1Client):
        self.session = session
        self.meeting = meeting
        self.client = client
        self.key = session["session_key"]
        self.year = int(session.get("year") or parse_utc(session["date_start"]).year)
        self.start = parse_utc(session["date_start"])
        self.origin = self.start - ORIGIN_BEFORE_START
        self.origin_ts = self.origin.timestamp()
        self.is_race = session.get("session_type") in _RACE_TYPES
        self._rows: Dict[Tuple[str, Tuple], List[Dict[str, Any]]] = {}
        self._lock = Lock()

    def rows(self, endpoint: str, **params: Any) -> List[Dict[str, Any]]:
        cache_key = (endpoint, tuple(sorted(params.items())))
        with self._lock:
            hit = self._rows.get(cache_key)
        if hit is not None:
            return hit
        rows = self.client.get(endpoint, session_key=self.key, **params)
        with self._lock:
            self._rows[cache_key] = rows
        return rows

    def t(self, utc_iso: str) -> float:
        """Session time (s) of an OpenF1 timestamp."""
        return parse_utc(utc_iso).timestamp() - self.origin_ts

    def ts(self, utc_iso: str) -> str:
        return fmt_session_time(self.t(utc_iso))

    def driver_numbers(self) -> List[str]:
        return sorted({str(d["driver_number"]) for d in self.rows("drivers")}, key=int)


_CTX: "OrderedDict[int, Tuple[float, _SessionCtx]]" = OrderedDict()
_CTX_LOCK = Lock()


def clear_cache() -> None:
    with _CTX_LOCK:
        _CTX.clear()


def _context(session: Dict[str, Any], client: Optional[OpenF1Client]) -> _SessionCtx:
    key = session["session_key"]
    with _CTX_LOCK:
        hit = _CTX.get(key)
        if hit is not None and hit[0] > time.time():
            _CTX.move_to_end(key)
            return hit[1]
    year = int(session.get("year") or parse_utc(session["date_start"]).year)
    meeting = season_catalog(year, client)["meetings"].get(session["meeting_key"], {})
    ctx = _SessionCtx(session, meeting, client or OpenF1Client())
    with _CTX_LOCK:
        _CTX[key] = (time.time() + _CTX_TTL_S, ctx)
        while len(_CTX) > _CTX_CACHE_MAX:
            _CTX.popitem(last=False)
    return ctx


def _no_data(ctx: _SessionCtx, what: str) -> DataNotAvailableError:
    return DataNotAvailableError(
        year=ctx.year, gp=ctx.meeting.get("meeting_name"), session=ctx.session.get("session_name"),
        source="openf1", reason=f"OpenF1 has no {what} for session {ctx.key}",
    )


# ---------------------------------------------------------------------------
# Small JSON files
# ---------------------------------------------------------------------------
def _position_int(value: Any) -> Optional[int]:
    """OpenF1 ``position`` is an int, or a status string such as ``"RT"`` (retired)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _final_positions(ctx: _SessionCtx) -> Dict[str, int]:
    out = {}
    for r in ctx.rows("session_result"):
        pos = _position_int(r.get("position"))
        if pos:
            out[str(r["driver_number"])] = pos
    return out


def _driver_list(ctx: _SessionCtx) -> Dict[str, Any]:
    drivers = ctx.rows("drivers")
    if not drivers:
        raise _no_data(ctx, "driver list")
    final = _final_positions(ctx)
    ordered = sorted(drivers, key=lambda d: (final.get(str(d["driver_number"]), 99), d["driver_number"]))
    out: Dict[str, Any] = {}
    for line, d in enumerate(ordered, start=1):
        num = str(d["driver_number"])
        out[num] = {
            "RacingNumber": num,
            "BroadcastName": d.get("broadcast_name", ""),
            "FullName": d.get("full_name", ""),
            "Tla": d.get("name_acronym") or num,
            "Line": line,
            "TeamName": d.get("team_name") or "Unknown",
            "TeamColour": (d.get("team_colour") or "").lstrip("#"),
            "FirstName": d.get("first_name", ""),
            "LastName": d.get("last_name", ""),
            "HeadshotUrl": d.get("headshot_url") or "",
            "CountryCode": d.get("country_code") or "",
        }
    return out


def _session_info(ctx: _SessionCtx) -> Dict[str, Any]:
    s, m = ctx.session, ctx.meeting
    offset = s.get("gmt_offset") or "00:00:00"
    return {
        "Meeting": {
            "Key": s["meeting_key"],
            "Name": m.get("meeting_name", ""),
            "OfficialName": m.get("meeting_official_name", ""),
            "Location": m.get("location") or s.get("location", ""),
            "Country": {"Key": m.get("country_key"), "Code": m.get("country_code", ""),
                        "Name": m.get("country_name", "")},
            "Circuit": {"Key": s.get("circuit_key"), "ShortName": s.get("circuit_short_name", "")},
        },
        "ArchiveStatus": {"Status": "Complete"},
        "Key": ctx.key,
        "Type": s.get("session_type", ""),
        "Name": livetiming_session_name(s.get("session_name", ""), ctx.year),
        "StartDate": local_naive(s["date_start"], offset).isoformat(),
        "EndDate": local_naive(s["date_end"], offset).isoformat() if s.get("date_end") else "",
        "GmtOffset": offset.lstrip("+"),
        "_source": "openf1",
    }


def _race_control(ctx: _SessionCtx) -> Dict[str, Any]:
    messages = []
    for r in sorted(ctx.rows("race_control"), key=lambda r: r["date"]):
        msg = {
            "Utc": r["date"],
            "Lap": r.get("lap_number"),
            "Category": r.get("category"),
            "Message": r.get("message", ""),
            "Flag": r.get("flag"),
            "Scope": r.get("scope"),
            "Sector": r.get("sector"),
            "RacingNumber": None if r.get("driver_number") is None else str(r["driver_number"]),
            "QualifyingPhase": r.get("qualifying_phase"),
        }
        messages.append({k: v for k, v in msg.items() if v is not None})
    return {"Messages": messages}


# ---------------------------------------------------------------------------
# Small streams
# ---------------------------------------------------------------------------
def _weather(ctx: _SessionCtx) -> List[Dict[str, Any]]:
    rows = sorted(ctx.rows("weather"), key=lambda r: r["date"])
    if not rows:
        raise _no_data(ctx, "weather")
    return [{
        "AirTemp": _num(r.get("air_temperature")),
        "Humidity": _num(r.get("humidity")),
        "Pressure": _num(r.get("pressure")),
        "Rainfall": _num(int(r["rainfall"]) if r.get("rainfall") is not None else None),
        "TrackTemp": _num(r.get("track_temperature")),
        "WindDirection": _num(r.get("wind_direction")),
        "WindSpeed": _num(r.get("wind_speed")),
        "_timestamp": ctx.ts(r["date"]),
    } for r in rows]


def _lap_rows(ctx: _SessionCtx) -> Dict[str, List[Dict[str, Any]]]:
    by_driver: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in ctx.rows("laps"):
        if r.get("lap_number") is not None:
            by_driver[str(r["driver_number"])].append(r)
    for rows in by_driver.values():
        rows.sort(key=lambda r: r["lap_number"])
    return by_driver


def _lap_end_times(ctx: _SessionCtx) -> List[float]:
    """Sorted session times of every lap completion (any driver)."""
    ends = []
    for rows in _lap_rows(ctx).values():
        for i, lap in enumerate(rows):
            end = _lap_end(ctx, rows, i)
            if end is not None:
                ends.append(end)
    return sorted(ends)


def _lap_end(ctx: _SessionCtx, rows: List[Dict[str, Any]], i: int) -> Optional[float]:
    """Session time a lap crossed the line.

    An out-lap's ``date_start`` is the pit *exit* while its ``lap_duration`` runs
    from the previous line crossing (garage time included: 475 s at 2026 Singapore
    Q), so ``start + duration`` lands minutes late; the next lap's start is the
    crossing. Every other lap is ``start + duration``.
    """
    lap = rows[i]
    next_start = rows[i + 1].get("date_start") if i + 1 < len(rows) else None
    if lap.get("is_pit_out_lap") and next_start:
        return ctx.t(next_start)
    if lap.get("date_start") and lap.get("lap_duration"):
        return ctx.t(lap["date_start"]) + float(lap["lap_duration"])
    if next_start:
        return ctx.t(next_start)
    return None


def _is_in_lap(rows: List[Dict[str, Any]], i: int) -> bool:
    """The lap ends in the pit lane: the driver's next lap is an out-lap."""
    return i + 1 < len(rows) and bool(rows[i + 1].get("is_pit_out_lap"))


_SC_CODES = {"4", "6"}


def _track_status(ctx: _SessionCtx) -> List[Dict[str, Any]]:
    """TrackStatus codes derived from race control (1 green, 2 yellow, 4 SC, 5 red, 6 VSC, 7 VSC ending)."""
    messages = sorted(ctx.rows("race_control"), key=lambda r: r["date"])
    lap_ends = _lap_end_times(ctx)
    out: List[Dict[str, Any]] = []
    state = {"red": False, "sc": None, "yellow": set(), "track_yellow": False}

    def code() -> str:
        if state["red"]:
            return "5"
        if state["sc"] in _SC_CODES:
            return state["sc"]
        return "2" if (state["yellow"] or state["track_yellow"]) else "1"

    names = {"1": "AllClear", "2": "Yellow", "4": "SCDeployed", "5": "Red", "6": "VSCDeployed", "7": "VSCEnding"}

    def emit(t: float, status: str) -> None:
        if out and out[-1]["Status"] == status:
            return
        out.append({"Status": status, "Message": names[status], "_timestamp": fmt_session_time(t)})

    pending_sc_end: Optional[float] = None
    for r in messages:
        t = ctx.t(r["date"])
        if pending_sc_end is not None:
            end = next((e for e in lap_ends if e > pending_sc_end), None)
            if end is not None and end <= t:
                state["sc"] = None
                emit(end, code())
                pending_sc_end = None
        msg = (r.get("message") or "").upper()
        category, flag, scope = r.get("category"), (r.get("flag") or "").upper(), r.get("scope")

        if category == "SessionStatus" and ("STARTED" in msg or "RESUMED" in msg):
            state["red"] = False
        elif category == "SafetyCar":
            # 2026 race control writes "VSC DEPLOYED" / "VSC ENDING"; older seasons spell it out.
            if "VIRTUAL SAFETY CAR DEPLOYED" in msg or "VSC DEPLOYED" in msg:
                state["sc"] = "6"
            elif "VIRTUAL SAFETY CAR ENDING" in msg or "VSC ENDING" in msg:
                state["sc"] = None
                emit(t, "7")
                continue
            elif "SAFETY CAR DEPLOYED" in msg:
                state["sc"] = "4"
            elif "SAFETY CAR IN THIS LAP" in msg:
                pending_sc_end = t
        elif category == "Flag":
            if flag == "RED":
                state["red"] = True
            elif flag in ("YELLOW", "DOUBLE YELLOW"):
                if scope == "Sector" and r.get("sector") is not None:
                    state["yellow"].add(r["sector"])
                else:
                    state["track_yellow"] = True
            elif flag == "CLEAR":
                if scope == "Sector" and r.get("sector") is not None:
                    state["yellow"].discard(r["sector"])
                else:
                    state["yellow"].clear()
                    state["track_yellow"] = False
            elif flag == "GREEN":
                state["red"] = False
                if "TRACK CLEAR" in msg:
                    state["yellow"].clear()
                    state["track_yellow"] = False
        emit(t, code())

    if pending_sc_end is not None:
        end = next((e for e in lap_ends if e > pending_sc_end), None)
        if end is not None:
            state["sc"] = None
            emit(end, code())
    if not out:
        start = fmt_session_time(ctx.t(ctx.session["date_start"]))
        out.append({"Status": "1", "Message": "AllClear", "_timestamp": start})
    return out


def _session_data(ctx: _SessionCtx) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen_parts = set()
    for r in sorted(ctx.rows("race_control"), key=lambda r: r["date"]):
        if r.get("category") != "SessionStatus":
            continue
        msg = (r.get("message") or "").upper()
        status = "Started" if "STARTED" in msg else "Finished" if "FINISHED" in msg else (
            "Aborted" if "SUSPENDED" in msg else "Resumed" if "RESUMED" in msg else None)
        ts = ctx.ts(r["date"])
        part = r.get("qualifying_phase")
        if part and status == "Started" and part not in seen_parts:
            seen_parts.add(part)
            out.append({"Series": [{"Utc": r["date"], "QualifyingPart": int(part)}], "_timestamp": ts})
        if status:
            out.append({"StatusSeries": [{"Utc": r["date"], "SessionStatus": status}], "_timestamp": ts})
    if not out:
        raise _no_data(ctx, "session status")
    return out


def _repaired_stints(ctx: _SessionCtx, stints: List[Dict[str, Any]],
                     laps: Dict[str, List[Dict[str, Any]]]) -> List[Tuple[Dict[str, Any], int, int]]:
    """``(stint row, first lap, last lap)`` per stint, boundaries repaired from pit stops.

    OpenF1's stint boundaries are occasionally wrong (2026 Singapore R: car 14
    "SOFT 1-1, MEDIUM 2-61" after stopping on lap 35). In a race, when a driver has
    exactly one more stint than pit stops, the stops decide the boundaries (a new
    stint starts the lap after the in-lap) and OpenF1 keeps only the compounds.
    A missing ``lap_end`` runs to the next stint, or to the driver's last lap.
    """
    pit_laps: Dict[str, List[int]] = defaultdict(list)
    if ctx.is_race:
        for p in ctx.rows("pit"):
            if p.get("lap_number"):
                pit_laps[str(p["driver_number"])].append(int(p["lap_number"]))

    by_driver: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for s in stints:
        by_driver[str(s["driver_number"])].append(s)

    out: List[Tuple[Dict[str, Any], int, int]] = []
    for num, rows in sorted(by_driver.items(), key=lambda kv: int(kv[0])):
        rows.sort(key=lambda r: r.get("stint_number") or 0)
        last_lap = max((r["lap_number"] for r in laps.get(num, [])), default=None)
        stops = sorted(pit_laps.get(num, []))
        if stops and len(rows) == len(stops) + 1:
            starts = [1] + [lap + 1 for lap in stops]
        else:
            starts = [r.get("lap_start") or 1 for r in rows]
        for i, row in enumerate(rows):
            first = starts[i]
            if i + 1 < len(rows):
                last = starts[i + 1] - 1
            else:
                last = row.get("lap_end") or last_lap or first
                # Outside races livetiming does not count the untimed lap back to the
                # pits after the flag (2026 Singapore Q: every final stint one lap long).
                final = laps.get(num, [])[-1:] or [{}]
                if not ctx.is_race and last == last_lap and not final[0].get("lap_duration"):
                    last -= 1
            out.append((row, first, max(first, last)))
    return out


def _timing_app_data(ctx: _SessionCtx) -> List[Dict[str, Any]]:
    stints = ctx.rows("stints")
    if not stints:
        raise _no_data(ctx, "stints")
    laps = _lap_rows(ctx)
    lap_start = {(num, r["lap_number"]): r.get("date_start") for num, rows in laps.items() for r in rows}
    entries = []
    for s, first, last in _repaired_stints(ctx, stints, laps):
        num = str(s["driver_number"])
        age = int(s.get("tyre_age_at_start") or 0)
        started = lap_start.get((num, first))
        t = ctx.t(started) if started else 0.0
        entries.append((t, {
            "Lines": {num: {"Stints": {str((s.get("stint_number") or 1) - 1): {
                "Compound": (s.get("compound") or "UNKNOWN").upper(),
                "New": "true" if age == 0 else "false",
                "TyresNotChanged": "0",
                "StartLaps": age,
                "TotalLaps": age + (last - first + 1),
            }}}},
            "_timestamp": fmt_session_time(t),
        }))
    entries.sort(key=lambda e: e[0])
    return [e for _, e in entries]


# ---------------------------------------------------------------------------
# TimingData
# ---------------------------------------------------------------------------
def _timing_events(ctx: _SessionCtx) -> List[Tuple[float, str, Dict[str, Any]]]:
    events: List[Tuple[float, str, Dict[str, Any]]] = []
    laps = _lap_rows(ctx)
    if not laps:
        raise _no_data(ctx, "laps")

    pit_stays: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    stops: Dict[str, int] = defaultdict(int)
    # OpenF1's pit ``date`` is the pit-lane *exit*: entry = date - lane_duration.
    # Checked against livetiming (2026 Singapore R, car 63): entry 6.0 s before the
    # in-lap ends, vs 5.9 s predicted -- read as the entry, every stop landed a lap late.
    for p in sorted(ctx.rows("pit"), key=lambda r: r["date"]):
        num = str(p["driver_number"])
        t_out = ctx.t(p["date"])
        lane = p.get("lane_duration") or p.get("pit_duration")
        t_in = t_out - float(lane) if lane else t_out
        stops[num] += 1
        events.append((t_in, num, {"InPit": True, "NumberOfPitStops": stops[num]}))
        pit_stays[num].append((t_in, t_out))
        if lane:
            events.append((t_out, num, {"InPit": False, "PitOut": True}))

    # livetiming numbers laps differently outside races: in practice, Q and SQ its
    # NumberOfLaps is OpenF1's lap_number + 1, and an in-lap gets no time. Measured
    # on 2026 Zandvoort FP1/SQ/Sprint, Hungaroring FP2, Singapore Q (offset 0 in races).
    lap_offset = 0 if ctx.is_race else 1
    for num, rows in laps.items():
        pit_windows = pit_stays.get(num, [])
        for i, lap in enumerate(rows):
            start = ctx.t(lap["date_start"]) if lap.get("date_start") else None
            if start is not None and lap.get("is_pit_out_lap") and not any(
                    t_in - 5 <= start <= t_out + 5 for t_in, t_out in pit_windows):
                # A pit visit OpenF1's pit table does not list (practice/quali, or the
                # garage before the first run): in at the previous lap's end, out now.
                prev_end = _lap_end(ctx, rows, i - 1) if i > 0 else None
                events.append((prev_end if prev_end is not None else start - 1.0, num, {"InPit": True}))
                events.append((start, num, {"InPit": False, "PitOut": True}))

            s1, s2, s3 = (lap.get(f"duration_sector_{k}") for k in (1, 2, 3))
            if start is not None and s1:
                upd: Dict[str, Any] = {"Sectors": {"0": {"Value": f"{float(s1):.3f}"}}}
                if lap.get("i1_speed"):
                    upd["Speeds"] = {"I1": {"Value": str(lap["i1_speed"])}}
                events.append((start + float(s1), num, upd))
                if s2:
                    upd = {"Sectors": {"1": {"Value": f"{float(s2):.3f}"}}}
                    if lap.get("i2_speed"):
                        upd["Speeds"] = {"I2": {"Value": str(lap["i2_speed"])}}
                    events.append((start + float(s1) + float(s2), num, upd))

            end = _lap_end(ctx, rows, i)
            if end is None:
                continue
            final: Dict[str, Any] = {"NumberOfLaps": int(lap["lap_number"]) + lap_offset}
            if s3:
                final["Sectors"] = {"2": {"Value": f"{float(s3):.3f}"}}
            if lap.get("st_speed"):
                final["Speeds"] = {"ST": {"Value": str(lap["st_speed"])}}
            # livetiming publishes no LastLapTime for a race's opening lap (standing
            # start), nor for an in-lap outside races.
            first_race_lap = ctx.is_race and int(lap["lap_number"]) == 1
            untimed_in_lap = not ctx.is_race and _is_in_lap(rows, i)
            if lap.get("lap_duration") and not first_race_lap and not untimed_in_lap:
                # "_s" is read (and removed) by _timing_data to flag personal/overall bests.
                final["LastLapTime"] = {"Value": fmt_lap_time(float(lap["lap_duration"])),
                                        "_s": float(lap["lap_duration"])}
            events.append((end, num, final))

    for p in ctx.rows("position"):
        events.append((ctx.t(p["date"]), str(p["driver_number"]), {"Position": str(p["position"])}))

    if ctx.is_race:
        for r in ctx.rows("intervals"):
            events.append((ctx.t(r["date"]), str(r["driver_number"]), {
                "GapToLeader": _fmt_gap(r.get("gap_to_leader")),
                "IntervalToPositionAhead": {"Value": _fmt_gap(r.get("interval"))},
            }))

    if not events:
        raise _no_data(ctx, "timing")
    t_end = max(t for t, _, _ in events) + 1.0
    for r in ctx.rows("session_result"):
        upd = {}
        if _position_int(r.get("position")):
            upd["Position"] = str(r["position"])
        if r.get("dnf") or r.get("dns") or r.get("dsq"):
            upd["Retired"] = True
        if upd:
            events.append((t_end, str(r["driver_number"]), upd))

    events.sort(key=lambda e: e[0])
    return events


def _timing_data(ctx: _SessionCtx) -> List[Dict[str, Any]]:
    events = _timing_events(ctx)
    personal: Dict[str, float] = {}
    overall = float("inf")
    entries: List[Dict[str, Any]] = []
    for t, num, upd in events:
        last = upd.get("LastLapTime")
        if last:
            lap_s = last.pop("_s")
            if lap_s < personal.get(num, float("inf")):
                personal[num] = lap_s
                last["PersonalFastest"] = True
            if lap_s < overall:
                overall = lap_s
                last["OverallFastest"] = True
        ts = fmt_session_time(t)
        if entries and entries[-1]["_timestamp"] == ts and num not in entries[-1]["Lines"]:
            entries[-1]["Lines"][num] = upd
        else:
            entries.append({"Lines": {num: upd}, "_timestamp": ts})
    return entries


# ---------------------------------------------------------------------------
# Telemetry (fetched per driver: OpenF1 refuses a whole-session query)
# ---------------------------------------------------------------------------
_PACKET_SAMPLES = 8


def _frames(ctx: _SessionCtx, endpoint: str, convert: Callable[[Dict[str, Any]], Dict[str, Any]]
            ) -> List[Tuple[float, str, Dict[str, Any]]]:
    """Per-timestamp frames ``(epoch, utc_iso, {car: payload})`` merged across drivers.

    OpenF1 splits one livetiming frame into a row per car with the frame's exact
    timestamp, so grouping by that timestamp rebuilds the original frames.
    """
    # One request per driver against a ~30/min budget: refuse a session that has
    # not run (or has no laps yet) before spending ~22 requests to find out.
    if ctx.start.timestamp() > time.time() or not ctx.rows("laps"):
        raise _no_data(ctx, endpoint.replace("_", " "))
    frames: Dict[float, Tuple[str, Dict[str, Any]]] = {}
    for num in ctx.driver_numbers():
        for r in ctx.client.get(endpoint, session_key=ctx.key, driver_number=int(num)):
            epoch = parse_utc(r["date"]).timestamp()
            if epoch < ctx.origin_ts:
                continue
            frames.setdefault(epoch, (r["date"], {}))[1][num] = convert(r)
    return [(e, iso, cars) for e, (iso, cars) in sorted(frames.items())]


def _car_data(ctx: _SessionCtx) -> List[Dict[str, Any]]:
    def convert(r: Dict[str, Any]) -> Dict[str, Any]:
        return {"Channels": {"0": r.get("rpm") or 0, "2": r.get("speed") or 0, "3": r.get("n_gear") or 0,
                             "4": r.get("throttle") or 0, "5": r.get("brake") or 0, "45": r.get("drs") or 0}}

    frames = _frames(ctx, "car_data", convert)
    if not frames:
        raise _no_data(ctx, "car data")
    out = []
    for i in range(0, len(frames), _PACKET_SAMPLES):
        chunk = frames[i:i + _PACKET_SAMPLES]
        out.append({
            "Entries": [{"Utc": iso, "Cars": cars} for _, iso, cars in chunk],
            "_timestamp": fmt_session_time(chunk[0][0] - ctx.origin_ts),
        })
    return out


def _position(ctx: _SessionCtx) -> List[Dict[str, Any]]:
    def convert(r: Dict[str, Any]) -> Dict[str, Any]:
        return {"Status": "OnTrack", "X": r.get("x", 0), "Y": r.get("y", 0), "Z": r.get("z", 0)}

    frames = _frames(ctx, "location", convert)
    if not frames:
        raise _no_data(ctx, "position data")
    out = []
    for i in range(0, len(frames), _PACKET_SAMPLES):
        chunk = frames[i:i + _PACKET_SAMPLES]
        out.append({
            "Position": [{"Timestamp": iso, "Entries": cars} for _, iso, cars in chunk],
            "_timestamp": fmt_session_time(chunk[0][0] - ctx.origin_ts),
        })
    return out


BUILDERS: Dict[str, Callable[[_SessionCtx], Any]] = {
    "DriverList.json": _driver_list,
    "SessionInfo.json": _session_info,
    "RaceControlMessages.json": _race_control,
    "WeatherData.jsonStream": _weather,
    "TrackStatus.jsonStream": _track_status,
    "SessionData.jsonStream": _session_data,
    "TimingAppData.jsonStream": _timing_app_data,
    "TimingData.jsonStream": _timing_data,
    "CarData.z.jsonStream": _car_data,
    "Position.z.jsonStream": _position,
}


def supports(filename: str) -> bool:
    return filename in BUILDERS


def load(url: str, client: Optional[OpenF1Client] = None) -> Any:
    """The livetiming file at ``url``, rebuilt from OpenF1.

    Raises :class:`UnsupportedStream` (no OpenF1 equivalent / pre-2023 / not a
    session URL), ``DataNotAvailableError`` (OpenF1 has the session but not this
    data) or ``UpstreamUnavailableError`` (OpenF1 down).
    """
    filename = url.rstrip("/").rsplit("/", 1)[-1]
    builder = BUILDERS.get(filename)
    path = session_path_from_url(url)
    if builder is None or path is None:
        raise UnsupportedStream(filename)
    try:
        year = int(path.split("/", 1)[0])
    except ValueError as exc:
        raise UnsupportedStream(filename) from exc
    from src.ingestion.openf1_client import MIN_YEAR

    if year < MIN_YEAR:
        raise UnsupportedStream(f"{filename} ({year} predates OpenF1)")
    session = session_key_for_path(path, client)
    ctx = _context(session, client)
    started = time.monotonic()
    data = builder(ctx)
    logger.info("Rebuilt %s for %s from OpenF1 in %.1fs", filename, path, time.monotonic() - started)
    return data


__all__ = ["BUILDERS", "ORIGIN_BEFORE_START", "UnsupportedStream", "clear_cache", "fmt_lap_time",
           "fmt_session_time", "load", "supports"]
