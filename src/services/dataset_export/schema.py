"""Canonical table definitions for the dataset export.

Every exported table is declared here as a :class:`TableSpec` — its tier, its columns and
each column's pandas nullable dtype. ``coerce`` and ``empty_frame`` use these declarations
so every Parquet file written for a table shares an identical Arrow schema regardless of
which session produced it, and so a session missing some fields (e.g. no weather stream)
still produces a well-formed, all-null frame rather than a ragged one.
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple

import pandas as pd
import pyarrow as pa

DATASET_SCHEMA_VERSION = "1.0.0"

TIERS = ("tables", "telemetry", "raw", "corpus")

# One session to export: (year, round_nr, gp_name, session_abbrev). The abbreviation
# (``R``, ``Q``, ``FP1``, ``S``, ``SQ``) is what ``plot_inventory._enumerate_targets``
# yields and what the GridFS raw-stream cache is keyed by.
Target = Tuple[int, int, str, str]


def key_values(target: Target) -> Dict[str, object]:
    """The four key columns every table row carries, for a target."""
    year, round_nr, _gp, session = target
    return {
        "year": year,
        "round": round_nr,
        "session": session,
        "session_key": f"{year}_{round_nr:02d}_{session}",
    }


@dataclass(frozen=True)
class ColumnSpec:
    """One column of a table: its name, pandas nullable dtype and a short description."""

    name: str
    dtype: str
    description: str = ""


@dataclass(frozen=True)
class TableSpec:
    """One exportable table: its name, tier and ordered columns."""

    name: str
    tier: str
    columns: Tuple[ColumnSpec, ...]

    @property
    def column_names(self) -> List[str]:
        return [c.name for c in self.columns]


KEY_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("year", "Int64", "Season year."),
    ColumnSpec("round", "Int64", "Championship round number."),
    ColumnSpec("session", "string", "Session code (FP1, FP2, FP3, Q, SQ, R, S)."),
    ColumnSpec("session_key", "string", "Stable session identifier: {year}_{round:02d}_{session}."),
)


def _table(name: str, tier: str, *cols: ColumnSpec) -> TableSpec:
    """Build a TableSpec, prepending the shared KEY_COLUMNS to the given columns."""
    return TableSpec(name=name, tier=tier, columns=KEY_COLUMNS + tuple(cols))


TABLES: Dict[str, TableSpec] = {
    "sessions": _table(
        "sessions",
        "tables",
        ColumnSpec("gp_name", "string", "Grand Prix name."),
        ColumnSpec("official_name", "string", "Official event name."),
        ColumnSpec("circuit_key", "Int64", "Livetiming circuit key."),
        ColumnSpec("circuit_short_name", "string", "Short circuit name."),
        ColumnSpec("country", "string", "Country of the event."),
        ColumnSpec("location", "string", "Location/city of the event."),
        ColumnSpec("session_type", "string", "Session type label."),
        ColumnSpec("start_utc", "string", "Session start time, ISO 8601 UTC."),
        ColumnSpec("end_utc", "string", "Session end time, ISO 8601 UTC."),
        ColumnSpec("gmt_offset", "string", "Local GMT offset string."),
        ColumnSpec("total_laps", "Int64", "Total laps scheduled/completed."),
        ColumnSpec("is_complete", "boolean", "Whether the session has finished."),
        ColumnSpec("livetiming_path", "string", "Livetiming source path for this session."),
    ),
    "drivers": _table(
        "drivers",
        "tables",
        ColumnSpec("driver_number", "string", "Car number as a string."),
        ColumnSpec("driver_code", "string", "Three-letter driver code."),
        ColumnSpec("full_name", "string", "Driver full name."),
        ColumnSpec("team_name", "string", "Team name."),
        ColumnSpec("team_color", "string", "Team color hex code."),
        ColumnSpec("line", "Int64", "Timing tower line/order."),
        ColumnSpec("reference_number", "string", "Reference driver number, if remapped."),
    ),
    "laps": _table(
        "laps",
        "tables",
        ColumnSpec("driver_number", "string", "Car number as a string."),
        ColumnSpec("driver_code", "string", "Three-letter driver code."),
        ColumnSpec("lap", "Int64", "Lap number."),
        ColumnSpec("lap_time_s", "Float64", "Lap time in seconds."),
        ColumnSpec("sector1_s", "Float64", "Sector 1 time in seconds."),
        ColumnSpec("sector2_s", "Float64", "Sector 2 time in seconds."),
        ColumnSpec("sector3_s", "Float64", "Sector 3 time in seconds."),
        ColumnSpec("speed_i1", "Float64", "Speed trap I1 (km/h)."),
        ColumnSpec("speed_i2", "Float64", "Speed trap I2 (km/h)."),
        ColumnSpec("speed_fl", "Float64", "Speed trap at the finish line (km/h)."),
        ColumnSpec("speed_st", "Float64", "Speed trap on the speed straight (km/h)."),
        ColumnSpec("position", "Int64", "Track position at the end of the lap."),
        ColumnSpec("gap_to_leader_s", "Float64", "Gap to the race leader in seconds."),
        ColumnSpec("interval_s", "Float64", "Interval to the car ahead in seconds."),
        ColumnSpec("compound", "string", "Tyre compound used on this lap."),
        ColumnSpec("tyre_life", "Int64", "Tyre age in laps."),
        ColumnSpec("stint_number", "Int64", "Stint number this lap belongs to."),
        ColumnSpec("is_pit_in", "boolean", "Whether this lap ended with a pit-in."),
        ColumnSpec("is_pit_out", "boolean", "Whether this lap started with a pit-out."),
        ColumnSpec("is_pit_lap", "boolean", "Whether this lap involved a pit stop."),
        ColumnSpec("timestamp_start_s", "Float64", "Lap start time, session-relative seconds."),
        ColumnSpec("timestamp_end_s", "Float64", "Lap end time, session-relative seconds."),
        ColumnSpec("track_status_code", "Int64", "Track status code during the lap."),
        ColumnSpec("track_status_label", "string", "Track status label during the lap."),
        ColumnSpec("is_deleted", "boolean", "Whether the lap time was deleted."),
        ColumnSpec("is_personal_best", "boolean", "Whether this was the driver's personal best."),
        ColumnSpec("is_overall_fastest", "boolean", "Whether this was the overall fastest lap."),
    ),
    "stints": _table(
        "stints",
        "tables",
        ColumnSpec("driver_number", "string", "Car number as a string."),
        ColumnSpec("stint_number", "Int64", "Stint number."),
        ColumnSpec("compound", "string", "Tyre compound used in the stint."),
        ColumnSpec("is_new", "boolean", "Whether the tyre set was new at stint start."),
        ColumnSpec("start_lap", "Int64", "First lap of the stint."),
        ColumnSpec("end_lap", "Int64", "Last lap of the stint."),
        ColumnSpec("lap_count", "Int64", "Number of laps in the stint."),
        ColumnSpec("tyre_life_start", "Int64", "Tyre age at stint start."),
        ColumnSpec("tyre_life_end", "Int64", "Tyre age at stint end."),
    ),
    "pit_stops": _table(
        "pit_stops",
        "tables",
        ColumnSpec("driver_number", "string", "Car number as a string."),
        ColumnSpec("stop_n", "Int64", "Pit stop sequence number for the driver."),
        ColumnSpec("lap", "Int64", "Lap the stop occurred on."),
        ColumnSpec("pit_lane_time_s", "Float64", "Time spent in the pit lane, seconds."),
        ColumnSpec("timestamp_s", "Float64", "Stop time, session-relative seconds."),
    ),
    "track_status": _table(
        "track_status",
        "tables",
        ColumnSpec("status_code", "Int64", "Track status code."),
        ColumnSpec("status_label", "string", "Track status label."),
        ColumnSpec("start_s", "Float64", "Period start, session-relative seconds."),
        ColumnSpec("end_s", "Float64", "Period end, session-relative seconds."),
        ColumnSpec("start_lap", "Int64", "First lap covered by the period."),
        ColumnSpec("end_lap", "Int64", "Last lap covered by the period."),
    ),
    "weather": _table(
        "weather",
        "tables",
        ColumnSpec("timestamp_s", "Float64", "Sample time, session-relative seconds."),
        ColumnSpec("air_temp", "Float64", "Air temperature, degrees Celsius."),
        ColumnSpec("track_temp", "Float64", "Track temperature, degrees Celsius."),
        ColumnSpec("humidity", "Float64", "Relative humidity, percent."),
        ColumnSpec("pressure", "Float64", "Air pressure, mbar."),
        ColumnSpec("wind_speed", "Float64", "Wind speed, m/s."),
        ColumnSpec("wind_direction", "Float64", "Wind direction, degrees."),
        ColumnSpec("rainfall", "boolean", "Whether rain was detected."),
    ),
    "race_control": _table(
        "race_control",
        "tables",
        ColumnSpec("timestamp_s", "Float64", "Message time, session-relative seconds."),
        ColumnSpec("lap", "Int64", "Lap the message relates to."),
        ColumnSpec("category", "string", "Race control message category."),
        ColumnSpec("flag", "string", "Flag referenced by the message, if any."),
        ColumnSpec("scope", "string", "Scope of the message (track, sector, driver)."),
        ColumnSpec("sector", "Int64", "Sector number, if scoped to one."),
        ColumnSpec("driver_number", "string", "Car number referenced, if any."),
        ColumnSpec("message", "string", "Message text."),
        ColumnSpec("utc", "string", "Message time, ISO 8601 UTC."),
    ),
    "results": _table(
        "results",
        "tables",
        ColumnSpec("driver_number", "string", "Car number as a string."),
        ColumnSpec("driver_code", "string", "Three-letter driver code."),
        ColumnSpec("team_name", "string", "Team name."),
        ColumnSpec("position", "Int64", "Classified finishing position."),
        ColumnSpec("classified_status", "string", "Classification/retirement status."),
        ColumnSpec("grid_position", "Int64", "Starting grid position."),
        ColumnSpec("laps_completed", "Int64", "Number of laps completed."),
        ColumnSpec("gap_to_winner", "string", "Gap to the winner, as reported."),
        ColumnSpec("best_lap_time_s", "Float64", "Best lap time in seconds."),
        ColumnSpec("q1_s", "Float64", "Qualifying Q1 time in seconds."),
        ColumnSpec("q2_s", "Float64", "Qualifying Q2 time in seconds."),
        ColumnSpec("q3_s", "Float64", "Qualifying Q3 time in seconds."),
    ),
    "car_telemetry": _table(
        "car_telemetry",
        "telemetry",
        ColumnSpec("session_time_s", "Float64", "Sample time, session-relative seconds."),
        ColumnSpec("utc", "string", "Sample time, ISO 8601 UTC."),
        ColumnSpec("driver_number", "string", "Car number as a string."),
        ColumnSpec("rpm", "Int64", "Engine RPM."),
        ColumnSpec("speed", "Int64", "Car speed, km/h."),
        ColumnSpec("gear", "Int64", "Gear number."),
        ColumnSpec("throttle", "Int64", "Throttle position, percent."),
        ColumnSpec("brake", "Int64", "Brake applied (0/1 or percent)."),
        ColumnSpec("drs", "Int64", "DRS status code."),
    ),
    "position_telemetry": _table(
        "position_telemetry",
        "telemetry",
        ColumnSpec("session_time_s", "Float64", "Sample time, session-relative seconds."),
        ColumnSpec("utc", "string", "Sample time, ISO 8601 UTC."),
        ColumnSpec("driver_number", "string", "Car number as a string."),
        ColumnSpec("status", "string", "Position status (OnTrack/OffTrack/etc.)."),
        ColumnSpec("x", "Float64", "X coordinate, tenths of a metre."),
        ColumnSpec("y", "Float64", "Y coordinate, tenths of a metre."),
        ColumnSpec("z", "Float64", "Z coordinate, tenths of a metre."),
    ),
}


def tables_for_tier(tier: str) -> List[TableSpec]:
    """Return all TableSpecs belonging to the given tier, in TABLES iteration order."""
    return [spec for spec in TABLES.values() if spec.tier == tier]


def empty_frame(spec: TableSpec) -> pd.DataFrame:
    """Build a zero-row DataFrame with the table's declared columns and dtypes."""
    data = {col.name: pd.array([], dtype=col.dtype) for col in spec.columns}
    return pd.DataFrame(data, columns=spec.column_names)


