"""
Tyre Degradation (V2, livetiming-backed).

Per-compound tyre degradation: for a race/sprint session, join each driver's
completed lap times with the compound + tyre-age of the stint that lap belongs
to, filter out laps that don't reflect clean green-flag pace, then linearly fit
lap-time against tyre-age per compound to get a degradation rate (s/lap).

The join uses:
  * ``extract_lap_times`` (``_race_helpers``) — per-driver completed laps with
    ``lap``, ``time_s``, ``pit_in``/``pit_out`` flags.
  * ``extract_stints_from_data`` (``_helpers``) — per-driver stints with
    ``compound``, race-lap ``start_lap``/``end_lap`` and ``tyre_life_end``.
    ``StartLaps`` in the raw stream is tyre *age*, so tyre_age for a race lap L
    inside a stint = (L - start_lap) + start_age, where start_age is derived
    from ``tyre_life_end`` (age at the stint's last lap) working backwards.

Filtering (a filtered lap is excluded from BOTH the plotted points and the fit):
  * in-laps / out-laps (``pit_in`` / ``pit_out``),
  * laps inside SC / VSC / RED / YELLOW track-status periods,
  * laps slower than 107% of that driver's median clean lap (traffic / errors).

Fuel correction (optional): a car gets lighter through the race, so raw lap
times drop even as the tyre degrades. ``fuel_corrected_s`` subtracts an
estimated fuel effect proportional to the laps of fuel still on board.

Both the Data and Plot entry points route through ``cached_or_generate``.
Race / Sprint sessions only.
"""
from __future__ import annotations

from statistics import median
from typing import Any, Dict, List, Optional, Union

import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import numpy as np

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._helpers import extract_stints_from_data
from src.services.analysis.v2._race_helpers import (
    extract_lap_times,
    get_track_status_periods,
)
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.services.plotting.colors import compound_colors, get_compound_color

logger = get_logger(__name__)

DATA_TYPE = "tyre_degradation"

# Sessions where lap-by-lap degradation is meaningful.
_VALID_SESSIONS = {"R", "RACE", "S", "SPRINT"}

# Fuel-burn effect: a car is ~0.055 s/lap slower per lap of fuel it still
# carries. Subtracting fuel_effect * laps_remaining isolates tyre pace.
FUEL_EFFECT_S_PER_LAP = 0.055

# Track-status labels whose laps are excluded from the analysis.
_NON_GREEN_STATUSES = {"SC", "VSC", "RED", "YELLOW"}

# 107% traffic/outlier filter and minimum clean laps for a stint to be fit.
_SLOW_LAP_FACTOR = 1.07
_MIN_STINT_LAPS_FOR_FIT = 4


def _init(y: int, event_name: str, session_name: str, driver: Optional[str],
          fuel_corrected: bool):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    suffix = ""
    if driver:
        suffix += f" {driver}"
    if fuel_corrected:
        suffix += " fuel-corrected"
    name = f"Tyre degradation{suffix} {y} {event_name} {session_name}.png"
    return location, name


def _data_type(driver: Optional[str], fuel_corrected: bool) -> str:
    dt = DATA_TYPE
    if driver:
        dt += f"_{driver.upper()}"
    if fuel_corrected:
        dt += "_fuel_corrected"
    return dt


def _assert_valid_session(session_name: str, year: int, identifier: Any) -> None:
    if session_name.strip().upper() not in _VALID_SESSIONS:
        raise DataNotAvailableError(
            year=year, gp=identifier, session=session_name,
            source="livetiming",
            reason=(
                "Tyre degradation is only available for Race/Sprint sessions "
                f"(got {session_name!r})"
            ),
        )


def _resolve_driver_num(
    drivers: Dict[str, Dict[str, Any]], driver_tla: str,
    year: int, identifier: Any, session: str,
) -> str:
    """Map a TLA filter to a car number; raise listing valid TLAs on miss."""
    wanted = driver_tla.strip().upper()
    for num, info in drivers.items():
        if str(info.get("tla", "")).upper() == wanted:
            return num
    valid = sorted({str(info.get("tla", "")) for info in drivers.values() if info.get("tla")})
    raise DataNotAvailableError(
        year=year, gp=identifier, session=session, source="livetiming",
        reason=f"Unknown driver {driver_tla!r}. Valid drivers: {', '.join(valid)}",
    )


def _non_green_laps(periods: List[Dict[str, Any]]) -> set:
    """Set of race-lap numbers that fall inside SC/VSC/RED/YELLOW periods."""
    laps: set = set()
    for period in periods:
        if period.get("status") not in _NON_GREEN_STATUSES:
            continue
        start = period.get("start_lap")
        if start is None:
            continue
        end = period.get("end_lap")
        if end is None:
            # Active to the end of the session; bound generously.
            end = start + 200
        for lap in range(int(start), int(end) + 1):
            laps.add(lap)
    return laps


def _lap_to_stint(stints: List[Dict[str, Any]], lap: int) -> Optional[Dict[str, Any]]:
    for stint in stints:
        if stint["start_lap"] <= lap <= stint["end_lap"]:
            return stint
    return None


