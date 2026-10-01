"""Builders for the dataset export ``tables`` tier.

One ``build_<table>`` function per table declared in :data:`schema.TABLES`, each
returning a :class:`pandas.DataFrame` already passed through :func:`schema.coerce`
and carrying the four key columns (:func:`schema.key_values`) on every row.
:func:`build_tables` runs every builder for one session and never aborts: a
builder that raises is logged and replaced with an empty, well-formed frame plus
a warning string, so one missing stream (e.g. no weather data for an old
session) never blocks the rest of the export.
"""

from typing import Any, Dict, List, Tuple

import pandas as pd

from src.core.exceptions import DataNotAvailableError, UpstreamUnavailableError
from src.core.logging import get_logger
from src.services.analysis.v2._helpers import get_finishing_order, get_qualifying_classification
from src.services.analysis.v2._race_helpers import (
    _TRACK_STATUS_CODES,
    _session_start_utc_timestamp,
    extract_race_control_events,
)
from src.services.analysis.v2._helpers import parse_f1_time
from src.services.dataset_export.laps import deleted_laps_from_rcm, extract_lap_records, join_lap_context
from src.services.dataset_export.schema import TABLES, Target, coerce, empty_frame, key_values, tables_for_tier

logger = get_logger(__name__)

_RACE_ABBREVS = ("R", "S")
_QUALI_ABBREVS = ("Q", "SQ")

# Reverse of _race_helpers._TRACK_STATUS_CODES: label -> numeric code. Several
# numeric codes map to the same label (7 -> GREEN alongside 1), so this keeps
# the first (canonical) code seen per label.
_STATUS_LABEL_TO_CODE: Dict[str, int] = {}
for _code, _label in _TRACK_STATUS_CODES.items():
    _STATUS_LABEL_TO_CODE.setdefault(_label, int(_code))


def _with_keys(rows: List[Dict[str, Any]], target: Target) -> pd.DataFrame:
    keys = key_values(target)
    for row in rows:
        row.update(keys)
    if not rows:
        return pd.DataFrame(columns=list(keys.keys()))
    return pd.DataFrame(rows)


def build_sessions(store: Any, target: Target) -> pd.DataFrame:
    """One-row session metadata frame."""
    spec = TABLES["sessions"]
    year, round_nr, gp_name, session_abbrev = target
    row: Dict[str, Any] = {}
    try:
        info = store.session_info()
    except (DataNotAvailableError, UpstreamUnavailableError):
        info = None

    if isinstance(info, dict):
        meeting = info.get("Meeting", {}) if isinstance(info.get("Meeting"), dict) else {}
        circuit = meeting.get("Circuit", {}) if isinstance(meeting.get("Circuit"), dict) else {}
        country = meeting.get("Country", {}) if isinstance(meeting.get("Country"), dict) else {}
        row.update({
            "gp_name": meeting.get("Name"),
            "official_name": meeting.get("OfficialName"),
            "circuit_key": circuit.get("Key"),
            "circuit_short_name": circuit.get("ShortName"),
            "country": country.get("Name"),
            "location": meeting.get("Location"),
            "session_type": info.get("Name"),
            "start_utc": info.get("StartDate"),
            "end_utc": info.get("EndDate"),
            "gmt_offset": info.get("GmtOffset"),
            "livetiming_path": info.get("Path"),
        })
    else:
        row.update({
            "gp_name": getattr(store, "event_name", gp_name),
            "official_name": getattr(store, "official_name", None),
        })

    total_laps = None
    if session_abbrev in _RACE_ABBREVS:
        try:
            lap_times = store.lap_times()
        except (DataNotAvailableError, UpstreamUnavailableError):
            lap_times = {}
        laps_seen = [rec.get("lap") for laps in lap_times.values() for rec in laps if rec.get("lap") is not None]
        total_laps = max(laps_seen) if laps_seen else None
    row["total_laps"] = total_laps

    is_complete = None
    is_complete_fn = getattr(store, "is_session_complete", None)
    if callable(is_complete_fn):
        try:
            is_complete = is_complete_fn()
        except Exception:  # noqa: BLE001 - best-effort metadata field
            is_complete = None
    row["is_complete"] = is_complete

    return coerce(_with_keys([row], target), spec)


