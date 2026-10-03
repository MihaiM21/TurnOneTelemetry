"""Per-lap records with sectors, speeds, gaps and context, from TimingData.

``_race_helpers._compute_lap_times`` already derives *completed* laps (lap
number, time, pit flags) from the ``NumberOfLaps`` + ``LastLapTime`` pair.
The dataset needs everything else TimingData says about a lap — sector times,
speed traps, position, gap and interval, fastest flags — which are delivered
**incrementally** while the lap is in progress and then reset once the car
crosses the line. This module closes laps on the same ``NumberOfLaps`` signal
and accumulates the in-progress fields alongside it, but is more tolerant than
the API helper: it keeps untimed laps and pairs a ``LastLapTime`` that the feed
delivered a few seconds apart from its lap count.

Ordering quirks the single pass has to survive:

* ``NumberOfLaps`` and ``LastLapTime`` are usually one entry but can be split
  in either order (observed on real 2024 practice data).

* Sector 3 and the finish-line speed trap (``Speeds.FL``) usually arrive in the
  same entry as ``LastLapTime`` but occasionally in the *next* one. A value for
  an already-closed lap is therefore accepted as long as the new lap has not
  yet reported its own sector 1.
* The feed clears ``Sectors[i].Value`` to ``""`` at the start of a new lap.
  Empty values are ignored, never written.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from src.services.analysis.v2._helpers import parse_f1_time
from src.services.analysis.v2._race_helpers import _iter_driver_lines, _normalize_sectors

SECTOR_FIELDS = {"0": "sector1_s", "1": "sector2_s", "2": "sector3_s"}
SPEED_FIELDS = {"I1": "speed_i1", "I2": "speed_i2", "FL": "speed_fl", "ST": "speed_st"}

# "CAR 44 (HAM) TIME 1:23.456 DELETED - TRACK LIMITS AT TURN 4 LAP 12 ..."
_DELETED_RE = re.compile(r"CAR\s+(\d+)\s+\(\w+\)\s+TIME\s+[\d:.]+\s+DELETED.*?\bLAP\s+(\d+)", re.IGNORECASE)

_STATUS_CODE = {"GREEN": 1, "YELLOW": 2, "SC": 4, "RED": 5, "VSC": 6}

# ``NumberOfLaps`` and ``LastLapTime`` normally share one entry but can be split
# across two a few seconds apart, in either order. A time arriving this soon
# after a count-only close belongs to that lap.
_LATE_TIME_WINDOW_S = 5.0


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _gap_seconds(value: Any) -> Optional[float]:
    """``"+1.234"`` -> 1.234; ``"LAP 1"``, ``"1L"``, ``""`` -> None (leader / lapped)."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.startswith("+"):
        return None
    return _to_float(text[1:])


