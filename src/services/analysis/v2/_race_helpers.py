"""
Derived race helpers built on top of :class:`SessionDataStore`.

These functions turn raw livetiming streams into the lap-indexed structures the
race plot features need: lap times, positions per lap, pit stops, and track
status periods. They take a store so a single set of parsed streams is reused
across every derivation (no repeated downloads).

Findings from real 2025 stream data that shape the parsing here:

* ``TimingData`` ``Lines.{num}`` deltas are *incremental*. ``LastLapTime.Value``
  and ``NumberOfLaps`` usually arrive together in the same entry, where
  ``NumberOfLaps`` is the lap just completed — so ``LastLapTime`` belongs to
  that lap number.
* ``LastLapTime`` also gets standalone ``{"OverallFastest": false}`` updates
  with no ``Value``; those must be ignored.
* Before the race starts there are spurious ``InPit``/``PitOut`` toggles on the
  grid (the lap counter has not begun). Real pit stops are signalled by
  ``NumberOfPitStops`` increments, which is what we key off.
* ``TrackStatus`` ``Status`` is a string code: ``1``=green ``2``=yellow
  ``4``=SC ``5``=red ``6``=VSC ``7``=VSC-ending. Timestamps are session-relative
  ``HH:MM:SS.mmm``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.services.analysis.v2._helpers import parse_f1_time

if TYPE_CHECKING:
    from src.services.analysis.v2.session_store import SessionDataStore

# TrackStatus numeric code -> canonical status label.
_TRACK_STATUS_CODES = {
    "1": "GREEN",
    "2": "YELLOW",
    "4": "SC",
    "5": "RED",
    "6": "VSC",
    "7": "GREEN",  # VSC ending -> racing resumes
}


def _via_store(store: Any, accessor: str, compute) -> Any:
    """Return a derived structure via the store's durably-cached accessor.

    Production always passes a :class:`SessionDataStore`, whose ``accessor``
    (e.g. ``lap_times``) serves the value from the in-process / MongoDB bundle.
    Any object that only exposes the raw stream methods still works by computing
    directly, keeping these helpers duck-typed on the stream accessors alone.
    """
    fn = getattr(store, accessor, None)
    if callable(fn):
        return fn()
    return compute(store)


def _iter_driver_lines(entries: List[Dict[str, Any]]):
    """Yield ``(timestamp_s, driver_num, line_dict)`` for every timing update."""
    for entry in entries:
        lines = entry.get("Lines")
        if not isinstance(lines, dict):
            continue
        ts = parse_f1_time(entry.get("_timestamp", entry.get("T", "")))
        for num, line in lines.items():
            if isinstance(line, dict):
                yield ts, str(num), line


def extract_lap_times(store: "SessionDataStore") -> Dict[str, List[Dict[str, Any]]]:
    """Per-driver completed-lap records (durably cached; see :meth:`SessionDataStore.lap_times`)."""
    return _via_store(store, "lap_times", _compute_lap_times)


def _compute_lap_times(store: "SessionDataStore") -> Dict[str, List[Dict[str, Any]]]:
    """Per-driver completed-lap records.

    Returns ``{car_number: [{lap, time_s, timestamp_s, pit_in, pit_out}, ...]}``.
    ``lap`` is the completed lap number (from ``NumberOfLaps``); ``time_s`` is
    the parsed ``LastLapTime``; ``timestamp_s`` is the session time the lap
    completed. ``pit_in`` marks laps where the driver entered the pits and
    ``pit_out`` marks out-laps (driver left the pits during that lap).
    """
    result: Dict[str, List[Dict[str, Any]]] = {}
    # Track per-driver in/out state so we can flag the lap it applies to.
    pending_in: Dict[str, bool] = {}
    pending_out: Dict[str, bool] = {}

    for ts, num, line in _iter_driver_lines(store.timing_data()):
        in_pit = line.get("InPit")
        pit_out = line.get("PitOut")
        if in_pit is True:
            pending_in[num] = True
        if pit_out is True:
            pending_out[num] = True

        number_of_laps = line.get("NumberOfLaps")
        last_lap = line.get("LastLapTime")
        last_val = last_lap.get("Value") if isinstance(last_lap, dict) else None

        if number_of_laps is None or not last_val:
            continue

        time_s = parse_f1_time(last_val)
        record = {
            "lap": int(number_of_laps),
            "time_s": time_s,
            "timestamp_s": ts,
            "pit_in": pending_in.pop(num, False),
            "pit_out": pending_out.pop(num, False),
        }
        result.setdefault(num, []).append(record)

    for num in result:
        result[num].sort(key=lambda r: r["lap"])
    return result


def extract_positions_by_lap(store: "SessionDataStore") -> Dict[str, List[Dict[str, Any]]]:
    """Per-driver position snapshots (durably cached; see :meth:`SessionDataStore.positions_by_lap`)."""
    return _via_store(store, "positions_by_lap", _compute_positions_by_lap)


def _compute_positions_by_lap(store: "SessionDataStore") -> Dict[str, List[Dict[str, Any]]]:
    """Per-driver position snapshots aligned to the driver's lap counter.

    Returns ``{car_number: [{lap, position}, ...]}``. ``Position`` updates arrive
    independently of the lap counter, so each position snapshot is tagged with
    the driver's most recent ``NumberOfLaps`` value. Consecutive duplicate
    positions on the same lap are collapsed.
    """
    result: Dict[str, List[Dict[str, Any]]] = {}
    current_lap: Dict[str, int] = {}

    for _ts, num, line in _iter_driver_lines(store.timing_data()):
        if "NumberOfLaps" in line and line["NumberOfLaps"] is not None:
            current_lap[num] = int(line["NumberOfLaps"])

        pos_raw = line.get("Position")
        if pos_raw in (None, ""):
            continue
        try:
            position = int(pos_raw)
        except (TypeError, ValueError):
            continue

        lap = current_lap.get(num, 0)
        records = result.setdefault(num, [])
        if records and records[-1]["lap"] == lap:
            records[-1]["position"] = position
        else:
            records.append({"lap": lap, "position": position})
    return result


def extract_pit_stops(store: "SessionDataStore") -> Dict[str, List[Dict[str, Any]]]:
    """Per-driver pit stops (durably cached; see :meth:`SessionDataStore.pit_stops`)."""
    return _via_store(store, "pit_stops", _compute_pit_stops)


def _compute_pit_stops(store: "SessionDataStore") -> Dict[str, List[Dict[str, Any]]]:
    """Per-driver pit stops keyed off ``NumberOfPitStops`` increments.

    Returns ``{car_number: [{lap, pit_lane_time_s, stop_n}, ...]}``. The pit-lane
    time is measured from the ``InPit:true`` timestamp to the following
    ``PitOut:true`` timestamp. Pre-race grid toggles are excluded because they
    carry no ``NumberOfPitStops`` increment.
    """
    result: Dict[str, List[Dict[str, Any]]] = {}
    current_lap: Dict[str, int] = {}
    stop_count: Dict[str, int] = {}
    in_ts: Dict[str, float] = {}
    pending: Dict[str, Dict[str, Any]] = {}

    for ts, num, line in _iter_driver_lines(store.timing_data()):
        if "NumberOfLaps" in line and line["NumberOfLaps"] is not None:
            current_lap[num] = int(line["NumberOfLaps"])

        if line.get("InPit") is True:
            in_ts[num] = ts

        nps = line.get("NumberOfPitStops")
        if nps is not None:
            try:
                nps_int = int(nps)
            except (TypeError, ValueError):
                nps_int = None
            if nps_int is not None and nps_int > stop_count.get(num, 0):
                stop_count[num] = nps_int
                pending[num] = {
                    "lap": current_lap.get(num, 0),
                    "pit_lane_time_s": None,
                    "stop_n": nps_int,
                    "_in_ts": in_ts.get(num),
                }
                result.setdefault(num, []).append(pending[num])

        if line.get("PitOut") is True and num in pending:
            rec = pending.pop(num)
            start = rec.pop("_in_ts", None)
            if start is not None:
                rec["pit_lane_time_s"] = round(ts - start, 3)

    # Strip any leftover private key from stops that never saw a PitOut.
    for stops in result.values():
        for rec in stops:
            rec.pop("_in_ts", None)
    return result


def _leader_lap_timestamps(store: SessionDataStore) -> List[float]:
    """Session-time at which each race lap was completed by the leader.

    Index ``i`` (0-based) is the completion time of lap ``i + 1``. Built from the
    earliest completion timestamp seen for each lap number across all drivers,
    which approximates the race leader's lap boundaries.
    """
    lap_times = extract_lap_times(store)
    earliest: Dict[int, float] = {}
    for records in lap_times.values():
        for rec in records:
            lap = rec["lap"]
            ts = rec["timestamp_s"]
            if lap not in earliest or ts < earliest[lap]:
                earliest[lap] = ts
    if not earliest:
        return []
    max_lap = max(earliest)
    return [earliest.get(lap, float("nan")) for lap in range(1, max_lap + 1)]


def _time_to_lap(time_s: float, leader_ts: List[float]) -> int:
    """Map a session time to the race lap in progress (1-based)."""
    lap = 1
    for idx, boundary in enumerate(leader_ts):
        if boundary != boundary:  # NaN guard
            continue
        if time_s >= boundary:
            lap = idx + 2  # completed lap idx+1 -> now on lap idx+2
        else:
            break
    return lap


def get_track_status_periods(store: "SessionDataStore") -> List[Dict[str, Any]]:
    """Contiguous track-status periods (durably cached; see :meth:`SessionDataStore.track_status_periods`)."""
    return _via_store(store, "track_status_periods", _compute_track_status_periods)


def _compute_track_status_periods(store: "SessionDataStore") -> List[Dict[str, Any]]:
    """Contiguous track-status periods mapped onto lap ranges.

    Returns ``[{status, start_lap, end_lap, start_time_s, end_time_s}, ...]``
    where ``status`` is one of ``GREEN/YELLOW/SC/VSC/RED``. Times come from the
    ``TrackStatus`` stream; laps are derived from the leader's lap boundaries.
    ``end_lap`` / ``end_time_s`` are ``None`` for a status still active when the
    session ends.
    """
    entries = store.track_status()
    leader_ts = _leader_lap_timestamps(store)

    raw: List[Dict[str, Any]] = []
    for entry in entries:
        code = str(entry.get("Status", "")).strip()
        status = _TRACK_STATUS_CODES.get(code)
        if status is None:
            continue
        ts = parse_f1_time(entry.get("_timestamp", entry.get("T", "")))
        # Collapse consecutive identical statuses.
        if raw and raw[-1]["status"] == status:
            continue
        raw.append({"status": status, "start_time_s": ts})

    periods: List[Dict[str, Any]] = []
    for i, item in enumerate(raw):
        end_time = raw[i + 1]["start_time_s"] if i + 1 < len(raw) else None
        start_lap = _time_to_lap(item["start_time_s"], leader_ts)
        end_lap = _time_to_lap(end_time, leader_ts) if end_time is not None else None
        periods.append({
            "status": item["status"],
            "start_lap": start_lap,
            "end_lap": end_lap,
            "start_time_s": item["start_time_s"],
            "end_time_s": end_time,
        })
    return periods


def get_clean_fastest_lap_window(store: "SessionDataStore", driver_num: str) -> Optional[Dict[str, float]]:
    """A driver's fastest *clean* lap window, or ``None`` if none qualifies.

    Unlike a naive scan for the minimum reported ``LastLapTime`` across a
    whole session (safe for practice/qualifying's handful of green-flag push
    laps, unsafe for a 50-70 lap race), this excludes pit in/out laps and
    laps that don't fall in a ``GREEN`` :func:`get_track_status_periods`
    window, so a red-flag-shortened lap, a VSC/SC in/out lap, or a
    pit-affected lap can't win the comparison and hand back a near-empty
    telemetry window.

    Returns ``{"StartTime", "EndTime", "LapTime"}`` (same shape as one row of
    ``get_fastest_lap_windows`` in ``_helpers.py``) for the fastest qualifying
    lap, or ``None`` when the driver has no clean lap (falls back to the
    caller's non-race-aware selection).
    """
    records = extract_lap_times(store).get(str(driver_num), [])
    if not records:
        return None

    periods = get_track_status_periods(store)
    green_ranges = [
        (p["start_lap"], p["end_lap"]) for p in periods if p["status"] == "GREEN"
    ]

    def _in_green(lap: int) -> bool:
        if not green_ranges:
            return True
        for start, end in green_ranges:
            if start is not None and lap < start:
                continue
            if end is not None and lap > end:
                continue
            return True
        return False

    candidates = [
        r for r in records
        if not r["pit_in"] and not r["pit_out"] and r["time_s"] > 0 and _in_green(r["lap"])
    ]
    if not candidates:
        return None

    best = min(candidates, key=lambda r: r["time_s"])
    return {
        "StartTime": best["timestamp_s"] - best["time_s"],
        "EndTime": best["timestamp_s"],
        "LapTime": best["time_s"],
    }


def cumtime_by_lap(lap_times: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[int, float]]:
    """Per-driver cumulative race time at the end of each completed lap.

    Shared promotion of the logic duplicated in ``pit_strategy._cumtime_by_lap``
    and ``race_gaps._cumulative_times``. Only laps with a positive parsed
    ``time_s`` contribute to the running sum.
    """
    result: Dict[str, Dict[int, float]] = {}
    for num, records in lap_times.items():
        cum = 0.0
        per_lap: Dict[int, float] = {}
        for rec in sorted(records, key=lambda r: r["lap"]):
            t = rec.get("time_s") or 0.0
            if t <= 0:
                continue
            cum += t
            per_lap[rec["lap"]] = cum
        result[num] = per_lap
    return result


# ----------------------------------------------------------------------
# Sector times (TimingData Lines.{num}.Sectors)
# ----------------------------------------------------------------------
def _normalize_sectors(sectors_raw: Any) -> Dict[str, Any]:
    """Normalize a ``Sectors`` payload to ``{"0": {...}, "1": {...}, "2": {...}}``.

    A full snapshot arrives as a 3-element list (index = sector number);
    incremental updates arrive as a dict keyed by sector index string.
    """
    if isinstance(sectors_raw, list):
        return {str(i): s for i, s in enumerate(sectors_raw) if isinstance(s, dict)}
    if isinstance(sectors_raw, dict):
        return {str(k): v for k, v in sectors_raw.items() if isinstance(v, dict)}
    return {}


def best_sectors_from_entries(entries: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Per-driver best sector times from already-fetched TimingData entries.

    Returns ``{car_num: {"s1": float, "s2": float, "s3": float}}`` keeping only
    the minimum valid time seen per sector per driver. Empty ``Value`` strings
    (mid-lap incremental updates with no time yet) are skipped. No lap-grouping
    is needed — this is a running per-driver minimum across the whole stream.
    """
    sector_key = {"0": "s1", "1": "s2", "2": "s3"}
    best: Dict[str, Dict[str, float]] = {}

    for entry in entries:
        lines = entry.get("Lines")
        if not isinstance(lines, dict):
            continue
        for num, line in lines.items():
            if not isinstance(line, dict):
                continue
            sectors_raw = line.get("Sectors")
            if sectors_raw is None:
                continue
            sectors = _normalize_sectors(sectors_raw)
            for idx, sector in sectors.items():
                key = sector_key.get(str(idx))
                if key is None:
                    continue
                value = sector.get("Value")
                if not value:
                    continue
                time_s = parse_f1_time(value)
                if time_s <= 0:
                    continue
                num_str = str(num)
                driver_best = best.setdefault(num_str, {})
                if key not in driver_best or time_s < driver_best[key]:
                    driver_best[key] = time_s

    return best


def extract_best_sectors(store: "SessionDataStore") -> Dict[str, Dict[str, float]]:
    """Per-driver best sectors (durably cached; see :meth:`SessionDataStore.best_sectors`)."""
    return _via_store(store, "best_sectors", _compute_best_sectors)


def _compute_best_sectors(store: "SessionDataStore") -> Dict[str, Dict[str, float]]:
    """Thin store wrapper around :func:`best_sectors_from_entries`."""
    return best_sectors_from_entries(store.timing_data())


# ----------------------------------------------------------------------
# Race control messages
# ----------------------------------------------------------------------
def _to_int(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _session_start_utc_timestamp(store: SessionDataStore) -> Optional[float]:
    """Session's ``StartDate``+``GmtOffset`` as a POSIX UTC timestamp, or ``None``.

    ``SessionInfo.json`` carries a *local* ``StartDate`` and a ``GmtOffset``
    (``"HH:MM:SS"``); subtracting the offset converts it to UTC so it can be
    compared with RaceControlMessages' absolute ``Utc`` timestamps.
    """
    try:
        info = store.session_info()
    except Exception:
        return None
    if not isinstance(info, dict):
        return None
    start_date = info.get("StartDate")
    if not start_date:
        return None
    try:
        from datetime import datetime
        local_dt = datetime.fromisoformat(str(start_date))
        offset_s = parse_f1_time(info.get("GmtOffset", "00:00:00"))
        return local_dt.timestamp() - offset_s
    except (ValueError, TypeError):
        return None


def extract_race_control_events(
    store: SessionDataStore, leader_ts: Optional[List[float]] = None
) -> List[Dict[str, Any]]:
    """Normalize ``store.race_control()`` into lap-indexed event records.

    Returns ``[{lap, category, message, flag, racing_number}, ...]`` sorted by
    lap (``None`` laps last). The message's own ``Lap`` field is used when
    present; otherwise the message's absolute ``Utc`` timestamp is anchored
    against the session start (``SessionInfo.json``) and mapped to a lap via
    ``leader_ts`` (as produced by :func:`_leader_lap_timestamps`). If neither
    is resolvable, ``lap`` is ``None``.
    """
    if leader_ts is None:
        leader_ts = _leader_lap_timestamps(store)

    session_start_utc = _session_start_utc_timestamp(store) if leader_ts else None

    events: List[Dict[str, Any]] = []
    for msg in store.race_control():
        if not isinstance(msg, dict):
            continue

        lap = _to_int(msg.get("Lap"))
        if lap is None and leader_ts and session_start_utc is not None:
            utc = msg.get("Utc")
            if utc:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(str(utc).replace("Z", "+00:00"))
                    rel_time_s = dt.timestamp() - session_start_utc
                    lap = _time_to_lap(rel_time_s, leader_ts)
                except (ValueError, TypeError):
                    lap = None

        events.append({
            "lap": lap,
            "category": msg.get("Category"),
            "message": msg.get("Message", ""),
            "flag": msg.get("Flag"),
            "racing_number": msg.get("RacingNumber"),
        })

    events.sort(key=lambda e: (e["lap"] is None, e["lap"] if e["lap"] is not None else 0))
    return events


__all__ = [
    "extract_lap_times",
    "extract_positions_by_lap",
    "extract_pit_stops",
    "get_track_status_periods",
    "get_clean_fastest_lap_window",
    "cumtime_by_lap",
    "best_sectors_from_entries",
    "extract_best_sectors",
    "extract_race_control_events",
]
