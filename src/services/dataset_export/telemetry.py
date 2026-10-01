"""Long-format telemetry export for the dataset export ``telemetry`` tier.

Reshapes the raw ``car_data`` / ``position_data`` streams (one JSON blob per
sample batch, all cars nested inside) into flat, chunked DataFrames matching
``TABLES["car_telemetry"]`` / ``TABLES["position_telemetry"]`` -- one row per
(sample, driver). A full session's telemetry can be tens of millions of rows,
so both functions are generators: each accumulates plain Python lists (far
cheaper than a list of per-row dicts) and yields a coerced chunk every
``chunk_rows`` samples rather than building the whole table in memory.
"""

from datetime import datetime
from typing import Dict, Iterator, List

import pandas as pd

from src.services.analysis.v2._helpers import CAR_DATA_CHANNELS, parse_f1_time
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.dataset_export.schema import TABLES, Target, coerce, key_values

DEFAULT_CHUNK_ROWS = 500_000

_CAR_SPEC = TABLES["car_telemetry"]
_POSITION_SPEC = TABLES["position_telemetry"]


def _get_session_start_utc(entries_list: list, packet_time: float):
    if entries_list:
        first_utc_str = entries_list[0].get("Utc")
        if first_utc_str:
            first_dt = datetime.fromisoformat(first_utc_str.replace("Z", "+00:00"))
            return first_dt.timestamp() - packet_time
    return None


def _iter_car_data_samples_with_utc(entries):
    """Yield ``(session_time_s, utc_str, cars)`` for each CarData.z sample.

    Same calibration as ``_helpers._iter_car_data_samples``, but also yields
    the sample's own UTC string (the iterator there discards it).
    """
    session_start_utc = None
    for entry in entries:
        t_str = entry.get("_timestamp", entry.get("T"))
        packet_time = parse_f1_time(t_str)

        entries_list = entry.get("Entries", [])
        if not isinstance(entries_list, list):
            entries_list = [entries_list]

        if session_start_utc is None and packet_time > 0 and entries_list:
            session_start_utc = _get_session_start_utc(entries_list, packet_time)

        for item in entries_list:
            utc_str = item.get("Utc")
            sample_time = packet_time
            if utc_str and session_start_utc:
                dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
                sample_time = dt.timestamp() - session_start_utc
            yield sample_time, utc_str, item.get("Cars", {})


def _iter_position_samples_with_utc(entries):
    """Yield ``(session_time_s, utc_str, cars)`` for each Position.z frame.

    Same calibration as ``_helpers._iter_position_samples``, but also yields
    the frame's own UTC string.
    """
    session_start_utc = None
    for entry in entries:
        t_str = entry.get("_timestamp", entry.get("T"))
        packet_time = parse_f1_time(t_str)

        frames = entry.get("Position", [])
        if not frames:
            continue

        if session_start_utc is None and packet_time > 0:
            first_ts = frames[0].get("Timestamp") or frames[0].get("Utc")
            if first_ts:
                first_dt = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
                session_start_utc = first_dt.timestamp() - packet_time

        for frame in frames:
            ts_str = frame.get("Timestamp") or frame.get("Utc")
            sample_time = packet_time
            if ts_str and session_start_utc:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                sample_time = dt.timestamp() - session_start_utc
            yield sample_time, ts_str, frame.get("Entries", frame.get("Cars", {}))


def _new_columns(names: List[str]) -> Dict[str, list]:
    return {name: [] for name in names}


def iter_car_telemetry(
    store: SessionDataStore, target: Target, chunk_rows: int = DEFAULT_CHUNK_ROWS
) -> Iterator[pd.DataFrame]:
    """Long-format car telemetry: one row per (sample, driver), chunked.

    Columns match ``TABLES["car_telemetry"]``. Missing channels become None;
    channel values are kept as the feed's raw ints (``coerce`` casts them).
    Yields nothing for a session with zero car-data samples.
    """
    keys = key_values(target)
    channel_keys = list(CAR_DATA_CHANNELS.keys())
    columns = _new_columns(_CAR_SPEC.column_names)
    rows = 0

    for sample_time, utc_str, cars in _iter_car_data_samples_with_utc(store.car_data()):
        if not isinstance(cars, dict):
            continue
        for driver_number, car in cars.items():
            channels = car.get("Channels", {}) if isinstance(car, dict) else {}
            for key, value in keys.items():
                columns[key].append(value)
            columns["session_time_s"].append(sample_time)
            columns["utc"].append(utc_str)
            columns["driver_number"].append(str(driver_number))
            for ch_key in channel_keys:
                columns[CAR_DATA_CHANNELS[ch_key]].append(channels.get(ch_key))
            rows += 1

            if rows >= chunk_rows:
                yield coerce(pd.DataFrame(columns), _CAR_SPEC)
                columns = _new_columns(_CAR_SPEC.column_names)
                rows = 0

    if rows:
        yield coerce(pd.DataFrame(columns), _CAR_SPEC)


def iter_position_telemetry(
    store: SessionDataStore, target: Target, chunk_rows: int = DEFAULT_CHUNK_ROWS
) -> Iterator[pd.DataFrame]:
    """Long-format position telemetry: one row per (sample, driver), chunked.

    Columns match ``TABLES["position_telemetry"]``. x==y==0 rows are kept
    (this is the raw dataset; filtering the feed's "no fix" encoding is left
    to the consumer). Yields nothing for a session with zero position samples.
    """
    keys = key_values(target)
    columns = _new_columns(_POSITION_SPEC.column_names)
    rows = 0

    for sample_time, utc_str, cars in _iter_position_samples_with_utc(store.position_data()):
        if not isinstance(cars, dict):
            continue
        for driver_number, entry in cars.items():
            if not isinstance(entry, dict):
                continue
            for key, value in keys.items():
                columns[key].append(value)
            columns["session_time_s"].append(sample_time)
            columns["utc"].append(utc_str)
            columns["driver_number"].append(str(driver_number))
            columns["status"].append(entry.get("Status"))
            columns["x"].append(entry.get("X"))
            columns["y"].append(entry.get("Y"))
            columns["z"].append(entry.get("Z"))
            rows += 1

            if rows >= chunk_rows:
                yield coerce(pd.DataFrame(columns), _POSITION_SPEC)
                columns = _new_columns(_POSITION_SPEC.column_names)
                rows = 0

    if rows:
        yield coerce(pd.DataFrame(columns), _POSITION_SPEC)


__all__ = ["DEFAULT_CHUNK_ROWS", "iter_car_telemetry", "iter_position_telemetry"]