def _time_seconds(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        seconds = parse_f1_time(str(value))
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _new_pending() -> Dict[str, Any]:
    return {"sectors": {}, "speeds": {}}


def extract_lap_records(timing_entries: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Per-driver completed laps with every per-lap TimingData field attached.

    Returns ``{car_number: [record, ...]}`` sorted by lap, each record carrying:
    ``lap, lap_time_s, sector1_s..sector3_s, speed_i1/i2/fl/st, position,
    gap_to_leader_s, interval_s, is_pit_in, is_pit_out, timestamp_start_s,
    timestamp_end_s, is_personal_best, is_overall_fastest``.

    A lap closes when ``NumberOfLaps`` advances. Its time is the ``LastLapTime``
    from the same entry, or one that arrived alone just before/after it (the
    feed splits the two across entries a few seconds apart, in either order).
    Untimed laps (out-laps, the first lap of a practice run) are kept with a
    null ``lap_time_s`` so lap counts and stint lengths stay consistent.
    """
    result: Dict[str, List[Dict[str, Any]]] = {}
    pending: Dict[str, Dict[str, Any]] = {}
    pending_in: Dict[str, bool] = {}
    pending_out: Dict[str, bool] = {}
    # Running state (not reset per lap): last position / gap / interval seen.
    position: Dict[str, Optional[int]] = {}
    gap: Dict[str, Optional[float]] = {}
    interval: Dict[str, Optional[float]] = {}
    last_close_ts: Dict[str, float] = {}
    # LastLapTime that arrived before its NumberOfLaps: (seconds, flags dict).
    pending_time: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    for ts, num, line in _iter_driver_lines(timing_entries):
        cur = pending.setdefault(num, _new_pending())
        closed = result.get(num)
        last = closed[-1] if closed else None
        # A late sector-3 / FL for the previous lap is only plausible while the
        # new lap has not started reporting its own sector 1.
        late_ok = last is not None and "0" not in cur["sectors"]

        if line.get("InPit") is True:
            pending_in[num] = True
        if line.get("PitOut") is True:
            pending_out[num] = True

        pos = _to_int(line.get("Position"))
        if pos is not None:
            position[num] = pos
        if "GapToLeader" in line:
            gap[num] = _gap_seconds(line.get("GapToLeader"))
        itv = line.get("IntervalToPositionAhead")
        if isinstance(itv, dict) and "Value" in itv:
            interval[num] = _gap_seconds(itv.get("Value"))

        for idx, sector in _normalize_sectors(line.get("Sectors")).items():
            if idx not in SECTOR_FIELDS:
                continue
            seconds = _time_seconds(sector.get("Value"))
            if seconds is None:
                continue
            if idx == "2" and late_ok and last.get("sector3_s") is None:
                last["sector3_s"] = seconds
            else:
                cur["sectors"][idx] = seconds

        speeds = line.get("Speeds")
        if isinstance(speeds, dict):
            for trap, field in SPEED_FIELDS.items():
                trap_data = speeds.get(trap)
                if not isinstance(trap_data, dict):
                    continue
                speed = _to_float(trap_data.get("Value"))
                if speed is None:
                    continue
                if trap == "FL" and late_ok and last.get("speed_fl") is None:
                    last["speed_fl"] = speed
                else:
                    cur["speeds"][field] = speed

        number_of_laps = line.get("NumberOfLaps")
        last_lap = line.get("LastLapTime")
        last_val = last_lap.get("Value") if isinstance(last_lap, dict) else None
        lap_time = _time_seconds(last_val)
        flags = last_lap if isinstance(last_lap, dict) else {}

        if number_of_laps is None:
            if lap_time is None:
                continue
            # Time arrived without the lap count. Either the count follows in a
            # later entry (hold the time for it) or it already arrived alone a
            # moment ago and closed an untimed record (backfill that record).
            if (last is not None and last["lap_time_s"] is None
                    and ts - last["timestamp_end_s"] <= _LATE_TIME_WINDOW_S):
                last["lap_time_s"] = lap_time
                last["is_personal_best"] = bool(flags.get("PersonalFastest", False))
                last["is_overall_fastest"] = bool(flags.get("OverallFastest", False))
            else:
                pending_time[num] = (lap_time, flags)
            continue

        lap_no = _to_int(number_of_laps)
        if lap_no is None:
            continue
        if lap_time is None and num in pending_time:
            lap_time, flags = pending_time.pop(num)
        pending_time.pop(num, None)
        record: Dict[str, Any] = {
            "lap": lap_no,
            "lap_time_s": lap_time,
            "sector1_s": cur["sectors"].get("0"),
            "sector2_s": cur["sectors"].get("1"),
            "sector3_s": cur["sectors"].get("2"),
            "speed_i1": cur["speeds"].get("speed_i1"),
            "speed_i2": cur["speeds"].get("speed_i2"),
            "speed_fl": cur["speeds"].get("speed_fl"),
            "speed_st": cur["speeds"].get("speed_st"),
            "position": position.get(num),
            "gap_to_leader_s": gap.get(num),
            "interval_s": interval.get(num),
            "is_pit_in": pending_in.pop(num, False),
            "is_pit_out": pending_out.pop(num, False),
            "timestamp_start_s": last_close_ts.get(num),
            "timestamp_end_s": ts,
            "is_personal_best": bool(flags.get("PersonalFastest", False)),
            "is_overall_fastest": bool(flags.get("OverallFastest", False)),
        }
        result.setdefault(num, []).append(record)
        last_close_ts[num] = ts
        pending[num] = _new_pending()

    for num in result:
        result[num].sort(key=lambda r: r["lap"])
    return result


def deleted_laps_from_rcm(events: Iterable[Dict[str, Any]]) -> Set[Tuple[str, int]]:
    """``{(car_number, lap)}`` for every "TIME ... DELETED" race-control message.

    Older seasons frequently omit the lap number from the message; those
    deletions cannot be attributed and are skipped.
    """
    deleted: Set[Tuple[str, int]] = set()
    for event in events:
        message = event.get("message") if isinstance(event, dict) else None
        if not message:
            continue
        match = _DELETED_RE.search(str(message))
        if match:
            deleted.add((match.group(1), int(match.group(2))))
    return deleted


def _status_at(time_s: Optional[float], periods: List[Dict[str, Any]]) -> Optional[str]:
    if time_s is None:
        return None
    for period in periods:
        start = period.get("start_time_s")
        end = period.get("end_time_s")
        if start is not None and time_s >= start and (end is None or time_s < end):
            return period.get("status")
    return None


def join_lap_context(
    records: Dict[str, List[Dict[str, Any]]],
    stints: Dict[str, List[Dict[str, Any]]],
    track_status_periods: List[Dict[str, Any]],
    deleted: Optional[Set[Tuple[str, int]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Attach compound / tyre life / stint number, track status and deletion flags.

    Mutates and returns ``records``. Track status is sampled at the lap's
    *start* (the state the driver set off under); a lap with no start
    timestamp (lap 1) uses its end timestamp instead.
    """
    deleted = deleted or set()
    for num, laps in records.items():
        driver_stints = stints.get(num, []) if stints else []
        for rec in laps:
            lap = rec["lap"]
            stint = next(
                (s for s in driver_stints if s.get("start_lap") is not None
                 and s["start_lap"] <= lap <= (s.get("end_lap") or lap)),
                None,
            )
            if stint is not None:
                rec["compound"] = stint.get("compound")
                rec["stint_number"] = stint.get("stint_number")
                life_end = stint.get("tyre_life_end")
                end_lap = stint.get("end_lap")
                if life_end is not None and end_lap is not None:
                    rec["tyre_life"] = life_end - (end_lap - lap)
                else:
                    rec["tyre_life"] = None
            else:
                rec["compound"] = None
                rec["stint_number"] = None
                rec["tyre_life"] = None

            probe = rec.get("timestamp_start_s")
            if probe is None:
                probe = rec.get("timestamp_end_s")
            label = _status_at(probe, track_status_periods or [])
            rec["track_status_label"] = label
            rec["track_status_code"] = _STATUS_CODE.get(label) if label else None
            rec["is_deleted"] = (num, lap) in deleted
            rec["is_pit_lap"] = bool(rec.get("is_pit_in") or rec.get("is_pit_out"))
    return records