def _tyre_age(stint: Dict[str, Any], lap: int) -> int:
    """Tyre age (laps on the tyre) at completion of race ``lap`` in ``stint``.

    ``tyre_life_end`` is the age at the stint's final lap (``end_lap``); walk
    back one age-year per race-lap to the requested lap. Starting a stint on
    a used set therefore yields a non-zero age on the stint's first lap.
    """
    return int(stint["tyre_life_end"]) - (int(stint["end_lap"]) - int(lap))


def _collect_driver_points(
    lap_records: List[Dict[str, Any]],
    stints: List[Dict[str, Any]],
    non_green: set,
    tla: str,
) -> List[Dict[str, Any]]:
    """Clean, stint-joined lap points for one driver (post SC/pit/107% filter)."""
    # First pass: valid laps with a compound + tyre age, excluding pit laps and
    # SC/VSC/etc. laps.
    candidates: List[Dict[str, Any]] = []
    for rec in lap_records:
        lap = rec["lap"]
        time_s = rec["time_s"]
        if time_s is None or time_s <= 0:
            continue
        if rec.get("pit_in") or rec.get("pit_out"):
            continue
        if lap in non_green:
            continue
        stint = _lap_to_stint(stints, lap)
        if stint is None:
            continue
        candidates.append({
            "lap": lap,
            "driver": tla,
            "tyre_age": _tyre_age(stint, lap),
            "lap_time_s": round(float(time_s), 3),
            "compound": stint["compound"],
            "stint_number": stint["stint_number"],
        })

    if not candidates:
        return []

    # 107% traffic filter relative to this driver's median clean lap.
    med = median(c["lap_time_s"] for c in candidates)
    threshold = med * _SLOW_LAP_FACTOR
    return [c for c in candidates if c["lap_time_s"] <= threshold]


def _fit_compound(points: List[Dict[str, Any]]):
    """Linear fit lap_time vs tyre_age -> (deg_rate, r_squared).

    Only laps from stints with >= _MIN_STINT_LAPS_FOR_FIT clean laps feed the
    fit; all points are still returned for plotting. Returns ``(None, None)``
    when a fit can't be computed (too few points / degenerate x).
    """
    per_stint: Dict[Any, int] = {}
    for p in points:
        per_stint[p.get("stint_key")] = per_stint.get(p.get("stint_key"), 0) + 1
    eligible = [p for p in points if per_stint.get(p.get("stint_key"), 0) >= _MIN_STINT_LAPS_FOR_FIT]

    if len(eligible) < 2:
        return None, None

    x = np.array([p["tyre_age"] for p in eligible], dtype=float)
    y = np.array([p["_fit_y"] for p in eligible], dtype=float)
    if np.all(x == x[0]):
        return None, None

    slope, intercept = np.polyfit(x, y, 1)
    y_hat = slope * x + intercept
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    return round(float(slope), 4), (round(r_squared, 4) if r_squared is not None else None)


def _build_payload(
    store: SessionDataStore,
    fuel_corrected: bool,
    driver_filter: Optional[str],
    year: int,
    identifier: Any,
    session: str,
) -> List[Dict[str, Any]]:
    lap_times = extract_lap_times(store)
    timing_app = store.timing_app_data()
    drivers = store.driver_list()
    periods = get_track_status_periods(store)
    non_green = _non_green_laps(periods)

    driver_num_filter: Optional[str] = None
    if driver_filter:
        driver_num_filter = _resolve_driver_num(
            drivers, driver_filter, year, identifier, session
        )

    # Total race laps (max completed lap across all drivers) for fuel modelling.
    total_laps = max(
        (rec["lap"] for recs in lap_times.values() for rec in recs), default=0
    )

    # compound -> list of point dicts (with private fit helper keys).
    by_compound: Dict[str, List[Dict[str, Any]]] = {}

    for num, recs in lap_times.items():
        if driver_num_filter is not None and num != driver_num_filter:
            continue
        stints = extract_stints_from_data(timing_app, num)
        if not stints:
            continue
        info = drivers.get(num, {})
        tla = info.get("tla", num)

        points = _collect_driver_points(recs, stints, non_green, tla)
        for p in points:
            compound = p["compound"] if p["compound"] in compound_colors else "UNKNOWN"
            laps_remaining = max(0, total_laps - p["lap"])
            if fuel_corrected:
                fc = round(p["lap_time_s"] - FUEL_EFFECT_S_PER_LAP * laps_remaining, 3)
            else:
                fc = None
            entry = {
                "driver": p["driver"],
                "tyre_age": p["tyre_age"],
                "lap_time_s": p["lap_time_s"],
                "fuel_corrected_s": fc,
                # Private keys (stripped before returning) for the per-compound fit.
                "stint_key": (num, p["stint_number"]),
                "_fit_y": fc if fuel_corrected else p["lap_time_s"],
            }
            by_compound.setdefault(compound, []).append(entry)

    payload: List[Dict[str, Any]] = []
    # Stable, readable compound ordering.
    order = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET", "UNKNOWN"]
    for compound in sorted(by_compound, key=lambda c: order.index(c) if c in order else 99):
        raw_points = by_compound[compound]
        deg_rate, r_squared = _fit_compound(raw_points)
        clean_points = [
            {
                "driver": p["driver"],
                "tyre_age": p["tyre_age"],
                "lap_time_s": p["lap_time_s"],
                "fuel_corrected_s": p["fuel_corrected_s"],
            }
            for p in raw_points
        ]
        payload.append({
            "compound": compound,
            "color": get_compound_color(compound),
            "points": clean_points,
            "deg_rate_s_per_lap": deg_rate,
            "r_squared": r_squared,
            "n_points": len(clean_points),
        })
    return payload


