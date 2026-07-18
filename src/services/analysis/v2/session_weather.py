"""
Weather & Session Timeline (V2, livetiming-backed).

Combines the ``WeatherData`` stream, track-status periods and race-control
messages into a single timeline payload/plot. Unlike ``position_changes.py``
and ``race_gaps.py`` this feature is meaningful for *every* session type
(Race, Sprint, Qualifying, Sprint Qualifying, FP1-3) — weather is recorded
throughout every on-track session, not just wheel-to-wheel ones. There is no
``_VALID_SESSIONS`` restriction here on purpose.

Lap numbers are only meaningful for Race/Sprint sessions (there's a single,
well-ordered lap counter). For Q/SQ/FP sessions, each car runs its own lap
count independently, so ``lap`` is left ``null`` in the weather series for
those session types; the x-axis for the plot uses session time (minutes)
uniformly across all session types so the same rendering code works for R/S
and Q/FP alike.

Both the Data and Plot entry points route through ``cached_or_generate`` so
the payload built once is reused by the plot (Mongo/Redis serves both).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import matplotlib.image as mpimg
import matplotlib.pyplot as plt

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._helpers import parse_f1_time
from src.services.analysis.v2._race_helpers import (
    _leader_lap_timestamps,
    _time_to_lap,
    get_track_status_periods,
)
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme

logger = get_logger(__name__)

DATA_TYPE = "session_weather"

# Session types where a single, session-wide lap counter makes "lap" a
# meaningful field on the weather series.
_LAP_MAPPABLE_SESSIONS = {"R", "RACE", "S", "SPRINT"}

# Race-control message keywords worth annotating on the timeline strip. Kept
# short and case-insensitive; capped at ~12 annotations at render time to
# avoid clutter.
_INCIDENT_KEYWORDS = ("INVESTIGATION", "PENALTY", "SAFETY CAR", "RED FLAG")

_STATUS_COLORS = {
    "GREEN": "#2ECC71",
    "YELLOW": "#FFD700",
    "SC": "#FF8C00",
    "VSC": "#FFA500",
    "RED": "#FF3B3B",
}


def _init(y: int, event_name: str, session_name: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f"Session weather {y} {event_name} {session_name}.png"
    return location, name


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_weather_series(
    store: SessionDataStore, session_name: str
) -> List[Dict[str, Any]]:
    """Parse ``WeatherData.jsonStream`` into the payload's ``weather`` list."""
    entries = store.weather_data()
    if not entries:
        raise DataNotAvailableError(
            year=store.year, gp=store.identifier, session=session_name,
            source="livetiming", reason="WeatherData stream is empty",
        )

    lap_mappable = session_name.strip().upper() in _LAP_MAPPABLE_SESSIONS
    leader_ts = _leader_lap_timestamps(store) if lap_mappable else []

    series: List[Dict[str, Any]] = []
    for entry in entries:
        ts_raw = entry.get("_timestamp", entry.get("T", ""))
        time_s = parse_f1_time(ts_raw)

        lap: Optional[int] = None
        if lap_mappable and leader_ts:
            lap = _time_to_lap(time_s, leader_ts)

        rainfall_raw = entry.get("Rainfall")
        rainfall = 1 if str(rainfall_raw).strip() in ("1", "1.0", "True", "true") else 0

        series.append({
            "time_s": time_s,
            "lap": lap,
            "air_temp": _to_float(entry.get("AirTemp")),
            "track_temp": _to_float(entry.get("TrackTemp")),
            "humidity": _to_float(entry.get("Humidity")),
            "rainfall": rainfall,
            "wind_speed": _to_float(entry.get("WindSpeed")),
            "wind_dir": _to_float(entry.get("WindDirection")),
        })

    series.sort(key=lambda r: r["time_s"])
    return series


def _build_race_control(store: SessionDataStore) -> List[Dict[str, Any]]:
    """Parse ``RaceControlMessages.json`` into the payload's ``race_control`` list."""
    messages = store.race_control()

    result: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        utc = msg.get("Utc", "")
        # RaceControlMessages timestamps are absolute UTC ISO strings, not
        # session-relative HH:MM:SS.mmm like other streams. There is no
        # reliable session-start anchor available here without an extra
        # fetch, so time_s reports the raw UTC-derived seconds-of-day via
        # parse_f1_time on the time component when present, else None.
        time_s = None
        if isinstance(utc, str) and "T" in utc:
            time_part = utc.split("T", 1)[1]
            time_s = parse_f1_time(time_part)
        elif isinstance(utc, str):
            time_s = parse_f1_time(utc)

        result.append({
            "time_s": time_s,
            "lap": _to_int(msg.get("Lap")),
            "category": msg.get("Category"),
            "flag": msg.get("Flag"),
            "message": msg.get("Message", ""),
        })

    result.sort(key=lambda r: (r["time_s"] if r["time_s"] is not None else 0.0))
    return result


def _build_payload(store: SessionDataStore, session_name: str) -> Dict[str, Any]:
    weather = _build_weather_series(store, session_name)
    periods = get_track_status_periods(store)
    race_control = _build_race_control(store)
    return {
        "weather": weather,
        "track_status_periods": periods,
        "race_control": race_control,
    }


