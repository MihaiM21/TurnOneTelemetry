"""
Track Evolution (V2, livetiming-backed).

Practice / Qualifying only: tracks how the session's best lap time evolves
over the course of the session (grip ramping up as rubber goes down), overlaid
with track temperature. The headline story is the "running best" step curve —
overall session best plus a handful of individual drivers — read against the
track-temp trend on a secondary axis.

Payload shape:
  * ``overall``      — the whole-session running-best step series:
                       ``[{minute, best_s}, ...]``, monotonically non-increasing.
  * ``drivers``      — ``{tla: [{minute, best_s}, ...]}`` per-driver running-best
                       series for the plotted drivers (overall best + a
                       handful of individuals).
  * ``weather``      — ``[{minute, track_temp}, ...]``; empty list if the
                       WeatherData stream is unavailable (never raises — the
                       plot renders without the temperature overlay instead).

Methodology:
  * "Running best" is a running minimum over completed, valid laps ordered by
    the timestamp they were set (not lap number, since drivers run different
    lap counts). Pit out-laps are skipped since they're never representative
    pace.
  * Session time is expressed in minutes from the first recorded lap
    timestamp, giving a consistent x-axis regardless of session length.
  * Default driver selection (when the caller doesn't request specific TLAs)
    is the overall series plus the 5 drivers with the fastest single lap in
    the session.

Only meaningful for Practice / Qualifying (single-lap pace) sessions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import matplotlib.image as mpimg
import matplotlib.pyplot as plt

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._helpers import parse_f1_time
from src.services.analysis.v2._race_helpers import extract_lap_times
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import colors as colors_module
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme

logger = get_logger(__name__)

DATA_TYPE = "track_evolution"

# Sessions where single-lap pace evolution is meaningful (not races).
_VALID_SESSIONS = {
    "FP1", "FP2", "FP3", "PRACTICE 1", "PRACTICE 2", "PRACTICE 3",
    "Q", "QUALIFYING", "SQ", "SPRINT QUALIFYING", "SPRINT SHOOTOUT",
}

# Default number of individual drivers shown alongside the overall series.
_DEFAULT_DRIVER_COUNT = 5


def _init(y: int, event_name: str, session_name: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f"Track evolution {y} {event_name} {session_name}.png"
    return location, name


def _assert_valid_session(session_name: str, year: int, identifier: Any) -> None:
    if session_name.strip().upper() not in _VALID_SESSIONS:
        raise DataNotAvailableError(
            year=year, gp=identifier, session=session_name,
            source="livetiming",
            reason=(
                "Track evolution is only available for Practice/Qualifying sessions "
                f"(got {session_name!r})"
            ),
        )


# ----------------------------------------------------------------------
# Pure payload builders (unit-testable without a live store)
# ----------------------------------------------------------------------
def _running_best(
    records: List[Dict[str, Any]], t0: float
) -> List[Dict[str, Any]]:
    """Running-minimum lap time over time, skipping pit-out / invalid laps.

    ``records`` is a single driver's (or the merged field's) lap-time records
    as produced by ``extract_lap_times`` — ``{lap, time_s, timestamp_s,
    pit_in, pit_out}``. Returns points ordered by timestamp with a
    monotonically non-increasing ``best_s``.
    """
    ordered = sorted(
        (r for r in records if not r.get("pit_out")),
        key=lambda r: r.get("timestamp_s") if r.get("timestamp_s") is not None else 0.0,
    )
    series: List[Dict[str, Any]] = []
    best: Optional[float] = None
    for rec in ordered:
        t = rec.get("time_s")
        if not t or t <= 0:
            continue
        if best is None or t < best:
            best = t
        ts = rec.get("timestamp_s")
        minute = ((ts - t0) / 60.0) if ts is not None else 0.0
        series.append({"minute": round(minute, 3), "best_s": round(best, 3)})
    return series


def _fastest_lap_by_driver(
    lap_times: Dict[str, List[Dict[str, Any]]]
) -> Dict[str, float]:
    """``{car_num: fastest_valid_lap_s}``."""
    result: Dict[str, float] = {}
    for num, records in lap_times.items():
        valid = [r["time_s"] for r in records if r.get("time_s") and r["time_s"] > 0]
        if valid:
            result[num] = min(valid)
    return result


def _session_start_ts(lap_times: Dict[str, List[Dict[str, Any]]]) -> float:
    """Earliest lap-completion timestamp across all drivers (session zero)."""
    all_ts = [
        rec["timestamp_s"]
        for records in lap_times.values()
        for rec in records
        if rec.get("timestamp_s") is not None
    ]
    return min(all_ts) if all_ts else 0.0


def _weather_series(entries: List[Dict[str, Any]], t0: float) -> List[Dict[str, Any]]:
    """Parse ``WeatherData`` entries into ``[{minute, track_temp}, ...]``.

    Never raises: bad/missing values are dropped, and an empty/unavailable
    stream simply yields an empty list (the plot renders without the overlay).
    """
    series: List[Dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        ts_raw = entry.get("_timestamp", entry.get("T", ""))
        ts = parse_f1_time(ts_raw)
        track_temp_raw = entry.get("TrackTemp")
        try:
            track_temp = float(track_temp_raw)
        except (TypeError, ValueError):
            continue
        minute = (ts - t0) / 60.0
        series.append({"minute": round(minute, 3), "track_temp": track_temp})
    series.sort(key=lambda r: r["minute"])
    return series


def build_payload_from_parts(
    lap_times: Dict[str, List[Dict[str, Any]]],
    drivers: Dict[str, Dict[str, Any]],
    weather_entries: List[Dict[str, Any]],
    requested_tlas: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Assemble the full payload from already-extracted parts (test seam)."""
    t0 = _session_start_ts(lap_times)

    # Overall series: merge every driver's completed-lap records into one
    # timeline and take the running minimum.
    merged_records: List[Dict[str, Any]] = []
    for records in lap_times.values():
        merged_records.extend(records)
    overall = _running_best(merged_records, t0)

    fastest_by_num = _fastest_lap_by_driver(lap_times)
    tla_by_num = {num: info.get("tla", num) for num, info in drivers.items()}
    num_by_tla = {tla: num for num, tla in tla_by_num.items()}

    if requested_tlas:
        selected_nums = [num_by_tla[t] for t in requested_tlas if t in num_by_tla]
    else:
        ranked = sorted(fastest_by_num.items(), key=lambda kv: kv[1])
        selected_nums = [num for num, _ in ranked[:_DEFAULT_DRIVER_COUNT]]

    driver_series: Dict[str, List[Dict[str, Any]]] = {}
    for num in selected_nums:
        tla = tla_by_num.get(num, num)
        driver_series[tla] = _running_best(lap_times.get(num, []), t0)

    weather = _weather_series(weather_entries, t0)

    return {
        "overall": overall,
        "drivers": driver_series,
        "weather": weather,
    }