class TyreDegradationData:
    """Callable: ``TyreDegradationData()(year, identifier, session, ...) -> list``."""

    def __call__(
        self,
        y: int,
        identifier: Union[int, str],
        e: str,
        driver: Optional[str] = None,
        fuel_corrected: bool = False,
    ) -> List[Dict[str, Any]]:
        _assert_valid_session(e, y, identifier)

        def _generate() -> List[Dict[str, Any]]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store, fuel_corrected, driver, y, identifier, e)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=_data_type(driver, fuel_corrected),
            generator=_generate, version="v2",
        )


class TyreDegradationPlot:
    """Callable: ``TyreDegradationPlot()(year, identifier, session, ...) -> png``."""

    def __call__(
        self,
        y: int,
        identifier: Union[int, str],
        e: str,
        driver: Optional[str] = None,
        fuel_corrected: bool = False,
    ) -> str:
        _assert_valid_session(e, y, identifier)

        payload = TyreDegradationData()(y, identifier, e, driver, fuel_corrected)
        if not payload or all(not c["points"] for c in payload):
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No clean tyre-degradation data available to plot",
            )

        store = SessionDataStore(y, identifier, e)
        event_name = store.event_name
        return self._render(payload, y, event_name, e, driver, fuel_corrected)

    @staticmethod
    def _render(
        payload: List[Dict[str, Any]],
        y: int,
        event_name: str,
        e: str,
        driver: Optional[str],
        fuel_corrected: bool,
    ) -> str:
        setup_theme.setup_turnone_theme()
        location, name = _init(y, event_name, e, driver, fuel_corrected)

        y_key = "fuel_corrected_s" if fuel_corrected else "lap_time_s"

        fig, ax = plt.subplots(figsize=(13, 8), layout='constrained')

        for comp in payload:
            pts = comp["points"]
            if not pts:
                continue
            color = comp["color"]
            xs = [p["tyre_age"] for p in pts]
            ys = [
                p[y_key] if p[y_key] is not None else p["lap_time_s"]
                for p in pts
            ]

            deg = comp["deg_rate_s_per_lap"]
            r2 = comp["r_squared"]
            if deg is not None:
                sign = "+" if deg >= 0 else "-"
                r2_txt = f", R²={r2:.2f}" if r2 is not None else ""
                label = f"{comp['compound']}: {sign}{abs(deg):.3f} s/lap{r2_txt}"
            else:
                label = f"{comp['compound']}: n/a (n={comp['n_points']})"

            ax.scatter(xs, ys, color=color, alpha=0.7, s=28,
                       edgecolors='#0d0d0d', linewidths=0.4, label=label, zorder=3)

            # Trendline across the observed tyre-age span.
            if deg is not None and xs:
                x_line = np.array([min(xs), max(xs)], dtype=float)
                # Recover intercept from the fit-eligible points would require
                # re-fitting; instead fit the drawn points for a visual line.
                xa = np.array(xs, dtype=float)
                ya = np.array(ys, dtype=float)
                if not np.all(xa == xa[0]):
                    slope, intercept = np.polyfit(xa, ya, 1)
                    ax.plot(x_line, slope * x_line + intercept,
                            color=color, linestyle='--', linewidth=2, zorder=2)

        ax.set_xlabel("Tyre age (laps)", fontsize=12)
        ax.set_ylabel("Lap time (s)", fontsize=12)
        ax.legend(loc='upper left', frameon=True, fontsize=10)

        try:
            logo = mpimg.imread('assets/images/logo mic.png')
            fig.figimage(logo, 30, 30, zorder=3, alpha=0.30)
        except Exception:
            pass

        title = "Tyre degradation"
        if driver:
            title += f" — {driver.upper()}"
        if fuel_corrected:
            title += " (fuel-corrected)"
        plt.suptitle(f"{title}\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"


if __name__ == "__main__":
    print("Testing V2 Tyre Degradation...")
    try:
        data = TyreDegradationData()(2025, 1, "R")
        print(f"Compounds: {len(data)}")
        for comp in data:
            print(f"  {comp['compound']}: deg={comp['deg_rate_s_per_lap']} "
                  f"R2={comp['r_squared']} n={comp['n_points']}")
        plot_path = TyreDegradationPlot()(2025, 1, "R")
        print(f"Plot: {plot_path}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
