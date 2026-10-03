"""Human-readable narratives and Q&A pairs for the dataset-export corpus.

Turns the per-session tables (``src/services/dataset_export/schema.py:TABLES``) into two
things an LLM can train or be evaluated on:

* a deterministic Markdown narrative (:func:`session_narrative` / :func:`narrative_record`)
* a fixed catalog of question/answer pairs (:func:`qa_pairs`)

Every table may be a zero-row ``empty_frame`` (a session missing a stream, or a practice
session with no pit stops). Every helper here degrades gracefully on missing/empty/NA data
instead of raising, and produces byte-identical output across runs: no dict-ordering
hazards, no randomness, no wall-clock timestamps.
"""

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from src.services.dataset_export.schema import Target, key_values
from src.services.dataset_export.writer import append_jsonl, rewrite_jsonl_without

NARRATIVES_FILENAME = "narratives.jsonl"
QA_FILENAME = "qa.jsonl"
CORPUS_DIR = "corpus"

SESSION_LABELS = {
    "R": "Race",
    "S": "Sprint",
    "Q": "Qualifying",
    "SQ": "Sprint Qualifying",
    "FP1": "Practice 1",
    "FP2": "Practice 2",
    "FP3": "Practice 3",
}

_NOTABLE_CATEGORIES = {"Flag", "SafetyCar", "Drs"}
_NOTABLE_KEYWORDS = ("DELETED", "PENALTY", "INVESTIGATION", "SAFETY CAR", "RED FLAG", "VIRTUAL")
_FULL_SC_LABELS = {"SC", "SAFETY CAR", "FULL SAFETY CAR", "FCSC"}
_RACE_CONTROL_CAP = 25


# --------------------------------------------------------------------------- small utils