def _build_payload(
    store: SessionDataStore, requested_tlas: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Build the track-evolution payload from a resolved store."""
    drivers = store.driver_list()
    lap_times = extract_lap_times(store)
    try:
        weather_entries = store.weather_data()
    except Exception as exc:
        logger.debug("Track evolution: weather stream unavailable: %s", exc)
        weather_entries = []
    return build_payload_from_parts(lap_times, drivers, weather_entries, requested_tlas)


def _parse_drivers_param(drivers: Optional[str]) -> Optional[List[str]]:
    if not drivers:
        return None
    return [d.strip().upper() for d in drivers.split(",") if d.strip()]


class TrackEvolutionData:
    """Callable: ``TrackEvolutionData()(year, identifier, session, drivers=None) -> dict``."""

    def __call__(
        self,
        y: int,
        identifier: Union[int, str],
        e: str,
        drivers: Optional[str] = None,
    ) -> Dict[str, Any]:
        _assert_valid_session(e, y, identifier)
        requested_tlas = _parse_drivers_param(drivers)

        # Custom driver selections aren't cached under the base key since the
        # payload shape depends on the request; only the default (no filter)
        # selection goes through cached_or_generate.
        if requested_tlas:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store, requested_tlas)

        def _generate() -> Dict[str, Any]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store, None)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=DATA_TYPE, generator=_generate, version="v2",
        )


class TrackEvolutionPlot:
    """Callable: ``TrackEvolutionPlot()(year, identifier, session, drivers=None) -> png path``."""

    def __call__(
        self,
        y: int,
        identifier: Union[int, str],
        e: str,
        drivers: Optional[str] = None,
    ) -> str:
        _assert_valid_session(e, y, identifier)

        payload = TrackEvolutionData()(y, identifier, e, drivers)
        if not payload.get("overall"):
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No lap-time data available to plot",
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

        overall = payload["overall"]
        driver_series = payload.get("drivers", {})
        weather = payload.get("weather", [])

        fig, ax = plt.subplots(figsize=(14, 8), layout='constrained')

        # Per-driver team-colored step lines (thin, background layer).
        for tla, series in driver_series.items():
            if not series:
                continue
            minutes = [p["minute"] for p in series]
            bests = [p["best_s"] for p in series]
            color = "#888888"
            try:
                color = colors_module.get_driver_color(tla)
            except Exception:
                color = "#888888"
            if color == "#FFFFFF":
                color = "#888888"
            ax.step(
                minutes, bests, where="post", color=color, linewidth=1.6,
                alpha=0.85, label=tla, zorder=3,
            )
            ax.text(
                minutes[-1], bests[-1], f" {tla}", color=color,
                fontsize=8, va="center", zorder=4,
            )

        # Overall session-best step line: thick white with glow.
        if overall:
            minutes = [p["minute"] for p in overall]
            bests = [p["best_s"] for p in overall]
            ax.step(
                minutes, bests, where="post", color="#FFFFFF", linewidth=3.2,
                label="Session best", zorder=5,
            )
            setup_theme.add_glow(ax, linewidth=8, alpha=0.25, passes=3)

        ax.set_xlabel("Session time (minutes)")
        ax.set_ylabel("Best lap time so far (s)")
        ax.set_title("Track evolution: best lap time vs session time")
        ax.legend(loc="upper right", fontsize=8, ncol=2)

        # Track temperature overlay on a secondary axis.
        if weather:
            ax2 = ax.twinx()
            w_minutes = [w["minute"] for w in weather]
            w_temp = [w["track_temp"] for w in weather]
            ax2.plot(
                w_minutes, w_temp, color="#FF8C00", linestyle="--",
                linewidth=1.8, label="Track temp (C)", zorder=2,
            )
            ax2.fill_between(w_minutes, w_temp, color="#FF8C00", alpha=0.12, zorder=1)
            ax2.set_ylabel("Track temp (C)", color="#FF8C00")
            ax2.tick_params(axis="y", colors="#FF8C00")
            ax2.grid(False)

        try:
            logo = mpimg.imread('assets/images/logo mic.png')
            fig.figimage(logo, 575, 575, zorder=3, alpha=.5)
        except Exception:
            pass

        plt.suptitle(f"Track evolution\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"


if __name__ == "__main__":
    print("Testing V2 Track Evolution...")
    try:
        data = TrackEvolutionData()(2025, 1, "Q")
        print(f"Overall points: {len(data['overall'])}")
        print(f"Drivers: {list(data['drivers'].keys())}")
        print(f"Weather points: {len(data['weather'])}")
        plot_path = TrackEvolutionPlot()(2025, 1, "Q")
        print(f"Plot: {plot_path}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