def _coerce_boolean(series: pd.Series) -> pd.Series:
    """Map truthy/falsy/None values safely to pandas BooleanDtype, never raising."""

    def _to_bool(value):
        if value is None:
            return None
        if not isinstance(value, (list, dict)):
            try:
                if pd.isna(value):
                    return None
            except (TypeError, ValueError):
                pass
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes", "y"):
                return True
            if lowered in ("false", "0", "no", "n", ""):
                return False
            return None
        return None

    return series.map(_to_bool).astype("boolean")


def coerce(df: pd.DataFrame, spec: TableSpec) -> pd.DataFrame:
    """Reindex and cast a DataFrame to match a TableSpec exactly.

    Missing columns become all-null; extra columns are dropped. Numeric columns go
    through ``pd.to_numeric(errors="coerce")`` first so malformed values (e.g. a
    "1:23.456" string in a float column) become NA instead of raising. Never raises on
    odd input, and is idempotent.
    """
    out = df.reindex(columns=spec.column_names)

    for col in spec.columns:
        series = out[col.name]
        try:
            if col.dtype == "Float64":
                out[col.name] = pd.to_numeric(series, errors="coerce").astype("Float64")
            elif col.dtype == "Int64":
                numeric = pd.to_numeric(series, errors="coerce").astype("Float64")
                out[col.name] = numeric.round().astype("Int64")
            elif col.dtype == "string":
                out[col.name] = series.astype("string")
            elif col.dtype == "boolean":
                out[col.name] = _coerce_boolean(series)
            else:
                out[col.name] = series
        except Exception:  # noqa: BLE001 - coercion must never raise on odd input
            out[col.name] = pd.array([None] * len(out), dtype=col.dtype)

    return out[spec.column_names]


_ARROW_DTYPES = {
    "Int64": pa.int64(),
    "Float64": pa.float64(),
    "string": pa.string(),
    "boolean": pa.bool_(),
}


# Columns encoded in the Hive directory path (``year=/round=/session=``). They are
# NOT stored inside the Parquet file: pyarrow/DuckDB/Spark restore them from the
# path when a table directory is read, and a column of the same name inside the
# file makes those readers refuse to merge the schema. ``session_key`` stays in
# the file so a single part file is still self-describing.
PARTITION_COLUMNS = ("year", "round", "session")


def file_columns(spec: TableSpec) -> List[str]:
    """Columns physically written to a partitioned Parquet file."""
    return [c.name for c in spec.columns if c.name not in PARTITION_COLUMNS]


def arrow_schema(spec: TableSpec) -> pa.Schema:
    """The pyarrow Schema of a written file (partition columns excluded), identical for every session."""
    fields = [pa.field(col.name, _ARROW_DTYPES[col.dtype], nullable=True)
              for col in spec.columns if col.name not in PARTITION_COLUMNS]
    return pa.schema(fields)
