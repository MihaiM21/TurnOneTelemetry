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

import math
from typing import Any, Dict, List, Optional, Tuple, Union

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._helpers import RACE_SESSIONS, assert_session_type, extract_stints_from_data
from src.services.analysis.v2._race_helpers import (
    cumtime_by_lap,
    extract_lap_times,
    extract_pit_stops,
    extract_positions_by_lap,
    extract_race_control_events,
    get_track_status_periods,
)
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import canvas
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.services.plotting.canvas import add_legacy_watermark
from src.services.plotting.colors import get_compound_color, get_driver_color, get_team_color

logger = get_logger(__name__)

DATA_TYPE = "race_story"

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

    # Finishing order: by actual classified position at each driver's last
    # recorded lap, not by comparing cumulative race time across drivers —
    # a driver who retired early has fewer laps summed and so a smaller
    # cumulative total than one who ran the full race, which would otherwise
    # sort retirees ahead of drivers who finished behind them.
    def _final_position(num: str) -> Optional[int]:
        recs = positions.get(num, [])
        if not recs:
            return None
        return max(recs, key=lambda r: r.get("lap", -1)).get("position")

    def _finish_key(num: str):
        pos = _final_position(num)
        return (pos is None, pos if pos is not None else 0, cum[num][max(cum[num])])

    finish_order = sorted(cum.keys(), key=_finish_key)
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


# ----------------------------------------------------------------------
# Social-format helpers
# ----------------------------------------------------------------------
_SESSION_LABELS = {"R": "Race", "RACE": "Race", "S": "Sprint", "SPRINT": "Sprint"}

# How many finishers get a trace per format (landscape keeps the legacy ten).
_FORMAT_TOP_N = {"landscape": 10, "square": 8, "portrait": 8, "story": 6}

# Key-moment marker colours by kind (lead change keeps the legacy gold).
_MOMENT_STYLE = {
    "lead_change": ("#FFD700", "Lead change"),
    "retirement": ("#FF5A5A", "Retirement"),
    "penalty": ("#E9E9E9", "Penalty"),
}


def _session_label(e: str) -> str:
    return _SESSION_LABELS.get((e or "").strip().upper(), e)


def _driver_color(code: str, year: int, fallback: str) -> str:
    """Year-aware driver colour; the payload's own colour when the driver is unknown."""
    color = get_driver_color(code, year)
    if str(color).upper() == "#FFFFFF" and fallback:
        return fallback
    return color


def _spread_labels(points: List[Tuple[float, float]], min_gap: float, min_dx: float) -> List[float]:
    """Push label y-positions apart so no two labels closer than ``min_gap`` overlap.

    ``points`` are ``(x, y)`` trace ends; only labels within ``min_dx`` of one
    another horizontally can collide. Returns adjusted y values in input order.
    """
    order = sorted(range(len(points)), key=lambda i: points[i][1])
    adjusted: Dict[int, float] = {}
    for idx in order:
        x, yv = points[idx]
        pos = yv
        for prev, prev_y in adjusted.items():
            if abs(points[prev][0] - x) < min_dx:
                pos = max(pos, prev_y + min_gap)
        adjusted[idx] = pos
    return [adjusted[i] for i in range(len(points))]


def _assign_rows(laps: List[float], min_dx: float) -> List[int]:
    """Stagger markers onto rows so two markers on one row are never closer than ``min_dx``."""
    row_last: List[float] = []
    rows: List[int] = []
    for lap in laps:
        for r, last in enumerate(row_last):
            if lap - last >= min_dx:
                row_last[r] = lap
                rows.append(r)
                break
        else:
            row_last.append(lap)
            rows.append(len(row_last) - 1)
    return rows


