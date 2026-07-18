"""
Race Story Timeline (V2, livetiming-backed). Flagship "gem" feature.

Tells the story of a race in a single shareable plot: gap-to-leader traces for
the top finishers (same math as ``race_gaps`` leader mode, built on the shared
``cumtime_by_lap`` helper), pit-stop markers colored by fitted compound,
Safety Car / VSC / Red-flag shading, and numbered annotation chips calling out
the key moments — lead changes, retirements, and penalties.

Sections of the payload:
  * ``drivers``      — per-driver gap-to-leader series (top ~10 finishers),
                        team color, pit-stop laps, and whether they retired.
  * ``key_moments``   — numbered, captioned events in chronological order:
                        lead changes ("VER passes NOR for the lead"),
                        retirements ("HAM retires"), and penalties (race
                        control messages mentioning "PENALTY").
  * ``track_status_periods`` — for shading, same shape as other race features.

Builders (all pure / unit-testable):
  * ``_build_gap_series``    — per-driver ``[{lap, gap_s}]``; ``gap_s`` is
                                ``None`` on red-flag laps so the plotted line
                                breaks across the stoppage (mirrors
                                ``race_gaps``).
  * ``_detect_lead_changes`` — scans ``extract_positions_by_lap`` for laps
                                where the P1 car number changes.
  * ``_detect_retirements``  — a driver's lap-time series ending more than 2
                                laps before the race's final completed lap is
                                treated as a retirement.
  * ``_build_key_moments``   — merges lead changes, retirements, and
                                "PENALTY"-flagged race-control messages into a
                                single chronological, capped (~10) list.

Only meaningful for Race / Sprint sessions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._helpers import extract_stints_from_data
from src.services.analysis.v2._race_helpers import (
    cumtime_by_lap,
    extract_lap_times,
    extract_pit_stops,
    extract_positions_by_lap,
    extract_race_control_events,
    get_track_status_periods,
)
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.services.plotting.colors import get_compound_color, get_team_color

logger = get_logger(__name__)

DATA_TYPE = "race_story"

# Sessions where a race-long narrative timeline is meaningful.
_VALID_SESSIONS = {"R", "RACE", "S", "SPRINT"}

# How many of the top finishers get a full, labeled trace.
_TOP_N_FINISHERS = 10

# A driver's series must end more than this many laps before the race's final
# completed lap to be flagged a retirement (avoids false positives for
# drivers who simply finish a few laps down / lapped at the flag).
_RETIREMENT_LAP_MARGIN = 2

# Cap on the number of annotated key-moment chips (keeps the plot legible).
_MAX_KEY_MOMENTS = 10


def _init(y: int, event_name: str, session_name: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f"Race story {y} {event_name} {session_name}.png"
    return location, name


def _assert_valid_session(session_name: str, year: int, identifier: Any) -> None:
    if session_name.strip().upper() not in _VALID_SESSIONS:
        raise DataNotAvailableError(
            year=year, gp=identifier, session=session_name,
            source="livetiming",
            reason=(
                "Race story is only available for Race/Sprint sessions "
                f"(got {session_name!r})"
            ),
        )


# ----------------------------------------------------------------------
# Pure payload builders (unit-testable without a live store)
# ----------------------------------------------------------------------
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


def _build_gap_series(
    cum: Dict[str, Dict[int, float]],
    periods: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Per-driver ``[{lap, gap_s}]`` gap-to-leader series.

    ``gap_s`` is the driver's cumulative time minus the per-lap leader's
    (minimum) cumulative time, so the leader always sits at ``0``. Laps inside
    a RED track-status period get ``gap_s = None`` so the plotted line breaks
    across the stoppage rather than drawing a misleading straight segment.
    """
    if not cum:
        return {}
    max_lap = max(lap for per_lap in cum.values() for lap in per_lap)
    leader_cum: Dict[int, float] = {}
    for lap in range(1, max_lap + 1):
        vals = [per_lap[lap] for per_lap in cum.values() if lap in per_lap]
        if vals:
            leader_cum[lap] = min(vals)

    red_laps = _red_flag_laps(periods)

    series: Dict[str, List[Dict[str, Any]]] = {}
    for num, per_lap in cum.items():
        laps: List[Dict[str, Any]] = []
        for lap in sorted(per_lap):
            if lap in red_laps:
                gap: Optional[float] = None
            else:
                gap = round(per_lap[lap] - leader_cum[lap], 3)
            laps.append({"lap": lap, "gap_s": gap})
        series[num] = laps
    return series


