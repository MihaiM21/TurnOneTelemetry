"""
Race Gaps / Race Trace (V2, livetiming-backed).

Two related race views, both built from per-driver cumulative lap times
(``_race_helpers.extract_lap_times``) so they are robust across seasons and do
not depend on parsing livetiming ``GapToLeader`` strings:

* ``reference="leader"`` — classic **race gaps**: for each lap, a driver's gap
  is their cumulative race time minus the per-lap leader's cumulative time
  (the minimum cumulative time over all drivers at that lap). Gaps are always
  ``>= 0``; the leader sits at ``0``.

* ``reference="average"`` — **race trace**: a driver's value is
  ``mean_reference_time * lap - driver_cumtime(lap)``. The reference pace is the
  **race winner's average lap time** (the driver with the smallest final
  cumulative time, over the number of laps they completed); if that cannot be
  determined it falls back to the field median of all completed lap times.
  Positive means ahead of the constant-pace reference, negative means behind.
  This flattens the leader's growing lead into a roughly horizontal line and
  makes relative pace swings legible.

Both entry points route through ``cached_or_generate`` so the payload built
once is reused by the plot (Redis/Mongo serves both). Retirements simply end a
driver's series (``extract_lap_times`` stops emitting laps). Red-flag laps get a
``null`` gap inserted so the plotted line breaks across the stoppage. Lapped
cars need no special handling: cumulative-time math grows their gap beyond a lap
time naturally.
"""
from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Sequence, Union

import matplotlib.image as mpimg
import matplotlib.pyplot as plt

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._race_helpers import (
    extract_lap_times,
    get_track_status_periods,
)
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme

logger = get_logger(__name__)

DATA_TYPE_LEADER = "race_gaps_leader"
DATA_TYPE_AVERAGE = "race_gaps_average"

# Sessions where a lap-by-lap race trace is meaningful.
_VALID_SESSIONS = {"R", "RACE", "S", "SPRINT"}