def format_lap_time(seconds: Any) -> str:
    """93.6 -> "1:33.600"; ``None``/NaN/negative/non-numeric -> "n/a"."""
    if seconds is None:
        return "n/a"
    try:
        if pd.isna(seconds):
            return "n/a"
        value = float(seconds)
    except (TypeError, ValueError):
        return "n/a"
    if value < 0:
        return "n/a"
    minutes = int(value // 60)
    remainder = value - minutes * 60
    return f"{minutes}:{remainder:06.3f}"


def _isna(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return value is None


def _str(value: Any) -> Optional[str]:
    if _isna(value):
        return None
    text = str(value).strip()
    return text or None


def _val(row: Optional[pd.Series], col: str) -> Any:
    if row is None or col not in row.index:
        return None
    value = row[col]
    return None if _isna(value) else value


def _num_key(value: Optional[str]) -> Tuple[int, Any]:
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


def _get(tables: Dict[str, pd.DataFrame], name: str) -> pd.DataFrame:
    df = tables.get(name)
    return df if df is not None else pd.DataFrame()


def _is_empty(df: Optional[pd.DataFrame]) -> bool:
    return df is None or len(df) == 0


def _session_row(tables: Dict[str, pd.DataFrame]) -> Optional[pd.Series]:
    df = _get(tables, "sessions")
    return None if _is_empty(df) else df.iloc[0]


def _is_race_like(session: str) -> bool:
    return session in ("R", "S")


def _is_quali_like(session: str) -> bool:
    return session in ("Q", "SQ")


# ------------------------------------------------------------------------- driver lookups


def _driver_names(tables: Dict[str, pd.DataFrame]) -> Dict[str, Dict[str, Optional[str]]]:
    """``driver_number -> {"code", "name", "team"}``, from drivers/results tables."""
    info: Dict[str, Dict[str, Optional[str]]] = {}

    drivers_df = _get(tables, "drivers")
    if not _is_empty(drivers_df):
        for _, row in drivers_df.iterrows():
            num = _str(row.get("driver_number"))
            if not num:
                continue
            info[num] = {
                "code": _str(row.get("driver_code")),
                "name": _str(row.get("full_name")),
                "team": _str(row.get("team_name")),
            }

    results_df = _get(tables, "results")
    if not _is_empty(results_df):
        for _, row in results_df.iterrows():
            num = _str(row.get("driver_number"))
            if not num:
                continue
            entry = info.setdefault(num, {"code": None, "name": None, "team": None})
            if entry.get("code") is None:
                entry["code"] = _str(row.get("driver_code"))
            if entry.get("team") is None:
                entry["team"] = _str(row.get("team_name"))

    return info


def _driver_label(num: str, info: Dict[str, Dict[str, Optional[str]]], seen: Optional[set] = None) -> str:
    """Full name with code the first time ``num`` is seen in ``seen``; code only after."""
    entry = info.get(num, {})
    code = entry.get("code") or num
    name = entry.get("name")
    already_seen = seen is not None and num in seen
    if seen is not None:
        seen.add(num)
    if not already_seen and name:
        return f"{name} ({code})"
    return code


def _driver_order(results_df: pd.DataFrame, secondary_df: Optional[pd.DataFrame]) -> List[str]:
    """Finishing order from results, then any remaining drivers found only elsewhere."""
    order: List[str] = []
    seen: set = set()
    if not _is_empty(results_df):
        for _, row in results_df.sort_values("position", na_position="last").iterrows():
            num = _str(row.get("driver_number"))
            if num and num not in seen:
                order.append(num)
                seen.add(num)
    if secondary_df is not None and not _is_empty(secondary_df):
        extra = sorted(
            {n for n in secondary_df["driver_number"].map(_str) if n and n not in seen},
            key=_num_key,
        )
        order.extend(extra)
    return order


def _position_is(df: pd.DataFrame, position: int) -> pd.Series:
    """A safe boolean mask for ``df["position"] == position`` on a nullable Int64 column."""
    return (df["position"].fillna(-1) == position).astype(bool)


# ------------------------------------------------------------------- narrative: sections


def _facts_line(session_row: Optional[pd.Series], session: str) -> Optional[str]:
    if session_row is None:
        return None
    parts: List[str] = []
    circuit = _val(session_row, "circuit_short_name")
    if circuit:
        parts.append(f"Circuit: {circuit}")
    country = _val(session_row, "country")
    if country:
        parts.append(f"Country: {country}")
    start_utc = _val(session_row, "start_utc")
    if start_utc:
        date = str(start_utc).split("T")[0]
        if date:
            parts.append(f"Date: {date}")
    if _is_race_like(session):
        laps = _val(session_row, "total_laps")
        if laps is not None:
            try:
                parts.append(f"Laps: {int(laps)}")
            except (TypeError, ValueError):
                pass
    return " · ".join(parts) if parts else None


def _results_table_md(
    results_df: pd.DataFrame, session: str, info: Dict[str, Dict[str, Optional[str]]]
) -> Optional[str]:
    if _is_empty(results_df):
        return None
    use_time = not _is_race_like(session)
    top10 = results_df.sort_values("position", na_position="last").head(10)
    if top10.empty:
        return None

    last_col = "Time" if use_time else "Gap"
    lines = [f"| Pos | Driver | Team | {last_col} |", "|---|---|---|---|"]
    for _, row in top10.iterrows():
        pos = row.get("position")
        pos_s = "n/a" if _isna(pos) else str(int(pos))
        num = _str(row.get("driver_number"))
        label = _driver_label(num, info) if num else (_str(row.get("driver_code")) or "n/a")
        team = _str(row.get("team_name")) or "n/a"
        if use_time:
            value = format_lap_time(row.get("best_lap_time_s"))
        elif not _isna(pos) and int(pos) == 1:
            value = "WINNER"
        else:
            value = _str(row.get("gap_to_winner")) or "n/a"
        lines.append(f"| {pos_s} | {label} | {team} | {value} |")
    return "\n".join(lines)


def _dnf_mask(results_df: pd.DataFrame) -> pd.Series:
    return results_df["classified_status"].map(lambda v: _str(v) is not None and _str(v).upper() != "FINISHED")


def _dnf_line(results_df: pd.DataFrame, info: Dict[str, Dict[str, Optional[str]]]) -> Optional[str]:
    if _is_empty(results_df):
        return None
    dnf_rows = results_df[_dnf_mask(results_df)]
    if dnf_rows.empty:
        return None
    labels = []
    for _, row in dnf_rows.sort_values("position", na_position="last").iterrows():
        num = _str(row.get("driver_number"))
        labels.append(_driver_label(num, info) if num else (_str(row.get("driver_code")) or "?"))
    return "Did not finish: " + ", ".join(labels)


def _stint_string(driver_stints: pd.DataFrame) -> Optional[str]:
    if _is_empty(driver_stints):
        return None
    rows = driver_stints.sort_values("stint_number", na_position="last")
    parts = []
    for _, row in rows.iterrows():
        compound = _str(row.get("compound")) or "UNKNOWN"
        start = row.get("start_lap")
        end = row.get("end_lap")
        start_s = "?" if _isna(start) else str(int(start))
        end_s = "?" if _isna(end) else str(int(end))
        parts.append(f"{compound} ({start_s}-{end_s})")
    if not parts:
        return None
    stops = max(len(parts) - 1, 0)
    stop_word = "stop" if stops == 1 else "stops"
    return f"{' → '.join(parts)}, {stops} {stop_word}"


def _strategy_section(
    results_df: pd.DataFrame, stints_df: pd.DataFrame, info: Dict[str, Dict[str, Optional[str]]]
) -> Optional[str]:
    if _is_empty(stints_df):
        return None
    lines = []
    for num in _driver_order(results_df, stints_df):
        driver_stints = stints_df[stints_df["driver_number"].map(_str) == num]
        stint_str = _stint_string(driver_stints)
        if stint_str is None:
            continue
        code = info.get(num, {}).get("code") or num
        lines.append(f"{code}: {stint_str}")
    return "\n".join(lines) if lines else None


def _pit_stops_section(pit_df: pd.DataFrame, info: Dict[str, Dict[str, Optional[str]]]) -> Optional[str]:
    if _is_empty(pit_df):
        return None
    records = []
    for _, row in pit_df.iterrows():
        num = _str(row.get("driver_number"))
        lap = row.get("lap")
        lap_val = None if _isna(lap) else int(lap)
        records.append((lap_val, num, row.get("pit_lane_time_s")))
    records.sort(key=lambda r: (r[0] if r[0] is not None else -1, _num_key(r[1])))
    lines = []
    for lap_val, num, pit_time in records:
        code = (info.get(num, {}).get("code") if num else None) or num or "?"
        lap_s = "n/a" if lap_val is None else str(lap_val)
        time_s = "n/a" if _isna(pit_time) else f"{float(pit_time):.1f} s"
        lines.append(f"{code} lap {lap_s} ({time_s})")
    return "\n".join(lines) if lines else None


def _is_notable_rcm(category: Optional[str], message: str) -> bool:
    if category in _NOTABLE_CATEGORIES:
        return True
    upper = message.upper()
    return any(keyword in upper for keyword in _NOTABLE_KEYWORDS)


def _race_control_section(rc_df: pd.DataFrame) -> Optional[str]:
    if _is_empty(rc_df):
        return None
    notable = []
    for _, row in rc_df.iterrows():
        category = _str(row.get("category"))
        message = _str(row.get("message")) or ""
        if not message or not _is_notable_rcm(category, message):
            continue
        lap = row.get("lap")
        lap_val = None if _isna(lap) else int(lap)
        notable.append((lap_val, message))
    if not notable:
        return None
    notable.sort(key=lambda t: t[0] if t[0] is not None else float("inf"))
    lines = []
    for lap_val, message in notable[:_RACE_CONTROL_CAP]:
        if lap_val is not None:
            lines.append(f"- Lap {lap_val}: {message}")
        else:
            lines.append(f"- {message}")
    return "\n".join(lines)


def _track_status_section(ts_df: pd.DataFrame) -> Optional[str]:
    if _is_empty(ts_df):
        return None
    rows = []
    for _, row in ts_df.iterrows():
        label = _str(row.get("status_label"))
        if not label:
            continue
        start = row.get("start_lap")
        end = row.get("end_lap")
        start_s = "?" if _isna(start) else str(int(start))
        end_s = "?" if _isna(end) else str(int(end))
        sort_key = -1 if _isna(start) else int(start)
        rows.append((sort_key, f"{label} laps {start_s}-{end_s}"))
    if not rows:
        return None
    rows.sort(key=lambda t: t[0])
    return "\n".join(text for _, text in rows)


def _weather_line(weather_df: pd.DataFrame) -> Optional[str]:
    if _is_empty(weather_df):
        return None
    parts = []
    air = weather_df.get("air_temp")
    if air is not None:
        valid = air.dropna()
        if not valid.empty:
            parts.append(f"Air temp: {float(valid.min()):.0f}–{float(valid.max()):.0f}°C")
    track = weather_df.get("track_temp")
    if track is not None:
        valid = track.dropna()
        if not valid.empty:
            parts.append(f"Track temp: {float(valid.min()):.0f}–{float(valid.max()):.0f}°C")
    humidity = weather_df.get("humidity")
    if humidity is not None:
        valid = humidity.dropna()
        if not valid.empty:
            parts.append(f"Humidity: {float(valid.min()):.0f}–{float(valid.max()):.0f}%")
    rainfall = weather_df.get("rainfall")
    if rainfall is not None and not rainfall.dropna().empty:
        rained = bool(rainfall.fillna(False).astype(bool).any())
        parts.append(f"rain: {'yes' if rained else 'no'}")
    return " · ".join(parts) if parts else None


def session_narrative(target: Target, tables: Dict[str, pd.DataFrame]) -> str:
    """Deterministic Markdown narrative for one session. Never raises."""
    year, _round_nr, gp_name, session = target
    label = SESSION_LABELS.get(session, session)
    lines = [f"# {year} {gp_name} — {label}"]

    facts = _facts_line(_session_row(tables), session)
    if facts:
        lines.append("")
        lines.append(facts)

    results_df = _get(tables, "results")
    info = _driver_names(tables)

    results_md = _results_table_md(results_df, session, info)
    if results_md:
        lines.append("")
        lines.append("## Results")
        lines.append(results_md)
        dnf_line = _dnf_line(results_df, info)
        if dnf_line:
            lines.append("")
            lines.append(dnf_line)

    if _is_race_like(session):
        strategy_md = _strategy_section(results_df, _get(tables, "stints"), info)
        if strategy_md:
            lines.append("")
            lines.append("## Strategy")
            lines.append(strategy_md)

    pit_md = _pit_stops_section(_get(tables, "pit_stops"), info)
    if pit_md:
        lines.append("")
        lines.append("## Pit stops")
        lines.append(pit_md)

    rc_md = _race_control_section(_get(tables, "race_control"))
    if rc_md:
        lines.append("")
        lines.append("## Race control")
        lines.append(rc_md)

    ts_md = _track_status_section(_get(tables, "track_status"))
    if ts_md:
        lines.append("")
        lines.append("## Track status")
        lines.append(ts_md)

    weather_line = _weather_line(_get(tables, "weather"))
    if weather_line:
        lines.append("")
        lines.append("## Weather")
        lines.append(weather_line)

    return "\n".join(lines) + "\n"


def narrative_record(target: Target, tables: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    year, round_nr, gp_name, session = target
    keys = key_values(target)
    return {
        "id": keys["session_key"],
        "session_key": keys["session_key"],
        "year": year,
        "round": round_nr,
        "session": session,
        "gp_name": gp_name,
        "text": session_narrative(target, tables),
        "source": "t1api-dataset-export",
    }


# --------------------------------------------------------------------------------- Q&A


def _qa_winner(
    results_df: pd.DataFrame, info: Dict[str, Dict[str, Optional[str]]], gp_name: str, label: str, session: str
) -> List[Tuple[str, str]]:
    if not _is_race_like(session) or _is_empty(results_df):
        return []
    winners = results_df[_position_is(results_df, 1)]
    if winners.empty:
        return []
    row = winners.iloc[0]
    num = _str(row.get("driver_number"))
    seen: set = set()
    driver = _driver_label(num, info, seen) if num else "the winner"
    team = (info.get(num, {}).get("team") if num else None) or _str(row.get("team_name"))
    question = f"Who won the {gp_name} {label}?"
    if team:
        answer = f"{driver} won the {gp_name} {label}, driving for {team}."
    else:
        answer = f"{driver} won the {gp_name} {label}."
    return [(question, answer)]


def _qa_podium(
    results_df: pd.DataFrame, info: Dict[str, Dict[str, Optional[str]]], gp_name: str, label: str, session: str
) -> List[Tuple[str, str]]:
    if not _is_race_like(session) or _is_empty(results_df):
        return []
    podium_rows = {}
    for pos in (1, 2, 3):
        matches = results_df[_position_is(results_df, pos)]
        if matches.empty:
            return []
        podium_rows[pos] = matches.iloc[0]
    seen: set = set()
    labels = []
    for pos in (1, 2, 3):
        row = podium_rows[pos]
        num = _str(row.get("driver_number"))
        labels.append(_driver_label(num, info, seen) if num else f"P{pos}")
    question = f"Who finished on the podium at the {gp_name} {label}?"
    answer = f"The podium was 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}."
    return [(question, answer)]


def _qa_pole(
    results_df: pd.DataFrame, info: Dict[str, Dict[str, Optional[str]]], label: str, session: str
) -> List[Tuple[str, str]]:
    if not _is_quali_like(session) or _is_empty(results_df):
        return []
    matches = results_df[_position_is(results_df, 1)]
    if matches.empty:
        return []
    row = matches.iloc[0]
    num = _str(row.get("driver_number"))
    seen: set = set()
    driver = _driver_label(num, info, seen) if num else "the pole-sitter"
    question = f"Who took pole position for the {label}?"
    answer = f"{driver} took pole position for the {label}."
    return [(question, answer)]


def _qa_fastest_lap(
    laps_df: pd.DataFrame, info: Dict[str, Dict[str, Optional[str]]], gp_name: str, label: str
) -> List[Tuple[str, str]]:
    if _is_empty(laps_df):
        return []
    # ``is_overall_fastest`` is a running flag (every new session best carries
    # it), so the minimum valid lap time is the only reliable answer.
    valid = laps_df.dropna(subset=["lap_time_s"])
    deleted = valid.get("is_deleted")
    if deleted is not None:
        valid = valid[~deleted.fillna(False).astype(bool)]
    if valid.empty:
        return []
    idx = valid["lap_time_s"].astype(float).idxmin()
    row = valid.loc[idx]
    num = _str(row.get("driver_number"))
    seen: set = set()
    driver = _driver_label(num, info, seen) if num else (_str(row.get("driver_code")) or "a driver")
    time_s = format_lap_time(row.get("lap_time_s"))
    question = f"Who set the fastest lap in the {gp_name} {label}?"
    answer = f"{driver} set the fastest lap of the session, {time_s}."
    return [(question, answer)]


def _qa_finishing_position(
    results_df: pd.DataFrame, info: Dict[str, Dict[str, Optional[str]]], label: str
) -> List[Tuple[str, str]]:
    if _is_empty(results_df):
        return []
    pairs = []
    for _, row in results_df.sort_values("position", na_position="last").iterrows():
        pos = row.get("position")
        if _isna(pos):
            continue
        num = _str(row.get("driver_number"))
        seen: set = set()
        driver = _driver_label(num, info, seen) if num else (_str(row.get("driver_code")) or "The driver")
        question = f"Where did {driver} finish in the {label}?"
        answer = f"{driver} finished P{int(pos)} in the {label}."
        pairs.append((question, answer))
    return pairs


def _qa_dnf(
    results_df: pd.DataFrame, info: Dict[str, Dict[str, Optional[str]]], label: str, session: str
) -> List[Tuple[str, str]]:
    if not _is_race_like(session) or _is_empty(results_df):
        return []
    dnf_rows = results_df[_dnf_mask(results_df)]
    if dnf_rows.empty:
        return []
    seen: set = set()
    labels = []
    for _, row in dnf_rows.sort_values("position", na_position="last").iterrows():
        num = _str(row.get("driver_number"))
        labels.append(_driver_label(num, info, seen) if num else (_str(row.get("driver_code")) or "a driver"))
    question = f"Which drivers did not finish the {label}?"
    if len(labels) == 1:
        answer = f"{labels[0]} did not finish the {label}."
    else:
        answer = ", ".join(labels[:-1]) + f" and {labels[-1]} did not finish the {label}."
    return [(question, answer)]


def _qa_pit_count(
    pit_df: pd.DataFrame, info: Dict[str, Dict[str, Optional[str]]], label: str, session: str
) -> List[Tuple[str, str]]:
    if _is_empty(pit_df):
        return []
    counts: Dict[str, int] = {}
    for num in pit_df["driver_number"].map(_str):
        if num:
            counts[num] = counts.get(num, 0) + 1
    pairs = []
    for num in sorted(counts, key=_num_key):
        n = counts[num]
        if n < 1:
            continue
        seen: set = set()
        driver = _driver_label(num, info, seen)
        word = "stop" if n == 1 else "stops"
        question = f"How many pit stops did {driver} make in the {label}?"
        answer = f"{driver} made {n} pit {word} in the {label}."
        pairs.append((question, answer))
    return pairs


def _qa_strategy(
    results_df: pd.DataFrame,
    stints_df: pd.DataFrame,
    info: Dict[str, Dict[str, Optional[str]]],
    label: str,
    session: str,
) -> List[Tuple[str, str]]:
    if not _is_race_like(session) or _is_empty(stints_df):
        return []
    pairs = []
    for num in _driver_order(results_df, stints_df):
        driver_stints = stints_df[stints_df["driver_number"].map(_str) == num]
        stint_str = _stint_string(driver_stints)
        if stint_str is None:
            continue
        seen: set = set()
        driver = _driver_label(num, info, seen)
        question = f"What tyre strategy did {driver} use in the {label}?"
        answer = f"{driver} ran: {stint_str}."
        pairs.append((question, answer))
    return pairs


def _qa_stop_strategies(
    stints_df: pd.DataFrame, info: Dict[str, Dict[str, Optional[str]]], label: str, session: str
) -> List[Tuple[str, str]]:
    if not _is_race_like(session) or _is_empty(stints_df):
        return []
    counts = stints_df.groupby(stints_df["driver_number"].map(_str))["stint_number"].size()
    one_stop = sorted((num for num, c in counts.items() if num and c == 2), key=_num_key)
    question = f"How many drivers ran a one-stop strategy in the {label}?"
    if not one_stop:
        return [(question, f"No drivers ran a one-stop strategy in the {label}.")]
    seen: set = set()
    labels = [_driver_label(num, info, seen) for num in one_stop]
    n = len(labels)
    word = "driver" if n == 1 else "drivers"
    answer = f"{n} {word} ran a one-stop strategy in the {label}: {', '.join(labels)}."
    return [(question, answer)]


def _qa_safety_car(ts_df: pd.DataFrame, label: str, session: str) -> List[Tuple[str, str]]:
    if _is_empty(ts_df):
        return []
    periods = []
    for _, row in ts_df.iterrows():
        status = _str(row.get("status_label"))
        if not status or status.upper() not in _FULL_SC_LABELS:
            continue
        start = row.get("start_lap")
        end = row.get("end_lap")
        periods.append((None if _isna(start) else int(start), None if _isna(end) else int(end)))
    if not periods:
        return []
    periods.sort(key=lambda t: t[0] if t[0] is not None else -1)
    lap_ranges = []
    for start, end in periods:
        if start is not None and end is not None:
            lap_ranges.append(f"{start}" if start == end else f"{start}-{end}")
        elif start is not None:
            lap_ranges.append(f"{start}")
        else:
            lap_ranges.append("unknown")
    n = len(periods)
    period_word = "period" if n == 1 else "periods"
    verb = "was" if n == 1 else "were"
    question = f"How many safety car periods were there in the {label}, and on which laps?"
    answer = f"There {verb} {n} safety car {period_word} in the {label}, on lap(s) {', '.join(lap_ranges)}."
    return [(question, answer)]


def _qa_weather(weather_df: pd.DataFrame, label: str) -> List[Tuple[str, str]]:
    if _is_empty(weather_df):
        return []
    pairs = []
    rainfall = weather_df.get("rainfall")
    if rainfall is not None and not rainfall.dropna().empty:
        rained = bool(rainfall.fillna(False).astype(bool).any())
        question = f"Was it raining during the {label}?"
        state = "Yes, there was rain" if rained else "No, there was no rain"
        answer = f"{state} recorded during the {label}."
        pairs.append((question, answer))
    track_temp = weather_df.get("track_temp")
    if track_temp is not None:
        valid = track_temp.dropna()
        if not valid.empty:
            tmin, tmax = float(valid.min()), float(valid.max())
            question = f"What was the track temperature range during the {label}?"
            answer = f"Track temperature ranged from {tmin:.1f}°C to {tmax:.1f}°C during the {label}."
            pairs.append((question, answer))
    return pairs


def _qa_gap(
    results_df: pd.DataFrame, info: Dict[str, Dict[str, Optional[str]]], label: str, session: str
) -> List[Tuple[str, str]]:
    if not _is_race_like(session) or _is_empty(results_df):
        return []
    p2_rows = results_df[_position_is(results_df, 2)]
    if p2_rows.empty:
        return []
    row = p2_rows.iloc[0]
    gap = _str(row.get("gap_to_winner"))
    if not gap:
        return []
    num = _str(row.get("driver_number"))
    seen: set = set()
    driver = _driver_label(num, info, seen) if num else "P2"
    question = "What was the gap between the winner and P2?"
    answer = f"{driver} finished P2, {gap} behind the winner of the {label}."
    return [(question, answer)]


def _qa_laps(session_row: Optional[pd.Series], label: str, session: str) -> List[Tuple[str, str]]:
    if not _is_race_like(session) or session_row is None:
        return []
    laps = _val(session_row, "total_laps")
    if laps is None:
        return []
    try:
        n = int(laps)
    except (TypeError, ValueError):
        return []
    question = f"How many laps was the {label}?"
    answer = f"The {label} was {n} laps."
    return [(question, answer)]


def _qa_team_result(
    results_df: pd.DataFrame, info: Dict[str, Dict[str, Optional[str]]], label: str
) -> List[Tuple[str, str]]:
    if _is_empty(results_df):
        return []
    teams: Dict[str, List[pd.Series]] = {}
    for _, row in results_df.iterrows():
        team = _str(row.get("team_name"))
        if not team:
            continue
        teams.setdefault(team, []).append(row)

    pairs = []
    for team in sorted(teams.keys()):
        rows = sorted(
            teams[team],
            key=lambda r: (_isna(r.get("position")), 0 if _isna(r.get("position")) else int(r.get("position"))),
        )
        seen: set = set()
        parts = []
        for row in rows:
            pos = row.get("position")
            num = _str(row.get("driver_number"))
            driver = _driver_label(num, info, seen) if num else (_str(row.get("driver_code")) or "unknown")
            pos_s = f"P{int(pos)}" if not _isna(pos) else "unclassified"
            parts.append(f"{pos_s} ({driver})")
        question = f"Where did the {team} cars finish in the {label}?"
        answer = f"{team}'s cars finished {' and '.join(parts)} in the {label}."
        pairs.append((question, answer))
    return pairs


_QA_TEMPLATES = (
    "winner",
    "podium",
    "pole",
    "fastest_lap",
    "finishing_position",
    "dnf",
    "pit_count",
    "strategy",
    "stop_strategies",
    "safety_car",
    "weather",
    "gap",
    "laps",
    "team_result",
)


def qa_pairs(target: Target, tables: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    """The fixed catalog of Q&A records for one session. Deterministic; never raises."""
    _year, _round_nr, gp_name, session = target
    keys = key_values(target)
    session_key = keys["session_key"]
    label = SESSION_LABELS.get(session, session)
    info = _driver_names(tables)

    results_df = _get(tables, "results")
    laps_df = _get(tables, "laps")
    stints_df = _get(tables, "stints")
    pit_df = _get(tables, "pit_stops")
    ts_df = _get(tables, "track_status")
    weather_df = _get(tables, "weather")
    session_row = _session_row(tables)

    generated: Dict[str, List[Tuple[str, str]]] = {
        "winner": _qa_winner(results_df, info, gp_name, label, session),
        "podium": _qa_podium(results_df, info, gp_name, label, session),
        "pole": _qa_pole(results_df, info, label, session),
        "fastest_lap": _qa_fastest_lap(laps_df, info, gp_name, label),
        "finishing_position": _qa_finishing_position(results_df, info, label),
        "dnf": _qa_dnf(results_df, info, label, session),
        "pit_count": _qa_pit_count(pit_df, info, label, session),
        "strategy": _qa_strategy(results_df, stints_df, info, label, session),
        "stop_strategies": _qa_stop_strategies(stints_df, info, label, session),
        "safety_car": _qa_safety_car(ts_df, label, session),
        "weather": _qa_weather(weather_df, label),
        "gap": _qa_gap(results_df, info, label, session),
        "laps": _qa_laps(session_row, label, session),
        "team_result": _qa_team_result(results_df, info, label),
    }

    records: List[Dict[str, Any]] = []
    for qa_type in _QA_TEMPLATES:
        for idx, (question, answer) in enumerate(generated.get(qa_type, [])):
            records.append(
                {
                    "id": f"{session_key}:{qa_type}:{idx}",
                    "session_key": session_key,
                    "type": qa_type,
                    "question": question,
                    "answer": answer,
                    "messages": [
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": answer},
                    ],
                }
            )
    return records


def write_corpus(target: Target, tables: Dict[str, pd.DataFrame], root: Path, lock: threading.Lock) -> Dict[str, int]:
    """Idempotently (re)write this session's narrative + Q&A records under ``root/corpus``."""
    session_key = key_values(target)["session_key"]
    corpus_dir = root / CORPUS_DIR
    narratives_path = corpus_dir / NARRATIVES_FILENAME
    qa_path = corpus_dir / QA_FILENAME

    rewrite_jsonl_without(narratives_path, session_key, lock)
    rewrite_jsonl_without(qa_path, session_key, lock)

    narratives_written = append_jsonl([narrative_record(target, tables)], narratives_path, lock)
    qa_written = append_jsonl(qa_pairs(target, tables), qa_path, lock)

    return {"narratives": narratives_written, "qa": qa_written}
