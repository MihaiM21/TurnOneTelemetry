"""
Pit Stop Strategy & Undercut (V2, livetiming-backed).

The headline strategy feature: it turns raw livetiming pit + stint + lap-time
streams into a structured strategy payload and a two-panel plot.

Payload sections:
  * ``stops``      — every pit stop with the compound on/off and whether the
                     stop happened under a Safety Car / VSC.
  * ``undercuts``  — undercut attempts and their measured time swing. For each
                     stop by driver A on lap ``L`` we find rivals B who were
                     within a small cumulative-time gap of A at the end of lap
                     ``L-1`` and who pitted 1-5 laps *later* (the classic
                     undercut window). We measure the gap before A's stop and
                     again after B has completed their pit cycle (both cars on
                     fresh rubber) and report the swing.
  * ``summary``    — fastest stop of the race and per-team average stop time.
  * ``free_changes`` — red-flag / drive-through tyre changes (compound changed
                       with no measured pit-lane time / pit stop increment).

Undercut methodology decisions (documented):
  * ``gap_before_s`` = A_cumtime(L-1) - B_cumtime(L-1) is signed so a *positive*
    value means B was ahead (A behind, i.e. A is the attacker chasing).
  * ``gap_after_s``  = A_cumtime(La) - B_cumtime(La), where ``La`` is the lap
    after B's out-lap (both on fresh tyres).
  * ``gain_s``       = gap_before_s - gap_after_s, signed so *positive means the
    attacker gained* (closed the gap / jumped ahead).
  * ``worked``       = the attacker ends up ahead after both cycles complete
    (gap_after_s <= 0). We use "ends ahead" rather than "gain > 0" so a stop
    that merely closes the gap without passing is not reported as success.
  * Pairs where either stop is under SC/VSC are excluded (cheap stops distort
    the delta), as are red-flag-adjacent / drive-through cycles.

Only meaningful for Race / Sprint sessions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import matplotlib.image as mpimg
import matplotlib.pyplot as plt

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._helpers import extract_stints_from_data
from src.services.analysis.v2._race_helpers import (
    extract_lap_times,
    extract_pit_stops,
    get_track_status_periods,
)
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import colors as colors_module
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme

logger = get_logger(__name__)

DATA_TYPE = "pit_strategy"

# Sessions where pit strategy is meaningful (racing, not single-lap).
_VALID_SESSIONS = {"R", "RACE", "S", "SPRINT"}

# Track statuses that make a pit stop "cheap" and distort undercut math.
_SLOW_STATUSES = {"SC", "VSC"}

# Undercut window: a rival must pit this many laps *after* the attacker.
_UNDERCUT_WINDOW = (1, 5)
# Maximum cumulative-time gap (s) at end of lap L-1 for a rival to be "in play".
_UNDERCUT_GAP_THRESHOLD_S = 3.0

# Fallback compound palette (another agent owns colors.compound_colors); import
# it defensively so we never hard-depend on that parallel edit.
_COMPOUND_COLORS = {
    "SOFT": "#da291c",
    "MEDIUM": "#ffd12e",
    "HARD": "#f0f0ec",
    "INTERMEDIATE": "#43b02a",
    "WET": "#0067ad",
    "UNKNOWN": "#777777",
}


def _compound_color(compound: Optional[str]) -> str:
    palette = getattr(colors_module, "compound_colors", _COMPOUND_COLORS)
    key = (compound or "UNKNOWN").upper()
    return palette.get(key, palette.get("UNKNOWN", "#777777"))


def _init(y: int, event_name: str, session_name: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f"Pit strategy {y} {event_name} {session_name}.png"
    return location, name


def _assert_valid_session(session_name: str, year: int, identifier: Any) -> None:
    if session_name.strip().upper() not in _VALID_SESSIONS:
        raise DataNotAvailableError(
            year=year, gp=identifier, session=session_name,
            source="livetiming",
            reason=(
                "Pit strategy is only available for Race/Sprint sessions "
                f"(got {session_name!r})"
            ),
        )


# ----------------------------------------------------------------------
# Pure payload builders (unit-testable without a live store)
# ----------------------------------------------------------------------
def _lap_in_slow_period(lap: int, periods: List[Dict[str, Any]]) -> bool:
    """True if ``lap`` falls inside any SC/VSC period."""
    for p in periods:
        if p.get("status") not in _SLOW_STATUSES:
            continue
        start = p.get("start_lap")
        if start is None:
            continue
        end = p.get("end_lap")
        if end is None:
            end = float("inf")
        if start <= lap <= end:
            return True
    return False


def _compounds_around_stop(stints: List[Dict[str, Any]], lap: int):
    """Return ``(compound_in, compound_out)`` for a stop completed on ``lap``.

    ``compound_in`` is the tyre on the car when it entered the pit (the stint
    whose ``end_lap`` is at/just before the stop); ``compound_out`` is the tyre
    fitted (the stint that starts at/just after the stop lap).
    """
    compound_in: Optional[str] = None
    compound_out: Optional[str] = None
    for st in stints:
        if st["end_lap"] <= lap:
            compound_in = st["compound"]
        if st["start_lap"] > lap and compound_out is None:
            compound_out = st["compound"]
    # Boundary case: a stop on the exact end_lap of a stint means the next stint
    # is the out tyre.
    if compound_out is None:
        for st in stints:
            if st["start_lap"] >= lap and st["compound"] != compound_in:
                compound_out = st["compound"]
                break
    return compound_in, compound_out


def _build_stops(
    pit_stops: Dict[str, List[Dict[str, Any]]],
    stints_by_num: Dict[str, List[Dict[str, Any]]],
    drivers: Dict[str, Dict[str, Any]],
    periods: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Flatten per-driver pit stops into annotated stop records.

    Adds compound in/out from stint boundaries, ``under_sc`` from track status,
    and ``drive_through`` when a NumberOfPitStops increment carried no stint /
    compound change.
    """
    stops: List[Dict[str, Any]] = []
    for num, drv_stops in pit_stops.items():
        info = drivers.get(num, {})
        tla = info.get("tla", num)
        team = info.get("team", "Unknown")
        stints = stints_by_num.get(num, [])
        for stop in drv_stops:
            lap = stop["lap"]
            compound_in, compound_out = _compounds_around_stop(stints, lap)
            # Drive-through: pit-stop increment but no compound change.
            drive_through = (
                compound_in is not None
                and compound_out is not None
                and compound_in == compound_out
            ) or (compound_out is None)
            stops.append({
                "driver": tla,
                "num": num,
                "team": team,
                "lap": lap,
                "stop_n": stop["stop_n"],
                "pit_lane_time_s": stop["pit_lane_time_s"],
                "compound_in": compound_in,
                "compound_out": compound_out,
                "under_sc": _lap_in_slow_period(lap, periods),
                "drive_through": drive_through,
            })
    stops.sort(key=lambda s: (s["lap"], s["driver"]))
    return stops