class RaceStoryData:
    """Callable: ``RaceStoryData()(year, identifier, session) -> dict``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> Dict[str, Any]:
        assert_session_type(
            e, y, identifier, allowed=RACE_SESSIONS, feature="Race story", sessions_label="Race/Sprint",
        )

        def _generate() -> Dict[str, Any]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=DATA_TYPE, generator=_generate, version="v2",
        )


class RaceStoryPlot:
    """Callable: ``RaceStoryPlot()(year, identifier, session, fmt=None) -> png path``.

    ``fmt`` is one of ``canvas.FORMAT_NAMES``: the same payload is recomposed
    for that social format (file name gains a ``_{fmt}`` suffix, and the key
    moments become numbered markers only). ``None`` keeps the original render.
    """

    def __call__(self, y: int, identifier: Union[int, str], e: str, fmt: Optional[str] = None) -> str:
        if fmt is not None:
            canvas.get_format(fmt)  # ValueError before any network work
        assert_session_type(
            e, y, identifier, allowed=RACE_SESSIONS, feature="Race story", sessions_label="Race/Sprint",
        )

        payload = RaceStoryData()(y, identifier, e)
        if not payload.get("drivers"):
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No lap-time data available to plot the race story",
            )

        store = SessionDataStore(y, identifier, e)
        event_name = store.event_name

        if fmt is not None:
            return self._render_formatted(payload, y, event_name, e, fmt)
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

        add_legacy_watermark(fig, 575, 575, alpha=0.5, zorder=3)

        plt.suptitle(f"Race story\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"

    @staticmethod
    def _render_formatted(
        payload: Dict[str, Any],
        y: int,
        event_name: str,
        e: str,
        fmt_name: str,
    ) -> str:
        """Recomposed render for a social format (see :mod:`canvas`).

        Gap-to-leader traces for the top finishers (fewer on tall formats),
        pit-stop dots coloured by compound and SC/VSC/red-flag shading, as in
        the legacy chart -- but the key moments become numbered markers in a
        strip along the top, coloured by kind, with no caption text.
        """
        fmt = canvas.get_format(fmt_name)
        location, name = _init(y, event_name, e)
        path = f"{location}/{name[:-4]}{canvas.format_suffix(fmt.name)}.png"

        drivers = payload["drivers"][:_FORMAT_TOP_N[fmt.name]]
        periods = payload.get("track_status_periods") or []
        moments = [m for m in (payload.get("key_moments") or []) if m.get("lap") is not None]
        moments.sort(key=lambda m: m["lap"])

        max_lap = max(max((d["last_lap"] or 0) for d in drivers), 1)
        finite = [p["gap_s"] for d in drivers for p in d["laps"] if p["gap_s"] is not None]
        gap_max = max(max(finite, default=0.0), 1.0)

        # Legend entries: track status, compounds fitted at the stops, marker kinds.
        statuses = sorted({p.get("status") for p in periods if p.get("status") in ("YELLOW", "SC", "VSC", "RED")})
        compounds: List[str] = []
        for d in drivers:
            for stop in d.get("pit_stops", []):
                comp = (stop.get("compound") or "").upper()
                if comp and comp not in compounds:
                    compounds.append(comp)
        kinds = [k for k in _MOMENT_STYLE if any(m["kind"] == k for m in moments)]
        n_entries = len(statuses) + len(compounds) + len(kinds)

        font_px = fmt.base_fontsize * canvas.DESIGN_DPI / 72.0
        legend_pt = fmt.base_fontsize * 0.75
        legend_px = legend_pt * canvas.DESIGN_DPI / 72.0
        left, _, right, _ = fmt.safe
        est_width_px = (right - left) * fmt.width_px - 6.2 * font_px
        entry_px = legend_px * (0.62 * 9 + 5.5)
        ncol = max(1, min(n_entries or 1, int(est_width_px // entry_px)))
        legend_rows = math.ceil(n_entries / ncol) if n_entries else 0
        header = canvas.header_height(fmt)
        if legend_rows:
            header += (1.5 * legend_px * legend_rows + 0.6 * font_px) / fmt.height_px

        fig = canvas.new_canvas(fmt)
        canvas.add_header(fig, fmt, "Race story", f"{y} {event_name}  ·  {_session_label(e)}")
        canvas.add_footer(fig, fmt)
        canvas.add_watermark(fig, fmt)
        gs = canvas.safe_gridspec(fig, fmt, 1, header=header)
        ax = fig.add_subplot(gs[0])

        width_px = ax.get_position().width * fmt.width_px
        height_px = ax.get_position().height * fmt.height_px

        # Horizontal room for the end-of-trace codes.
        label_pt = fmt.base_fontsize * 0.85
        label_px = label_pt * canvas.DESIGN_DPI / 72.0
        code_px = 3 * 0.82 * label_px + 12
        pad_laps = max_lap * code_px / max(width_px - code_px, 1.0)
        x_right = max_lap + pad_laps
        laps_per_px = (x_right + 0.5) / max(width_px, 1.0)

        # Vertical room: a marker strip on top, then the gaps (leader at the top).
        marker_pt = fmt.base_fontsize * 0.75
        marker_px = 2.05 * marker_pt * canvas.DESIGN_DPI / 72.0
        rows = _assign_rows([m["lap"] for m in moments], (marker_px + 6) * laps_per_px)
        n_rows = (max(rows) + 1) if rows else 0
        strip_px = n_rows * marker_px * 1.12 + (0.3 * marker_px if n_rows else 0.0)
        y_bottom = gap_max * 1.05
        data_per_px = y_bottom / max(height_px - strip_px, 1.0)
        strip = strip_px * data_per_px
        ax.set_ylim(y_bottom, -strip)
        # Shade first (an open-ended period runs to the right edge of the laps),
        # then widen the view to make room for the end labels.
        ax.set_xlim(-0.5, max_lap + 0.5)
        setup_theme.add_track_status_shading(ax, periods)
        ax.set_xlim(-0.5, x_right)

        pit_size = (fmt.base_fontsize * 0.62) ** 2
        line_w = fmt.base_fontsize * 0.2
        ends: List[Tuple[float, float]] = []
        end_meta: List[Tuple[Dict[str, Any], str]] = []
        seen_teams: Dict[str, int] = {}
        for rank, d in enumerate(drivers):
            color = _driver_color(d["driver"], y, d["color"])
            car_index = seen_teams.get(d["team"], 0)
            seen_teams[d["team"]] = car_index + 1
            laps = [p["lap"] for p in d["laps"]]
            gaps = [float("nan") if p["gap_s"] is None else p["gap_s"] for p in d["laps"]]
            is_winner = rank == 0
            ax.plot(laps, gaps, color=color, lw=line_w * (1.5 if is_winner else 1.0),
                    linestyle="--" if car_index >= 1 else "-", alpha=1.0 if is_winner else 0.9,
                    zorder=6 if is_winner else 4, solid_capstyle="round")
            if is_winner:
                setup_theme.add_glow(ax, linewidth=8, alpha=0.22, passes=3)

            gap_by_lap = {p["lap"]: p["gap_s"] for p in d["laps"]}
            for stop in d.get("pit_stops", []):
                gap_at_stop = gap_by_lap.get(stop["lap"])
                if gap_at_stop is None:
                    continue
                ax.scatter([stop["lap"]], [gap_at_stop], color=get_compound_color(stop.get("compound")),
                           edgecolors="#0d0d0d", linewidths=0.8, s=pit_size, zorder=7)

            valid = [(lp, g) for lp, g in zip(laps, gaps) if not np.isnan(g)]
            if valid:
                ends.append((float(valid[-1][0]), float(valid[-1][1])))
                end_meta.append((d, color))

        ax.hlines(0.0, -0.5, max_lap + 0.5, color=canvas.TEXT, linestyle=":", linewidth=1, alpha=0.6, zorder=1)

        # End-of-trace codes, pushed apart where traces finish close together.
        if ends:
            gap_h = 1.12 * label_px * data_per_px
            adj = _spread_labels(ends, gap_h, min_dx=code_px * laps_per_px)
            for (x, yv), yl, (d, color) in zip(ends, adj, end_meta):
                if abs(yl - yv) > 0.25 * gap_h:
                    ax.plot([x, x + 6 * laps_per_px], [yv, yl], color=color, lw=1.0, alpha=0.7, zorder=3)
                ax.text(x + 10 * laps_per_px, yl, d["driver"], ha="left", va="center", fontsize=label_pt,
                        color=color, fontweight="bold" if d is drivers[0] else "semibold", zorder=8)

        # Numbered key-moment markers, staggered onto rows along the top.
        row_data = marker_px * 1.12 * data_per_px
        for m, row in zip(moments, rows):
            color = _MOMENT_STYLE.get(m["kind"], ("#FFD700", ""))[0]
            yy = -strip + (row + 0.5) * row_data + 0.15 * marker_px * data_per_px
            ax.plot([m["lap"], m["lap"]], [yy, 0.0], color=color, alpha=0.4, lw=1.0, zorder=2)
            ax.text(m["lap"], yy, str(m["n"]), ha="center", va="center", fontsize=marker_pt,
                    color="#0d0d0d", fontweight="bold", zorder=9,
                    bbox={"boxstyle": "circle,pad=0.3", "fc": color, "ec": "#0d0d0d", "lw": 0.8})

        ax.set_xticks(list(range(0, max_lap + 1, 10 if max_lap > 30 else 5)))
        ax.set_xlabel("Lap")
        ax.set_ylabel("Gap to leader (s)")
        canvas.style_axis(ax, fmt)
        ax.grid(False, axis="x")
        ax.set_yticks([t for t in ax.get_yticks() if 0 <= t <= y_bottom])

        if n_entries:
            handles: List[Any] = []
            labels: List[str] = []
            span_handles, span_labels = ax.get_legend_handles_labels()
            for status in statuses:
                if status in span_labels:
                    face = span_handles[span_labels.index(status)].get_facecolor()
                    handles.append(Patch(facecolor=face, alpha=0.75, edgecolor="none"))
                    labels.append(status)
            for comp in compounds:
                handles.append(Line2D([0], [0], marker="o", ls="", color=get_compound_color(comp),
                                      markeredgecolor="#0d0d0d", markersize=legend_pt * 0.8))
                labels.append(comp)
            for kind in kinds:
                handles.append(Line2D([0], [0], marker="o", ls="", color=_MOMENT_STYLE[kind][0],
                                      markeredgecolor="#0d0d0d", markersize=legend_pt * 0.8))
                labels.append(_MOMENT_STYLE[kind][1])
            ax.legend(handles, labels, loc="lower left", bbox_to_anchor=(0.0, 1.005), borderaxespad=0.0,
                      ncol=ncol, frameon=False, fontsize=legend_pt, labelcolor=canvas.TEXT,
                      columnspacing=1.4, handletextpad=0.5)

        return canvas.save_png(fig, path, fmt)


if __name__ == "__main__":
    logger.info("Testing V2 Race Story...")
    try:
        data = RaceStoryData()(2025, 1, "R")
        logger.info("Drivers: %s", len(data['drivers']))
        logger.info("Key moments: %s", len(data['key_moments']))
        for m in data["key_moments"][:5]:
            logger.info("  (%s) %s", m['n'], m['caption'])
        plot_path = RaceStoryPlot()(2025, 1, "R")
        logger.info("Plot: %s", plot_path)
    except Exception as ex:
        logger.error("Error: %s", ex)
        import traceback
        traceback.print_exc()