def _init(y: int, event_name: str, session_name: str, reference: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f"Race gaps {reference} {y} {event_name} {session_name}.png"
    return location, name


def _assert_valid_session(session_name: str, year: int, identifier: Any) -> None:
    if session_name.strip().upper() not in _VALID_SESSIONS:
        raise DataNotAvailableError(
            year=year, gp=identifier, session=session_name,
            source="livetiming",
            reason=(
                "Race gaps are only available for Race/Sprint sessions "
                f"(got {session_name!r})"
            ),
        )


def _normalize_drivers(drivers: Union[str, Sequence[str], None]) -> Optional[List[str]]:
    """Turn a comma-string or list of TLAs into an upper-cased list (or None)."""
    if drivers is None:
        return None
    if isinstance(drivers, str):
        parts = [p.strip() for p in drivers.split(",")]
    else:
        parts = [str(p).strip() for p in drivers]
    parts = [p.upper() for p in parts if p]
    return parts or None


def _cumulative_times(lap_times: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[int, float]]:
    """Per-driver ``{lap: cumulative_race_time_s}`` from completed-lap records.

    A driver's cumulative time at lap ``n`` is the sum of their lap times for
    laps ``1..n``. Only laps with a positive parsed time contribute; laps are
    processed in order so a missing intermediate lap (rare) does not silently
    shift later cumulative sums onto the wrong lap number.
    """
    cum: Dict[str, Dict[int, float]] = {}
    for num, records in lap_times.items():
        running = 0.0
        per_lap: Dict[int, float] = {}
        for rec in records:
            time_s = rec.get("time_s")
            if not time_s or time_s <= 0:
                continue
            running += float(time_s)
            per_lap[int(rec["lap"])] = running
        if per_lap:
            cum[num] = per_lap
    return cum


def _red_flag_laps(periods: List[Dict[str, Any]]) -> set:
    """Set of lap numbers covered by a RED track-status period."""
    reds: set = set()
    for period in periods:
        if period.get("status") != "RED":
            continue
        start = period.get("start_lap")
        end = period.get("end_lap")
        if start is None:
            continue
        if end is None:
            end = start
        for lap in range(int(start), int(end) + 1):
            reds.add(lap)
    return reds


def _driver_meta(info: Dict[str, Any], fallback: str) -> Dict[str, str]:
    tla = info.get("tla", fallback)
    team = info.get("team", "Unknown")
    color = info.get("color", "")
    color = f"#{color}" if color and not color.startswith("#") else (color or "#FFFFFF")
    return {"driver": tla, "team": team, "color": color}


def _build_payload(
    store: SessionDataStore,
    reference: str,
    drivers_filter: Optional[List[str]],
) -> List[Dict[str, Any]]:
    """Build the race-gaps payload from a resolved store.

    Returns a list of ``{driver, team, color, laps: [{lap, gap_s}, ...]}``,
    ordered by finishing order (smallest final cumulative time first). ``gap_s``
    is ``None`` on red-flag laps so the plotted line breaks there.
    """
    lap_times = extract_lap_times(store)
    cum = _cumulative_times(lap_times)
    if not cum:
        return []

    driver_list = store.driver_list()

    if drivers_filter is not None:
        valid_tlas = {
            info.get("tla", num) for num, info in driver_list.items()
        }
        unknown = [t for t in drivers_filter if t not in valid_tlas]
        if unknown:
            raise DataNotAvailableError(
                year=store.year, gp=store.identifier, session=store.session_name,
                source="livetiming",
                reason=(
                    f"Unknown driver TLA(s): {', '.join(sorted(unknown))}. "
                    f"Valid: {', '.join(sorted(valid_tlas))}"
                ),
            )

    max_lap = max(lap for per_lap in cum.values() for lap in per_lap)

    # Per-lap leader cumulative time (minimum across drivers present at the lap).
    leader_cum: Dict[int, float] = {}
    for lap in range(1, max_lap + 1):
        vals = [per_lap[lap] for per_lap in cum.values() if lap in per_lap]
        if vals:
            leader_cum[lap] = min(vals)

    reference_pace: Optional[float] = None
    if reference == "average":
        reference_pace = _reference_pace(lap_times, cum)

    periods = get_track_status_periods(store)
    red_laps = _red_flag_laps(periods)

    payload: List[Dict[str, Any]] = []
    for num, per_lap in cum.items():
        info = driver_list.get(num, {})
        meta = _driver_meta(info, num)
        if drivers_filter is not None and meta["driver"] not in drivers_filter:
            continue

        laps: List[Dict[str, Any]] = []
        for lap in sorted(per_lap):
            if lap in red_laps:
                gap: Optional[float] = None
            elif reference == "leader":
                gap = round(per_lap[lap] - leader_cum[lap], 3)
            else:  # average / race trace
                gap = round(reference_pace * lap - per_lap[lap], 3)
            laps.append({"lap": lap, "gap_s": gap})

        final_cum = per_lap[max(per_lap)]
        payload.append({
            "driver": meta["driver"],
            "team": meta["team"],
            "color": meta["color"],
            "laps": laps,
            "_final_cum": final_cum,
        })

    payload.sort(key=lambda d: d["_final_cum"])
    for d in payload:
        d.pop("_final_cum", None)
    return payload


def _reference_pace(
    lap_times: Dict[str, List[Dict[str, Any]]],
    cum: Dict[str, Dict[int, float]],
) -> float:
    """Reference pace (avg lap time, seconds) for the race-trace baseline.

    Uses the race winner's average lap time: the driver with the smallest final
    cumulative time, averaged over the number of laps they completed. Falls back
    to the field median of all completed lap times if no winner is resolvable.
    """
    winner_num = None
    winner_final = None
    for num, per_lap in cum.items():
        final = per_lap[max(per_lap)]
        if winner_final is None or final < winner_final:
            winner_final = final
            winner_num = num

    if winner_num is not None:
        per_lap = cum[winner_num]
        laps_done = len(per_lap)
        if laps_done > 0:
            return per_lap[max(per_lap)] / laps_done

    all_times = [
        float(rec["time_s"])
        for records in lap_times.values()
        for rec in records
        if rec.get("time_s") and rec["time_s"] > 0
    ]
    return statistics.median(all_times) if all_times else 0.0


class RaceGapsData:
    """Callable: ``RaceGapsData()(year, id, session, reference, drivers) -> list``."""

    def __call__(
        self,
        y: int,
        identifier: Union[int, str],
        e: str,
        reference: str = "leader",
        drivers: Union[str, Sequence[str], None] = None,
    ) -> List[Dict[str, Any]]:
        _assert_valid_session(e, y, identifier)
        reference = reference if reference in ("leader", "average") else "leader"
        drivers_filter = _normalize_drivers(drivers)
        data_type = DATA_TYPE_LEADER if reference == "leader" else DATA_TYPE_AVERAGE

        def _generate() -> List[Dict[str, Any]]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store, reference, drivers_filter=None)

        full = cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=data_type, generator=_generate, version="v2",
        )

        if drivers_filter is None:
            return full

        # Filtering is applied post-cache so the full payload stays cacheable.
        valid_tlas = {d["driver"] for d in full}
        unknown = [t for t in drivers_filter if t not in valid_tlas]
        if unknown:
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason=(
                    f"Unknown driver TLA(s): {', '.join(sorted(unknown))}. "
                    f"Valid: {', '.join(sorted(valid_tlas))}"
                ),
            )
        return [d for d in full if d["driver"] in drivers_filter]


