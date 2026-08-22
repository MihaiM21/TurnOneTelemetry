"""
Full-lap "everything" data (V2, livetiming-backed).

Given a session + driver + lap number, returns the complete data picture for
that single lap in one JSON payload:

  * full high-frequency telemetry time-series (speed / rpm / throttle / brake /
    gear / drs) with per-sample X/Y/Z and cumulative track distance,
  * lap metadata (time, session timestamp, pit in/out, race position),
  * tyre / stint context (compound, tyre life, stint window),
  * best sector times,
  * the nearest weather sample,
  * the track-status period(s) covering the lap,
  * driver and session metadata.

This module is pure composition of existing accessors: ``SessionDataStore`` for
the parsed streams and the ``_helpers`` extract functions for the per-lap
telemetry/position windows. It adds no new data acquisition.

Any session works (practice / qualifying / race all publish CarData + Position).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.repositories.plots import store_data_dict_to_mongo
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._helpers import (
    compute_distance,
    extract_position_for_lap,
    extract_telemetry_for_lap,
    merge_distance_onto_telemetry,
    parse_f1_time,
)
from src.services.analysis.v2.session_store import SessionDataStore

logger = get_logger(__name__)

DATA_TYPE = "lap_all_data"

# CarData channel keys pulled for the full picture (see CHANNEL_NAMES in
# _helpers). '47' (DRS) is not in CHANNEL_NAMES, so it lands under the raw key
# '47' — we normalize the telemetry row keys below regardless.
_CHANNELS = ["2", "3", "4", "5", "45", "47"]


def _data_type(tla: str, lap: int) -> str:
    return f"{DATA_TYPE}_{tla.upper()}_{lap}"


def _resolve_driver(store: SessionDataStore, tla: str) -> tuple[str, Dict[str, Any]]:
    """Return ``(car_number, driver_info)`` for a TLA, or raise DataNotAvailable."""
    wanted = tla.strip().upper()
    driver_list = store.driver_list()
    for num, info in driver_list.items():
        if str(info.get("tla", "")).upper() == wanted:
            return str(num), info
    valid = sorted({str(i.get("tla")) for i in driver_list.values() if i.get("tla")})
    raise DataNotAvailableError(
        year=store.year, gp=store.identifier, session=store.session_name,
        source="livetiming",
        reason=f"Unknown driver {tla!r}. Valid drivers: {', '.join(valid)}",
    )


def _find_lap(lap_records: List[Dict[str, Any]], lap: int) -> Dict[str, Any]:
    for rec in lap_records:
        if rec.get("lap") == lap:
            return rec
    available = sorted(r.get("lap") for r in lap_records if r.get("lap") is not None)
    raise DataNotAvailableError(
        reason=f"Lap {lap} not found. Recorded laps: {available}",
    )


def _stint_for_lap(stints: List[Dict[str, Any]], lap: int) -> Optional[Dict[str, Any]]:
    for st in stints:
        start = st.get("start_lap")
        end = st.get("end_lap")
        if start is None:
            continue
        if lap >= start and (end is None or lap <= end):
            return st
    return None


def _nearest_weather(weather: List[Dict[str, Any]], timestamp_s: float) -> Optional[Dict[str, Any]]:
    """Weather sample whose session time is closest to the lap completion time."""
    best = None
    best_gap = None
    for w in weather:
        t = w.get("timestamp_s")
        if t is None:
            t = parse_f1_time(w.get("_timestamp", w.get("T", "")))
        gap = abs(float(t) - timestamp_s)
        if best_gap is None or gap < best_gap:
            best_gap, best = gap, w
    return best


def _track_status_for_lap(periods: List[Dict[str, Any]], lap: int) -> List[Dict[str, Any]]:
    covering = []
    for p in periods:
        start = p.get("start_lap")
        end = p.get("end_lap")
        if start is None:
            continue
        if lap >= start and (end is None or lap <= end):
            covering.append({"status": p.get("status"), "start_lap": start, "end_lap": end})
    return covering


def _build_telemetry_rows(store: SessionDataStore, car_num: str,
                          start_t: float, end_t: float) -> List[Dict[str, Any]]:
    """Full per-sample telemetry rows with distance + X/Y/Z merged on."""
    df_tel = extract_telemetry_for_lap(
        store.base_url, store.client, car_num, start_t, end_t,
        channels=_CHANNELS, store=store,
    )
    df_pos = extract_position_for_lap(
        store.base_url, store.client, car_num, start_t, end_t, store=store,
    )
    df_pos = compute_distance(df_pos)
    merged = merge_distance_onto_telemetry(df_tel, df_pos)
    if merged.empty:
        return []

    # Attach X/Y/Z by nearest-time so each telemetry row is self-contained.
    if not df_pos.empty:
        import pandas as pd
        pos_xyz = df_pos[["Time", "X", "Y", "Z"]].sort_values("Time")
        merged = pd.merge_asof(
            merged.sort_values("Time"), pos_xyz, on="Time", direction="nearest",
        )

    rows: List[Dict[str, Any]] = []
    for _, r in merged.iterrows():
        rows.append({
            "time": float(r.get("Time", 0.0)),
            "distance": float(r.get("Distance", 0.0)),
            "speed": _opt_float(r.get("Speed")),
            "rpm": _opt_float(r.get("RPM")),
            "throttle": _opt_float(r.get("Throttle")),
            "brake": _opt_float(r.get("Brake")),
            "gear": _opt_int(r.get("Gear")),
            "drs": _opt_int(r.get("47")),
            "x": _opt_float(r.get("X")),
            "y": _opt_float(r.get("Y")),
            "z": _opt_float(r.get("Z")),
        })
    return rows


def _opt_float(v) -> Optional[float]:
    try:
        import pandas as pd
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _opt_int(v) -> Optional[int]:
    f = _opt_float(v)
    return int(f) if f is not None else None


def _build_payload(store: SessionDataStore, tla: str, lap: int) -> Dict[str, Any]:
    car_num, driver_info = _resolve_driver(store, tla)

    lap_records = store.lap_times().get(car_num, [])
    lap_rec = _find_lap(lap_records, lap)
    time_s = float(lap_rec.get("time_s") or 0.0)
    timestamp_s = float(lap_rec.get("timestamp_s") or 0.0)
    start_t = timestamp_s - time_s
    end_t = timestamp_s

    telemetry = _build_telemetry_rows(store, car_num, start_t, end_t)
    if not telemetry:
        raise DataNotAvailableError(
            year=store.year, gp=store.identifier, session=store.session_name,
            source="livetiming",
            reason=f"No telemetry available for {tla.upper()} lap {lap}",
        )

    # Position at end of this lap (race classification).
    position = None
    for rec in store.positions_by_lap().get(car_num, []):
        if rec.get("lap") == lap:
            position = rec.get("position")

    stint = _stint_for_lap(store.stints().get(car_num, []), lap)
    sectors = store.best_sectors().get(car_num, {})
    weather = _nearest_weather(store.weather_data(), timestamp_s)
    track_status = _track_status_for_lap(store.track_status_periods(), lap)

    return {
        "driver": {
            "tla": driver_info.get("tla", tla.upper()),
            "car_number": car_num,
            "name": driver_info.get("name", ""),
            "team": driver_info.get("team", ""),
            "color": driver_info.get("color", ""),
            "racing_number": driver_info.get("racing_number", car_num),
        },
        "lap": {
            "number": lap,
            "lap_time_s": time_s,
            "timestamp_s": timestamp_s,
            "pit_in": bool(lap_rec.get("pit_in", False)),
            "pit_out": bool(lap_rec.get("pit_out", False)),
            "position": position,
        },
        "tyre": {
            "compound": stint.get("compound") if stint else None,
            "stint_number": stint.get("stint_number") if stint else None,
            "tyre_life_end": stint.get("tyre_life_end") if stint else None,
            "start_lap": stint.get("start_lap") if stint else None,
            "end_lap": stint.get("end_lap") if stint else None,
        },
        "sectors": {
            "s1": sectors.get("s1"),
            "s2": sectors.get("s2"),
            "s3": sectors.get("s3"),
        },
        "weather": weather,
        "track_status": track_status,
        "telemetry": telemetry,
        "session_info": {
            "year": store.year,
            "event_name": store.event_name,
            "session_name": store.session_name,
        },
    }


class LapAllData:
    """Callable: ``LapAllData()(year, identifier, session, driver, lap) -> dict``."""

    def __call__(
        self, y: int, identifier: Union[int, str], e: str,
        driver: str, lap: int,
    ) -> Dict[str, Any]:
        tla = driver.strip().upper()

        def _generate() -> Dict[str, Any]:
            store = SessionDataStore(y, identifier, e)
            payload = _build_payload(store, tla, lap)
            try:
                store_data_dict_to_mongo(
                    year=y, round_nr=store.round_nr, session_name=e,
                    event_name=store.event_name, data_type=_data_type(tla, lap),
                    data=payload, version="v2",
                )
            except Exception as exc:  # persistence is best-effort
                logger.debug("Failed to persist lap_all_data to Mongo: %s", exc)
            return payload

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=_data_type(tla, lap), generator=_generate, version="v2",
        )


__all__ = ["LapAllData"]