def build_drivers(store: Any, target: Target) -> pd.DataFrame:
    """One row per driver from ``driver_list()``."""
    spec = TABLES["drivers"]
    driver_list = store.driver_list()
    rows = []
    for num, info in driver_list.items():
        rows.append({
            "driver_number": str(num),
            "driver_code": info.get("tla"),
            "full_name": info.get("name"),
            "team_name": info.get("team"),
            "team_color": info.get("color"),
            "line": info.get("line"),
            "reference_number": info.get("racing_number"),
        })
    return coerce(_with_keys(rows, target), spec)


def _safe_driver_list(store: Any) -> Dict[str, Dict[str, Any]]:
    try:
        return store.driver_list()
    except (DataNotAvailableError, UpstreamUnavailableError):
        return {}


def build_laps(store: Any, target: Target) -> pd.DataFrame:
    """Per-lap records joined with stints, track status and RCM deletions."""
    spec = TABLES["laps"]
    records = extract_lap_records(store.timing_data())

    try:
        stints = store.stints()
    except Exception:  # noqa: BLE001 - stints are a best-effort join, never fatal to laps
        logger.debug("Dataset export: stints unavailable for laps join", exc_info=True)
        stints = {}

    try:
        track_status_periods = store.track_status_periods()
    except Exception:  # noqa: BLE001 - track status is a best-effort join, never fatal to laps
        logger.debug("Dataset export: track status unavailable for laps join", exc_info=True)
        track_status_periods = []

    try:
        rcm_events = extract_race_control_events(store)
        deleted = deleted_laps_from_rcm(rcm_events)
    except Exception:  # noqa: BLE001 - race control is a best-effort join, never fatal to laps
        logger.debug("Dataset export: race control unavailable for laps join", exc_info=True)
        deleted = set()

    records = join_lap_context(records, stints, track_status_periods, deleted)
    driver_list = _safe_driver_list(store)

    rows: List[Dict[str, Any]] = []
    for num, laps in records.items():
        driver_code = driver_list.get(str(num), {}).get("tla")
        for rec in laps:
            row = dict(rec)
            row["driver_number"] = str(num)
            row["driver_code"] = driver_code
            rows.append(row)

    rows.sort(key=lambda r: (r["driver_number"], r.get("lap") if r.get("lap") is not None else -1))
    return coerce(_with_keys(rows, target), spec)


def build_stints(store: Any, target: Target) -> pd.DataFrame:
    """Per-driver stints, with tyre life at stint start derived from stint end."""
    spec = TABLES["stints"]
    stints = store.stints()
    rows: List[Dict[str, Any]] = []
    for num, driver_stints in stints.items():
        for stint in driver_stints:
            lap_count = stint.get("lap_count")
            tyre_life_end = stint.get("tyre_life_end")
            tyre_life_start = None
            is_new = None
            if lap_count is not None and tyre_life_end is not None:
                tyre_life_start = tyre_life_end - lap_count
                is_new = tyre_life_start == 0
            rows.append({
                "driver_number": str(num),
                "stint_number": stint.get("stint_number"),
                "compound": stint.get("compound"),
                "is_new": is_new,
                "start_lap": stint.get("start_lap"),
                "end_lap": stint.get("end_lap"),
                "lap_count": lap_count,
                "tyre_life_start": tyre_life_start,
                "tyre_life_end": tyre_life_end,
            })
    return coerce(_with_keys(rows, target), spec)


def build_pit_stops(store: Any, target: Target) -> pd.DataFrame:
    """Per-driver pit stops, with a timestamp taken from the matching lap record.

    ``store.lap_times()`` is the derived ``{lap, time_s, timestamp_s, pit_in,
    pit_out}`` accessor (``_race_helpers._compute_lap_times``) -- distinct from
    this module's own richer ``extract_lap_records`` shape used by
    :func:`build_laps`.
    """
    spec = TABLES["pit_stops"]
    pit_stops = store.pit_stops()

    try:
        lap_times = store.lap_times()
    except (DataNotAvailableError, UpstreamUnavailableError):
        lap_times = {}

    rows: List[Dict[str, Any]] = []
    for num, stops in pit_stops.items():
        driver_laps = lap_times.get(num, []) or []
        laps_by_no = {rec.get("lap"): rec for rec in driver_laps if rec.get("lap") is not None}
        for stop in stops:
            lap_no = stop.get("lap")
            lap_rec = laps_by_no.get(lap_no)
            timestamp_s = lap_rec.get("timestamp_s") if lap_rec else None
            rows.append({
                "driver_number": str(num),
                "stop_n": stop.get("stop_n"),
                "lap": lap_no,
                "pit_lane_time_s": stop.get("pit_lane_time_s"),
                "timestamp_s": timestamp_s,
            })
    return coerce(_with_keys(rows, target), spec)