class SessionWeatherData:
    """Callable: ``SessionWeatherData()(year, identifier, session) -> dict``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> Dict[str, Any]:
        def _generate() -> Dict[str, Any]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store, e)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=DATA_TYPE, generator=_generate, version="v2",
        )


class SessionWeatherPlot:
    """Callable: ``SessionWeatherPlot()(year, identifier, session) -> png path``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> str:
        payload = SessionWeatherData()(y, identifier, e)
        weather = payload.get("weather") or []
        if not weather:
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No weather data available to plot",
            )

        store = SessionDataStore(y, identifier, e)
        event_name = store.event_name

        return self._render(payload, y, event_name, e)

    @staticmethod
    def _render(
        payload: Dict[str, Any],
        y: int,
        event_name: str,
        e: str,
    ) -> str:
        setup_theme.setup_turnone_theme()
        location, name = _init(y, event_name, e)

        weather = payload["weather"]
        periods = payload.get("track_status_periods") or []
        race_control = payload.get("race_control") or []

        # Session time in minutes is the shared x-axis: it's universal across
        # every session type (R/S/Q/SQ/FP1-3), unlike "lap" which only exists
        # for R/S.
        times_min = [w["time_s"] / 60.0 for w in weather]
        air_temp = [w["air_temp"] for w in weather]
        track_temp = [w["track_temp"] for w in weather]
        humidity = [w["humidity"] for w in weather]
        rainfall = [w["rainfall"] for w in weather]

        fig, axes = plt.subplots(
            3, 1, figsize=(14, 10), sharex=True, layout='constrained',
            gridspec_kw={"height_ratios": [3, 2, 1]},
        )
        ax_temp, ax_hum, ax_status = axes

        # Panel 1: air + track temperature.
        ax_temp.plot(times_min, air_temp, color="#4FC3F7", label="Air Temp (C)")
        ax_temp.plot(times_min, track_temp, color="#FF6F61", label="Track Temp (C)")
        ax_temp.set_ylabel("Temp (C)")
        ax_temp.legend(loc="upper right")
        ax_temp.set_title("Temperature")

        # Panel 2: humidity + rainfall indicator.
        ax_hum.plot(times_min, humidity, color="#B39DDB", label="Humidity (%)")
        ax_hum.fill_between(
            times_min, 0, 1,
            where=[bool(r) for r in rainfall],
            transform=ax_hum.get_xaxis_transform(),
            color="#2196F3", alpha=0.25, label="Rainfall", step="mid",
        )
        ax_hum.set_ylabel("Humidity (%)")
        ax_hum.legend(loc="upper right")
        ax_hum.set_title("Humidity / Rainfall")

        # Panel 3: track-status timeline strip.
        ax_status.set_ylim(0, 1)
        ax_status.set_yticks([])
        ax_status.set_xlabel("Session time (minutes)")
        ax_status.set_title("Track Status")

        x_max = times_min[-1] if times_min else 1.0
        seen_labels = set()
        for period in periods:
            status = period.get("status")
            color = _STATUS_COLORS.get(status, "#777777")
            start_time_s = period.get("start_time_s")
            end_time_s = period.get("end_time_s")
            if start_time_s is None:
                continue
            start_min = start_time_s / 60.0
            end_min = (end_time_s / 60.0) if end_time_s is not None else x_max
            if end_min <= start_min:
                end_min = start_min + 0.1
            label = status if status not in seen_labels else None
            seen_labels.add(status)
            ax_status.axvspan(start_min, end_min, color=color, alpha=0.7, label=label)

        ax_status.legend(loc="upper right", ncol=len(seen_labels) or 1, fontsize=8)

        # Sparse race-control incident annotations: filter to notable keywords,
        # cap at 12 to avoid clutter, stagger vertical offsets.
        incidents = [
            rc for rc in race_control
            if rc.get("message") and any(kw in rc["message"].upper() for kw in _INCIDENT_KEYWORDS)
            and rc.get("time_s") is not None
        ]
        incidents = incidents[:12]
        for idx, rc in enumerate(incidents):
            # race_control time_s is derived from absolute UTC and is not on
            # the same axis origin as the session-relative weather/status
            # timestamps; without a reliable session-start anchor we can only
            # place these at a relative position proportional to their order,
            # which keeps them visible without implying false precision.
            frac = (idx + 1) / (len(incidents) + 1)
            x_pos = frac * x_max
            y_offset = 0.9 if idx % 2 == 0 else 0.5
            ax_status.annotate(
                rc["message"][:40],
                xy=(x_pos, 0.5), xytext=(x_pos, y_offset),
                fontsize=6, rotation=45, ha="left", va="bottom",
                color="#E9E9E9",
                arrowprops=dict(arrowstyle="-", color="#555555", lw=0.5),
            )

        try:
            logo = mpimg.imread('assets/images/logo mic.png')
            fig.figimage(logo, 575, 575, zorder=3, alpha=.6)
        except Exception:
            pass

        plt.suptitle(f"Weather & Session Timeline\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"


if __name__ == "__main__":
    print("Testing V2 Session Weather...")
    try:
        data = SessionWeatherData()(2025, 1, "R")
        print(f"Weather samples: {len(data.get('weather', []))}")
        print(f"Track status periods: {len(data.get('track_status_periods', []))}")
        print(f"Race control messages: {len(data.get('race_control', []))}")
        plot_path = SessionWeatherPlot()(2025, 1, "R")
        print(f"Plot: {plot_path}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