def _build_free_changes(
    stops: List[Dict[str, Any]],
    stints_by_num: Dict[str, List[Dict[str, Any]]],
    drivers: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Compound changes not backed by a pit-stop increment (red-flag changes)."""
    free: List[Dict[str, Any]] = []
    stop_laps_by_num: Dict[str, set] = {}
    for s in stops:
        stop_laps_by_num.setdefault(s["num"], set()).add(s["lap"])

    for num, stints in stints_by_num.items():
        info = drivers.get(num, {})
        tla = info.get("tla", num)
        stop_laps = stop_laps_by_num.get(num, set())
        for prev, nxt in zip(stints, stints[1:]):
            if prev["compound"] == nxt["compound"]:
                continue
            boundary_lap = prev["end_lap"]
            # A pit stop near this boundary explains the change -> not "free".
            if any(abs(boundary_lap - sl) <= 1 for sl in stop_laps):
                continue
            free.append({
                "driver": tla,
                "lap": boundary_lap,
                "compound_in": prev["compound"],
                "compound_out": nxt["compound"],
            })
    free.sort(key=lambda f: (f["lap"], f["driver"]))
    return free


def _cumtime_by_lap(lap_times: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[int, float]]:
    """Per-driver cumulative race time at the end of each completed lap."""
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


def _build_undercuts(
    stops: List[Dict[str, Any]],
    lap_times: Dict[str, List[Dict[str, Any]]],
    drivers: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Detect and measure undercut attempts.

    See the module docstring for the full methodology. Excludes any pair where
    either stop is under SC/VSC or is a drive-through.
    """
    cumtime = _cumtime_by_lap(lap_times)
    tla_by_num = {num: info.get("tla", num) for num, info in drivers.items()}

    # First real stop per driver by lap (ignore cheap / drive-through stops).
    clean_stops = [
        s for s in stops
        if not s["under_sc"] and not s["drive_through"]
    ]
    lo, hi = _UNDERCUT_WINDOW

    undercuts: List[Dict[str, Any]] = []
    for att in clean_stops:
        a_num = att["num"]
        a_lap = att["lap"]  # attacker pits on lap L
        a_cum = cumtime.get(a_num, {})
        if (a_lap - 1) not in a_cum:
            continue

        for dfd in clean_stops:
            d_num = dfd["num"]
            if d_num == a_num:
                continue
            d_lap = dfd["lap"]
            # Defender must pit 1-5 laps LATER.
            if not (lo <= d_lap - a_lap <= hi):
                continue
            d_cum = cumtime.get(d_num, {})
            if (a_lap - 1) not in d_cum:
                continue

            gap_before = a_cum[a_lap - 1] - d_cum[a_lap - 1]
            # Only rivals within threshold and AHEAD of the attacker (positive gap).
            if not (0 < gap_before <= _UNDERCUT_GAP_THRESHOLD_S):
                continue

            # Measure after the defender's pit cycle completes: the lap after
            # the defender's out-lap, where both are on fresh rubber.
            after_lap = d_lap + 1
            if after_lap not in a_cum or after_lap not in d_cum:
                continue
            gap_after = a_cum[after_lap] - d_cum[after_lap]
            gain = gap_before - gap_after
            worked = gap_after <= 0

            undercuts.append({
                "attacker": tla_by_num.get(a_num, a_num),
                "defender": tla_by_num.get(d_num, d_num),
                "lap": a_lap,
                "gap_before_s": round(gap_before, 3),
                "gap_after_s": round(gap_after, 3),
                "gain_s": round(gain, 3),
                "worked": bool(worked),
            })
    undercuts.sort(key=lambda u: (u["lap"], u["attacker"]))
    return undercuts


def _build_summary(stops: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fastest stop + per-team average pit-lane time (excludes drive-throughs)."""
    timed = [
        s for s in stops
        if s.get("pit_lane_time_s") is not None and not s["drive_through"]
    ]

    fastest: Optional[Dict[str, Any]] = None
    if timed:
        best = min(timed, key=lambda s: s["pit_lane_time_s"])
        fastest = {
            "driver": best["driver"],
            "lap": best["lap"],
            "pit_lane_time_s": best["pit_lane_time_s"],
        }

    by_team: Dict[str, List[float]] = {}
    for s in timed:
        by_team.setdefault(s["team"], []).append(s["pit_lane_time_s"])

    avg_by_team = [
        {
            "team": team,
            "avg_pit_lane_time_s": round(sum(vals) / len(vals), 3),
            "n_stops": len(vals),
        }
        for team, vals in by_team.items()
    ]
    avg_by_team.sort(key=lambda t: t["avg_pit_lane_time_s"])

    return {"fastest_stop": fastest, "avg_stop_by_team": avg_by_team}


def build_payload_from_parts(
    pit_stops: Dict[str, List[Dict[str, Any]]],
    stints_by_num: Dict[str, List[Dict[str, Any]]],
    lap_times: Dict[str, List[Dict[str, Any]]],
    drivers: Dict[str, Dict[str, Any]],
    periods: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Assemble the full payload from already-extracted parts (test seam)."""
    stops = _build_stops(pit_stops, stints_by_num, drivers, periods)
    undercuts = _build_undercuts(stops, lap_times, drivers)
    summary = _build_summary(stops)
    free_changes = _build_free_changes(stops, stints_by_num, drivers)
    # Drop the private ``num`` key from public stop records.
    public_stops = [{k: v for k, v in s.items() if k != "num"} for s in stops]
    return {
        "stops": public_stops,
        "undercuts": undercuts,
        "summary": summary,
        "free_changes": free_changes,
    }


def _build_payload(store: SessionDataStore) -> Dict[str, Any]:
    """Build the pit-strategy payload from a resolved store."""
    drivers = store.driver_list()
    pit_stops = extract_pit_stops(store)
    lap_times = extract_lap_times(store)
    periods = get_track_status_periods(store)

    timing_app = store.timing_app_data()
    stints_by_num: Dict[str, List[Dict[str, Any]]] = {}
    for num in drivers:
        stints_by_num[num] = extract_stints_from_data(timing_app, num)

    return build_payload_from_parts(
        pit_stops, stints_by_num, lap_times, drivers, periods
    )


class PitStrategyData:
    """Callable: ``PitStrategyData()(year, identifier, session) -> dict``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> Dict[str, Any]:
        _assert_valid_session(e, y, identifier)

        def _generate() -> Dict[str, Any]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=DATA_TYPE, generator=_generate, version="v2",
        )


class PitStrategyPlot:
    """Callable: ``PitStrategyPlot()(year, identifier, session) -> png path``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> str:
        _assert_valid_session(e, y, identifier)

        payload = PitStrategyData()(y, identifier, e)
        if not payload.get("stops"):
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No pit-stop data available to plot",
            )

        store = SessionDataStore(y, identifier, e)
        event_name = store.event_name
        periods = get_track_status_periods(store)

        return self._render(payload, periods, y, event_name, e)

    @staticmethod
    def _render(
        payload: Dict[str, Any],
        periods: List[Dict[str, Any]],
        y: int,
        event_name: str,
        e: str,
    ) -> str:
        setup_theme.setup_turnone_theme()
        location, name = _init(y, event_name, e)

        stops = payload.get("stops", [])
        undercuts = payload.get("undercuts", [])

        fig, (ax_top, ax_bot) = plt.subplots(
            2, 1, figsize=(14, 11), height_ratios=[2, 1], layout='constrained'
        )

        PitStrategyPlot._render_timeline(ax_top, stops, periods)
        PitStrategyPlot._render_undercuts(ax_bot, undercuts)

        try:
            logo = mpimg.imread('assets/images/logo mic.png')
            fig.figimage(logo, 575, 575, zorder=3, alpha=.5)
        except Exception:
            pass

        plt.suptitle(f"Pit strategy & undercuts\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"

    @staticmethod
    def _render_timeline(ax, stops, periods) -> None:
        """Top panel: per-driver stop timeline (y=driver, x=lap)."""
        drivers = sorted({s["driver"] for s in stops})
        y_of = {tla: i for i, tla in enumerate(drivers)}

        max_lap = max((s["lap"] for s in stops), default=1)
        ax.set_xlim(0, max_lap + 1)
        ax.set_ylim(-0.5, len(drivers) - 0.5)

        setup_theme.add_track_status_shading(ax, periods)

        times = [
            s["pit_lane_time_s"] for s in stops if s.get("pit_lane_time_s")
        ]
        t_min = min(times) if times else 20.0
        t_max = max(times) if times else 30.0

        def _size(t: Optional[float]) -> float:
            if not t or t_max <= t_min:
                return 140.0
            frac = (t - t_min) / (t_max - t_min)
            return 90.0 + frac * 320.0

        seen_compounds = set()
        for s in stops:
            yv = y_of[s["driver"]]
            color = _compound_color(s.get("compound_out"))
            edge = "#FF3B3B" if s.get("under_sc") else "#0d0d0d"
            lw = 2.4 if s.get("under_sc") else 0.8
            marker = "X" if s.get("drive_through") else "o"
            comp = (s.get("compound_out") or "UNKNOWN").upper()
            label = comp if comp not in seen_compounds else None
            seen_compounds.add(comp)
            ax.scatter(
                s["lap"], yv,
                s=_size(s.get("pit_lane_time_s")),
                color=color, edgecolors=edge, linewidths=lw,
                marker=marker, zorder=5, label=label,
            )

        ax.set_yticks(range(len(drivers)))
        ax.set_yticklabels(drivers)
        ax.set_xlabel("Lap")
        ax.set_ylabel("Driver")
        ax.set_title("Pit stops (marker=fitted compound, size=pit-lane time, red edge=under SC/VSC)")
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(loc="upper right", fontsize=9, title="Compound / status")

    @staticmethod
    def _render_undercuts(ax, undercuts) -> None:
        """Bottom panel: undercut gains as a horizontal bar chart."""
        if not undercuts:
            ax.text(
                0.5, 0.5, "No undercut attempts detected",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=12, color="#888888",
            )
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title("Undercut gains")
            return

        ordered = sorted(undercuts, key=lambda u: u["gain_s"])
        labels = [
            f"{u['attacker']}->{u['defender']} L{u['lap']}" for u in ordered
        ]
        gains = [u["gain_s"] for u in ordered]
        bar_colors = [
            "#43b02a" if u["worked"] else "#da291c" for u in ordered
        ]

        ypos = range(len(ordered))
        ax.barh(list(ypos), gains, color=bar_colors, edgecolor="#0d0d0d")
        ax.set_yticks(list(ypos))
        ax.set_yticklabels(labels, fontsize=9)
        ax.axvline(0, color="#888888", linewidth=1.0)
        ax.set_xlabel("Gain (s)  |  green = worked, red = failed")
        ax.set_title("Undercut gains (positive = attacker gained time)")


if __name__ == "__main__":
    print("Testing V2 Pit Strategy...")
    try:
        data = PitStrategyData()(2025, 1, "R")
        print(f"Stops: {len(data['stops'])}")
        print(f"Undercuts: {len(data['undercuts'])}")
        print(f"Fastest: {data['summary']['fastest_stop']}")
        print(f"Free changes: {len(data['free_changes'])}")
        plot_path = PitStrategyPlot()(2025, 1, "R")
        print(f"Plot: {plot_path}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