def build_track_status(store: Any, target: Target) -> pd.DataFrame:
    """Contiguous track-status periods, with the label's numeric code attached."""
    spec = TABLES["track_status"]
    periods = store.track_status_periods()
    rows = []
    for period in periods:
        label = period.get("status")
        rows.append({
            "status_code": _STATUS_LABEL_TO_CODE.get(label),
            "status_label": label,
            "start_s": period.get("start_time_s"),
            "end_s": period.get("end_time_s"),
            "start_lap": period.get("start_lap"),
            "end_lap": period.get("end_lap"),
        })
    return coerce(_with_keys(rows, target), spec)


def _truthy_rainfall(value: Any) -> Any:
    if value in ("1", 1, True, "true", "True"):
        return True
    if value is None or value == "":
        return None
    return False


def build_weather(store: Any, target: Target) -> pd.DataFrame:
    """One row per weather sample."""
    spec = TABLES["weather"]
    entries = store.weather_data()
    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ts = entry.get("_timestamp")
        try:
            timestamp_s = parse_f1_time(ts) if ts else None
        except (TypeError, ValueError):
            timestamp_s = None

        def _float(key: str):
            value = entry.get(key)
            if value is None or value == "":
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        rows.append({
            "timestamp_s": timestamp_s,
            "air_temp": _float("AirTemp"),
            "track_temp": _float("TrackTemp"),
            "humidity": _float("Humidity"),
            "pressure": _float("Pressure"),
            "wind_speed": _float("WindSpeed"),
            "wind_direction": _float("WindDirection"),
            "rainfall": _truthy_rainfall(entry.get("Rainfall")),
        })
    return coerce(_with_keys(rows, target), spec)


def build_race_control(store: Any, target: Target) -> pd.DataFrame:
    """One row per raw race-control message, with a session-relative timestamp."""
    spec = TABLES["race_control"]
    session_start_utc = _session_start_utc_timestamp(store)

    rows = []
    for msg in store.race_control():
        if not isinstance(msg, dict):
            continue
        timestamp_s = None
        utc = msg.get("Utc")
        if utc and session_start_utc is not None:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(str(utc).replace("Z", "+00:00"))
                timestamp_s = dt.timestamp() - session_start_utc
            except (TypeError, ValueError):
                timestamp_s = None
        rows.append({
            "timestamp_s": timestamp_s,
            "lap": msg.get("Lap"),
            "category": msg.get("Category"),
            "flag": msg.get("Flag"),
            "scope": msg.get("Scope"),
            "sector": msg.get("Sector"),
            "driver_number": msg.get("RacingNumber"),
            "message": msg.get("Message"),
            "utc": utc,
        })
    return coerce(_with_keys(rows, target), spec)