def _detect_lead_changes(
    positions: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Detect laps where the P1 car number changes.

    Returns ``[{lap, new_leader_num, prev_leader_num}, ...]`` in lap order.
    Scans every driver's per-lap position snapshots to find, for each lap, who
    held P1; a change from the previous lap's P1 holder is a lead change.
    """
    # Build {lap: car_num holding P1} from every driver's position records.
    p1_by_lap: Dict[int, str] = {}
    for num, records in positions.items():
        for rec in records:
            if rec.get("position") == 1:
                lap = rec["lap"]
                # Later records for the same lap win (most recent snapshot).
                p1_by_lap[lap] = num

    changes: List[Dict[str, Any]] = []
    prev_leader: Optional[str] = None
    for lap in sorted(p1_by_lap):
        leader = p1_by_lap[lap]
        if prev_leader is not None and leader != prev_leader:
            changes.append({
                "lap": lap,
                "new_leader_num": leader,
                "prev_leader_num": prev_leader,
            })
        prev_leader = leader
    return changes


def _detect_retirements(
    lap_times: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Detect drivers whose lap-time series ends well before the race finish.

    Returns ``[{num, last_lap}, ...]``. A driver "retires" if their last
    completed lap is more than ``_RETIREMENT_LAP_MARGIN`` laps before the
    race's final completed lap (the max last-lap across all drivers).
    """
    last_laps: Dict[str, int] = {}
    for num, records in lap_times.items():
        if records:
            last_laps[num] = max(r["lap"] for r in records)

    if not last_laps:
        return []
    final_lap = max(last_laps.values())

    retirements: List[Dict[str, Any]] = []
    for num, last_lap in last_laps.items():
        if last_lap < final_lap - _RETIREMENT_LAP_MARGIN:
            retirements.append({"num": num, "last_lap": last_lap})
    retirements.sort(key=lambda r: r["last_lap"])
    return retirements


def _build_key_moments(
    lead_changes: List[Dict[str, Any]],
    retirements: List[Dict[str, Any]],
    race_control_events: List[Dict[str, Any]],
    tla_by_num: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Merge lead changes, retirements, and penalties into a capped, numbered,
    chronological list of ``{lap, kind, caption}`` moments.

    Penalties are race-control messages whose ``message`` text contains
    "PENALTY" (case-insensitive); other race-control categories are ignored.
    The merged list is sorted by lap (``None`` laps last) and truncated to
    ``_MAX_KEY_MOMENTS``.
    """
    moments: List[Dict[str, Any]] = []

    for lc in lead_changes:
        new_tla = tla_by_num.get(lc["new_leader_num"], lc["new_leader_num"])
        prev_tla = tla_by_num.get(lc["prev_leader_num"], lc["prev_leader_num"])
        moments.append({
            "lap": lc["lap"],
            "kind": "lead_change",
            "caption": f"L{lc['lap']} {new_tla} passes {prev_tla} for the lead",
        })

    for ret in retirements:
        tla = tla_by_num.get(ret["num"], ret["num"])
        moments.append({
            "lap": ret["last_lap"],
            "kind": "retirement",
            "caption": f"L{ret['last_lap']} {tla} retires",
        })

    for ev in race_control_events:
        message = ev.get("message") or ""
        if "PENALTY" not in message.upper():
            continue
        lap = ev.get("lap")
        lap_label = f"L{lap} " if lap is not None else ""
        moments.append({
            "lap": lap,
            "kind": "penalty",
            "caption": f"{lap_label}{message.strip().title()}",
        })

    moments.sort(key=lambda m: (m["lap"] is None, m["lap"] if m["lap"] is not None else 0))
    moments = moments[:_MAX_KEY_MOMENTS]
    for i, m in enumerate(moments, start=1):
        m["n"] = i
    return moments


def build_payload_from_parts(
    lap_times: Dict[str, List[Dict[str, Any]]],
    positions: Dict[str, List[Dict[str, Any]]],
    pit_stops: Dict[str, List[Dict[str, Any]]],
    stints_by_num: Dict[str, List[Dict[str, Any]]],
    drivers: Dict[str, Dict[str, Any]],
    periods: List[Dict[str, Any]],
    race_control_events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Assemble the full race-story payload from already-extracted parts."""
    cum = cumtime_by_lap(lap_times)
    if not cum:
        return {"drivers": [], "key_moments": [], "track_status_periods": periods}

    gap_series = _build_gap_series(cum, periods)

    # Finishing order: smallest final cumulative time first.
    finish_order = sorted(cum.keys(), key=lambda num: cum[num][max(cum[num])])
    top_nums = finish_order[:_TOP_N_FINISHERS]

    tla_by_num = {num: info.get("tla", num) for num, info in drivers.items()}

    def _pit_laps(num: str) -> List[Dict[str, Any]]:
        stints = stints_by_num.get(num, [])
        out = []
        for stop in pit_stops.get(num, []):
            lap = stop["lap"]
            compound_out = None
            for st in stints:
                if st["start_lap"] > lap:
                    compound_out = st["compound"]
                    break
            out.append({"lap": lap, "compound": compound_out})
        return out

    driver_payload: List[Dict[str, Any]] = []
    for rank, num in enumerate(top_nums, start=1):
        info = drivers.get(num, {})
        laps = gap_series.get(num, [])
        last_lap = laps[-1]["lap"] if laps else None
        driver_payload.append({
            "driver": tla_by_num.get(num, num),
            "team": info.get("team", "Unknown"),
            "color": get_team_color(info.get("team", "Unknown")),
            "finish_rank": rank,
            "laps": laps,
            "pit_stops": _pit_laps(num),
            "last_lap": last_lap,
        })

    lead_changes = _detect_lead_changes(positions)
    retirements = _detect_retirements(lap_times)
    key_moments = _build_key_moments(lead_changes, retirements, race_control_events, tla_by_num)

    return {
        "drivers": driver_payload,
        "key_moments": key_moments,
        "track_status_periods": periods,
    }


def _build_payload(store: SessionDataStore) -> Dict[str, Any]:
    """Build the race-story payload from a resolved store."""
    lap_times = extract_lap_times(store)
    positions = extract_positions_by_lap(store)
    drivers = store.driver_list()
    periods = get_track_status_periods(store)
    race_control_events = extract_race_control_events(store)
    pit_stops = extract_pit_stops(store)

    timing_app = store.timing_app_data()
    stints_by_num: Dict[str, List[Dict[str, Any]]] = {}
    for num in drivers:
        stints_by_num[num] = extract_stints_from_data(timing_app, num)

    return build_payload_from_parts(
        lap_times, positions, pit_stops, stints_by_num, drivers, periods, race_control_events
    )


class RaceStoryData:
    """Callable: ``RaceStoryData()(year, identifier, session) -> dict``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> Dict[str, Any]:
        _assert_valid_session(e, y, identifier)

        def _generate() -> Dict[str, Any]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=DATA_TYPE, generator=_generate, version="v2",
        )


class RaceStoryPlot:
    """Callable: ``RaceStoryPlot()(year, identifier, session) -> png path``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> str:
        _assert_valid_session(e, y, identifier)

        payload = RaceStoryData()(y, identifier, e)
        if not payload.get("drivers"):
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No lap-time data available to plot the race story",
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

        drivers = payload["drivers"]
        periods = payload.get("track_status_periods") or []
        key_moments = payload.get("key_moments") or []

        max_lap = max((d["last_lap"] or 0) for d in drivers) if drivers else 1
        max_lap = max(max_lap, 1)

        # Reserve a right-side caption column for numbered key moments.
        fig = plt.figure(figsize=(16, 10), layout='constrained')
        gs = fig.add_gridspec(1, 4)
        ax = fig.add_subplot(gs[0, :3])
        ax_caption = fig.add_subplot(gs[0, 3])
        ax_caption.axis("off")

        ax.set_xlim(-0.5, max_lap + 4.0)  # extra room for end-of-trace TLA labels

        setup_theme.add_track_status_shading(ax, periods)

        winner = drivers[0] if drivers else None

        for d in drivers:
            laps = [p["lap"] for p in d["laps"]]
            gaps = [
                float("nan") if p["gap_s"] is None else p["gap_s"]
                for p in d["laps"]
            ]
            is_winner = winner is not None and d["driver"] == winner["driver"]

            if is_winner:
                color = d["color"]
                alpha = 1.0
                lw = 3.0
                zorder = 6
            else:
                color = "#888888"
                alpha = 0.35
                lw = 1.4
                zorder = 3

            ax.plot(
                laps, gaps, color=color if is_winner else d["color"], alpha=alpha,
                linewidth=lw, zorder=zorder, solid_capstyle="round",
            )

            # Pit-stop markers, colored by fitted compound.
            gap_by_lap = {p["lap"]: p["gap_s"] for p in d["laps"]}
            for stop in d.get("pit_stops", []):
                gap_at_stop = gap_by_lap.get(stop["lap"])
                if gap_at_stop is None:
                    continue
                ax.scatter(
                    [stop["lap"]], [gap_at_stop],
                    color=get_compound_color(stop.get("compound")),
                    edgecolors="#0d0d0d", linewidths=0.8, s=60, zorder=7,
                )

            # End-of-trace TLA label.
            if laps:
                ax.annotate(
                    d["driver"], xy=(laps[-1], gaps[-1] if gaps else 0),
                    xytext=(6, 0), textcoords="offset points",
                    va="center", ha="left", fontsize=9,
                    color=d["color"], fontweight="bold" if is_winner else "normal",
                )

        # Glow the winner's trace.
        if winner is not None:
            setup_theme.add_glow(ax, linewidth=8, alpha=0.22, passes=3)

        ax.set_xlabel("Lap")
        ax.set_ylabel("Gap to leader (s)")
        ax.invert_yaxis()  # leader at the top
        ax.axhline(0.0, color="#E9E9E9", linestyle=":", linewidth=1, zorder=1)

        # Numbered annotation chips along the top edge, with connector lines
        # down to the lap they refer to.
        y_top = ax.get_ylim()[1]  # inverted axis -> this is the visual top (gap=0-ish)
        for m in key_moments:
            lap = m.get("lap")
            if lap is None:
                continue
            ax.annotate(
                str(m["n"]),
                xy=(lap, 0), xycoords=("data", "data"),
                xytext=(lap, y_top), textcoords="data",
                ha="center", va="bottom",
                fontsize=9, color="#0d0d0d", fontweight="bold",
                bbox=dict(boxstyle="circle,pad=0.25", facecolor="#FFD700", edgecolor="#0d0d0d"),
                arrowprops=dict(arrowstyle="-", color="#FFD700", alpha=0.6, linewidth=1.0),
                zorder=8,
            )

        # Right-side caption column.
        ax_caption.set_title("Key moments", fontsize=12, loc="left")
        caption_lines = [f"({m['n']}) {m['caption']}" for m in key_moments]
        ax_caption.text(
            0.0, 1.0, "\n\n".join(caption_lines) if caption_lines else "No key moments detected",
            transform=ax_caption.transAxes, va="top", ha="left", fontsize=9.5,
            color="#E9E9E9", wrap=True,
        )

        legend_handles = [
            Line2D([0], [0], color=winner["color"] if winner else "#FFFFFF",
                   linewidth=3, label=f"Winner: {winner['driver']}" if winner else "Winner"),
            Line2D([0], [0], color="#888888", alpha=0.6, linewidth=1.4, label="Other finishers"),
        ]
        ax.legend(handles=legend_handles, loc="lower left", fontsize=9)

        try:
            logo = mpimg.imread('assets/images/logo mic.png')
            fig.figimage(logo, 575, 575, zorder=3, alpha=.5)
        except Exception:
            pass

        plt.suptitle(f"Race story\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"


if __name__ == "__main__":
    print("Testing V2 Race Story...")
    try:
        data = RaceStoryData()(2025, 1, "R")
        print(f"Drivers: {len(data['drivers'])}")
        print(f"Key moments: {len(data['key_moments'])}")
        for m in data["key_moments"][:5]:
            print(f"  ({m['n']}) {m['caption']}")
        plot_path = RaceStoryPlot()(2025, 1, "R")
        print(f"Plot: {plot_path}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