class RaceGapsPlot:
    """Callable: ``RaceGapsPlot()(year, id, session, reference, drivers) -> png path``."""

    def __call__(
        self,
        y: int,
        identifier: Union[int, str],
        e: str,
        reference: str = "leader",
        drivers: Union[str, Sequence[str], None] = None,
    ) -> str:
        _assert_valid_session(e, y, identifier)
        reference = reference if reference in ("leader", "average") else "leader"

        payload = RaceGapsData()(y, identifier, e, reference, drivers)
        if not payload:
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No lap-time data available to plot race gaps",
            )

        store = SessionDataStore(y, identifier, e)
        event_name = store.event_name
        periods = get_track_status_periods(store)

        return self._render(payload, periods, y, event_name, e, reference)

    @staticmethod
    def _render(
        payload: List[Dict[str, Any]],
        periods: List[Dict[str, Any]],
        y: int,
        event_name: str,
        e: str,
        reference: str,
    ) -> str:
        setup_theme.setup_turnone_theme()
        location, name = _init(y, event_name, e, reference)

        max_lap = max(
            (p["lap"] for d in payload for p in d["laps"]), default=1
        )

        fig, ax = plt.subplots(figsize=(14, 9), layout='constrained')

        # Second car of a team gets a dashed line so team-mates are visible as a
        # pair rather than overlapping identically-coloured solid lines.
        seen_teams: Dict[str, int] = {}
        for d in payload:
            team = d["team"]
            car_index = seen_teams.get(team, 0)
            seen_teams[team] = car_index + 1
            linestyle = "--" if car_index >= 1 else "-"

            laps = [p["lap"] for p in d["laps"]]
            # None gap_s (red flag) becomes NaN so matplotlib breaks the line.
            gaps = [
                float("nan") if p["gap_s"] is None else p["gap_s"]
                for p in d["laps"]
            ]

            ax.plot(
                laps, gaps,
                color=d["color"], linestyle=linestyle,
                label=d["driver"], marker=None,
            )

        ax.set_xlim(-0.5, max_lap + 0.5)
        ax.set_xlabel("Lap")

        if reference == "leader":
            ax.set_ylabel("Gap to leader (s)")
            # Leader (gap 0) on top: invert so smaller gaps are higher.
            ax.invert_yaxis()
            ax.axhline(0.0, color="#E9E9E9", linestyle=":", linewidth=1, zorder=1)
            title_mode = "gap to leader"
        else:
            ax.set_ylabel("Time vs reference pace (s)\n<- behind | ahead ->")
            ax.axhline(0.0, color="#E9E9E9", linestyle=":", linewidth=1, zorder=1)
            title_mode = "race trace (vs average pace)"

        add_track_status_shading = setup_theme.add_track_status_shading
        add_track_status_shading(ax, periods)

        ax.legend(loc="best", ncol=2, fontsize=9)

        try:
            logo = mpimg.imread('assets/images/logo mic.png')
            fig.figimage(logo, 575, 575, zorder=3, alpha=.6)
        except Exception:
            pass

        plt.suptitle(f"Race gaps — {title_mode}\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"


if __name__ == "__main__":
    print("Testing V2 Race Gaps...")
    for ref in ("leader", "average"):
        try:
            data = RaceGapsData()(2025, 1, "R", ref)
            print(f"[{ref}] Drivers: {len(data)}")
            if data:
                leader = data[0]
                last = leader["laps"][-1] if leader["laps"] else None
                print(f"[{ref}] Leader: {leader['driver']} last lap: {last}")
            plot_path = RaceGapsPlot()(2025, 1, "R", ref)
            print(f"[{ref}] Plot: {plot_path}")
        except Exception as ex:
            print(f"[{ref}] Error: {ex}")
            import traceback
            traceback.print_exc()