def _results_race(store: Any, target: Target, driver_list: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    order = get_finishing_order(store.base_url, store.client, store=store)
    records = extract_lap_records(store.timing_data())

    max_laps = max(
        (rec.get("lap") for laps in records.values() for rec in laps if rec.get("lap") is not None),
        default=None,
    )

    # ``Retired``/``Stopped`` are the feed's own classification flags; the lap
    # count is only a fallback for seasons where they are missing (a car lapped
    # several times is still classified).
    retired: Dict[str, bool] = {}
    for entry in store.timing_data():
        lines = entry.get("Lines")
        if not isinstance(lines, dict):
            continue
        for num, line in lines.items():
            if isinstance(line, dict) and ("Retired" in line or "Stopped" in line):
                retired[str(num)] = bool(line.get("Retired") or line.get("Stopped"))

    rows = []
    for num, position in order.items():
        driver_laps = records.get(num, [])
        laps_completed = max((rec.get("lap") for rec in driver_laps if rec.get("lap") is not None), default=None)
        lap_times_s = [rec.get("lap_time_s") for rec in driver_laps if rec.get("lap_time_s") is not None]
        best_lap_time_s = min(lap_times_s) if lap_times_s else None

        classified_status = None
        if str(num) in retired:
            classified_status = "DNF" if retired[str(num)] else "FINISHED"
        elif laps_completed is not None and max_laps is not None:
            classified_status = "FINISHED" if laps_completed >= max_laps * 0.9 else "DNF"

        # The gap on the driver's final lap; the winner has none. Searching
        # further back would pick up a stale gap from before a position change.
        gap_to_winner = None
        if position != 1 and driver_laps:
            final_gap = driver_laps[-1].get("gap_to_leader_s")
            gap_to_winner = str(final_gap) if final_gap is not None else None

        info = driver_list.get(str(num), {})
        rows.append({
            "driver_number": str(num),
            "driver_code": info.get("tla"),
            "team_name": info.get("team"),
            "position": position,
            "classified_status": classified_status,
            "grid_position": None,
            "laps_completed": laps_completed,
            "gap_to_winner": gap_to_winner,
            "best_lap_time_s": best_lap_time_s,
            "q1_s": None,
            "q2_s": None,
            "q3_s": None,
        })
    return rows


def _results_qualifying(store: Any, driver_list: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    df = get_qualifying_classification(store.base_url, store.client, store=store)
    rows = []
    if df is None or df.empty:
        return rows
    for _, record in df.iterrows():
        num = str(record.get("DriverNum"))
        info = driver_list.get(num, {})
        position = record.get("Position")
        rows.append({
            "driver_number": num,
            "driver_code": info.get("tla"),
            "team_name": info.get("team"),
            "position": None if pd.isna(position) else position,
            "classified_status": None,
            "grid_position": None,
            "laps_completed": None,
            "gap_to_winner": None,
            "best_lap_time_s": record.get("LapTime"),
            "q1_s": None,
            "q2_s": None,
            "q3_s": None,
        })
    return rows


def _results_practice(store: Any, driver_list: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    records = extract_lap_records(store.timing_data())
    best_by_driver = []
    for num, laps in records.items():
        lap_times_s = [rec.get("lap_time_s") for rec in laps if rec.get("lap_time_s") is not None]
        if not lap_times_s:
            continue
        best_by_driver.append((num, min(lap_times_s)))
    best_by_driver.sort(key=lambda item: item[1])

    rows = []
    for position, (num, best_lap_time_s) in enumerate(best_by_driver, start=1):
        info = driver_list.get(str(num), {})
        rows.append({
            "driver_number": str(num),
            "driver_code": info.get("tla"),
            "team_name": info.get("team"),
            "position": position,
            "classified_status": None,
            "grid_position": None,
            "laps_completed": None,
            "gap_to_winner": None,
            "best_lap_time_s": best_lap_time_s,
            "q1_s": None,
            "q2_s": None,
            "q3_s": None,
        })
    return rows


def build_results(store: Any, target: Target) -> pd.DataFrame:
    """Classification, keyed by session type: race/sprint, qualifying, or practice."""
    spec = TABLES["results"]
    _year, _round_nr, _gp_name, session_abbrev = target
    driver_list = _safe_driver_list(store)

    if session_abbrev in _RACE_ABBREVS:
        rows = _results_race(store, target, driver_list)
    elif session_abbrev in _QUALI_ABBREVS:
        rows = _results_qualifying(store, driver_list)
    else:
        rows = _results_practice(store, driver_list)

    return coerce(_with_keys(rows, target), spec)


_BUILDERS = {
    "sessions": build_sessions,
    "drivers": build_drivers,
    "laps": build_laps,
    "stints": build_stints,
    "pit_stops": build_pit_stops,
    "track_status": build_track_status,
    "weather": build_weather,
    "race_control": build_race_control,
    "results": build_results,
}


def build_tables(store: Any, target: Target) -> Tuple[Dict[str, pd.DataFrame], List[str]]:
    """Run every ``tables``-tier builder for one session.

    Never aborts: a builder that raises is logged and replaced with an empty,
    well-formed frame (key columns still populated) plus a warning string
    ``"<table>: <exc>"`` in the returned list.
    """
    frames: Dict[str, pd.DataFrame] = {}
    warnings: List[str] = []

    for spec in tables_for_tier("tables"):
        builder = _BUILDERS[spec.name]
        try:
            frames[spec.name] = builder(store, target)
        except Exception as exc:  # noqa: BLE001 - one bad table must not abort the export
            logger.exception("Dataset export builder failed for table %s", spec.name)
            frames[spec.name] = empty_frame(spec)
            warnings.append(f"{spec.name}: {exc}")

    return frames, warnings
