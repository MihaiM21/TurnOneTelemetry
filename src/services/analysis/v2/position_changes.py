"""
Position Changes (V2, livetiming-backed).

Classic "position chart": one line per driver, x=lap, y=race position, built
from ``SessionDataStore`` + ``_race_helpers.extract_positions_by_lap``. Only
meaningful for wheel-to-wheel sessions (Race / Sprint) — grid-position-only
sessions (Q/FP) don't have a lap-indexed position series worth plotting.

Both the Data and Plot entry points route through ``cached_or_generate`` so
the payload built once is reused by the plot (Mongo/Redis serves both).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import matplotlib.pyplot as plt

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._helpers import RACE_SESSIONS, assert_session_type
from src.services.analysis.v2._race_helpers import (
    extract_lap_times,
    extract_positions_by_lap,
    get_track_status_periods,
)
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import canvas
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.services.plotting.canvas import add_legacy_watermark
from src.services.plotting.colors import get_driver_color

logger = get_logger(__name__)

DATA_TYPE = "position_changes"


def _init(y: int, event_name: str, session_name: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f"Position changes {y} {event_name} {session_name}.png"
    return location, name


def _fill_laps(records: List[Dict[str, Any]], last_lap: int) -> List[Dict[str, Any]]:
    """One ``{lap, position}`` per lap from 0 to ``last_lap``.

    Live timing only sends a driver's ``Position`` when it changes, so the raw
    records are a change log: a leader who never lost P1 has a single record
    (the grid) and drew no line at all, and every other line ran diagonally
    between changes and stopped at the last one. Each position is carried
    forward until the next change, up to the laps the driver completed.
    """
    by_lap: Dict[int, int] = {}
    for r in records:
        by_lap[int(r["lap"])] = int(r["position"])
    out: List[Dict[str, Any]] = []
    position = None
    for lap in range(0, max(last_lap, max(by_lap)) + 1):
        position = by_lap.get(lap, position)
        if position is not None:
            out.append({"lap": lap, "position": position})
    return out


def _laps_completed(store: SessionDataStore) -> Dict[str, int]:
    """Highest completed lap per car number (0 when the lap stream is unavailable)."""
    try:
        lap_times = extract_lap_times(store)
    except Exception:
        logger.warning("Position changes: lap times unavailable, lines end at the last position change",
                       exc_info=True)
        return {}
    return {num: max((int(r["lap"]) for r in laps), default=0) for num, laps in lap_times.items()}


def _build_payload(store: SessionDataStore) -> List[Dict[str, Any]]:
    """Build the position-changes payload from a resolved store.

    Returns a list ordered by finishing (end) position: each entry has
    ``driver`` (TLA), ``team``, ``color``, ``start_pos``, ``end_pos`` and
    ``positions`` (``[{lap, position}, ...]``, lap 0 = grid, one entry per
    lap the driver completed -- see :func:`_fill_laps`).
    """
    positions_by_num = extract_positions_by_lap(store)
    drivers = store.driver_list()
    laps_completed = _laps_completed(store)

    payload: List[Dict[str, Any]] = []
    for num, records in positions_by_num.items():
        if not records:
            continue
        info = drivers.get(num, {})
        tla = info.get("tla", num)
        team = info.get("team", "Unknown")
        color = info.get("color", "")
        color = f"#{color}" if color and not color.startswith("#") else (color or "#FFFFFF")

        payload.append({
            "driver": tla,
            "team": team,
            "color": color,
            "start_pos": records[0]["position"],
            "end_pos": records[-1]["position"],
            "positions": _fill_laps(records, laps_completed.get(num, 0)),
        })

    payload.sort(key=lambda d: d["end_pos"])
    return payload


class PositionChangesData:
    """Callable: ``PositionChangesData()(year, identifier, session) -> list``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> List[Dict[str, Any]]:
        assert_session_type(
            e, y, identifier, allowed=RACE_SESSIONS, feature="Position changes", sessions_label="Race/Sprint",
        )

        def _generate() -> List[Dict[str, Any]]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=DATA_TYPE, generator=_generate, version="v2",
        )


_SESSION_LABELS = {"R": "Race", "RACE": "Race", "S": "Sprint", "SPRINT": "Sprint"}

# Formatted renders: how many finishers get a full, labelled line before the
# rest fade into the background (``None`` = every driver is drawn in full).
_HIGHLIGHT_TOP = {"story": 10}


def _session_label(e: str) -> str:
    return _SESSION_LABELS.get((e or "").strip().upper(), e)


def _driver_color(code: str, year: int, fallback: str) -> str:
    """Year-aware driver colour; the payload's own colour when the driver is unknown."""
    color = get_driver_color(code, year)
    if str(color).upper() == "#FFFFFF" and fallback:
        return fallback
    return color


class PositionChangesPlot:
    """Callable: ``PositionChangesPlot()(year, identifier, session, fmt=None) -> png path``.

    ``fmt`` is one of ``canvas.FORMAT_NAMES``: the same payload is recomposed
    for that social format (file name gains a ``_{fmt}`` suffix). ``None`` keeps
    the original render untouched.
    """

    def __call__(self, y: int, identifier: Union[int, str], e: str, fmt: Optional[str] = None) -> str:
        if fmt is not None:
            canvas.get_format(fmt)  # ValueError before any network work
        assert_session_type(
            e, y, identifier, allowed=RACE_SESSIONS, feature="Position changes", sessions_label="Race/Sprint",
        )

        payload = PositionChangesData()(y, identifier, e)
        if not payload:
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No position data available to plot",
            )

        store = SessionDataStore(y, identifier, e)
        event_name = store.event_name
        periods = get_track_status_periods(store)

        if fmt is not None:
            return self._render_formatted(payload, periods, y, event_name, e, fmt)
        return self._render(payload, periods, y, event_name, e)

    @staticmethod
    def _render(
        payload: List[Dict[str, Any]],
        periods: List[Dict[str, Any]],
        y: int,
        event_name: str,
        e: str,
    ) -> str:
        setup_theme.setup_turnone_theme()
        location, name = _init(y, event_name, e)

        max_pos = max((d["start_pos"] for d in payload), default=20)
        max_pos = max(max_pos, max((d["end_pos"] for d in payload), default=20))
        max_lap = max(
            (p["lap"] for d in payload for p in d["positions"]), default=1
        )

        fig, ax = plt.subplots(figsize=(14, 9), layout='constrained')

        # Second car of a team gets a dashed line so team-mates are visible
        # as a pair rather than overlapping identically-colored solid lines.
        seen_teams: Dict[str, int] = {}
        for d in payload:
            team = d["team"]
            car_index = seen_teams.get(team, 0)
            seen_teams[team] = car_index + 1
            linestyle = "--" if car_index >= 1 else "-"

            laps = [p["lap"] for p in d["positions"]]
            positions = [p["position"] for p in d["positions"]]

            ax.plot(
                laps, positions,
                color=d["color"], linestyle=linestyle,
                label=d["driver"], marker=None,
            )

            # TLA label at both the start and end of the line.
            ax.annotate(
                d["driver"], (laps[0], positions[0]),
                xytext=(-6, 0), textcoords="offset points",
                ha="right", va="center", fontsize=8, color=d["color"],
            )
            ax.annotate(
                d["driver"], (laps[-1], positions[-1]),
                xytext=(6, 0), textcoords="offset points",
                ha="left", va="center", fontsize=8, color=d["color"],
            )

        ax.set_xlim(-0.5, max_lap + 0.5)
        ax.set_ylim(max_pos + 0.5, 0.5)
        ax.set_yticks(range(1, max_pos + 1))
        ax.set_xlabel("Lap")
        ax.set_ylabel("Position")

        add_track_status_shading = setup_theme.add_track_status_shading
        add_track_status_shading(ax, periods)

        add_legacy_watermark(fig, 575, 575, alpha=0.6, zorder=3)

        plt.suptitle(f"Position changes\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"

    @staticmethod
    def _render_formatted(
        payload: List[Dict[str, Any]],
        periods: List[Dict[str, Any]],
        y: int,
        event_name: str,
        e: str,
        fmt_name: str,
    ) -> str:
        """Recomposed render for a social format (see :mod:`canvas`).

        Every driver's line runs between a start-of-race and an end-of-race code
        label, so no legend is needed. On ``story`` only the top ten finishers
        are drawn in full; the rest stay as faint, unlabelled context lines.
        """
        fmt = canvas.get_format(fmt_name)
        location, name = _init(y, event_name, e)
        path = f"{location}/{name[:-4]}{canvas.format_suffix(fmt.name)}.png"

        fig = canvas.new_canvas(fmt)
        canvas.add_header(fig, fmt, "Position changes", f"{y} {event_name}  ·  {_session_label(e)}")
        canvas.add_footer(fig, fmt)
        canvas.add_watermark(fig, fmt)

        max_pos = max(
            max((d["start_pos"] for d in payload), default=20),
            max((d["end_pos"] for d in payload), default=20),
        )
        max_lap = max((p["lap"] for d in payload for p in d["positions"]), default=1)
        max_lap = max(max_lap, 1)

        statuses = sorted({p.get("status") for p in periods if p.get("status") in ("YELLOW", "SC", "VSC", "RED")})
        font_px = fmt.base_fontsize * canvas.DESIGN_DPI / 72.0
        header = canvas.header_height(fmt) + (1.9 * font_px / fmt.height_px if statuses else 0.0)
        gs = canvas.safe_gridspec(fig, fmt, 1, header=header)
        ax = fig.add_subplot(gs[0])

        width_px = ax.get_position().width * fmt.width_px
        height_px = ax.get_position().height * fmt.height_px
        # Label size: body-sized, but never taller than a row of the chart.
        row_px = height_px / max(max_pos, 1)
        label_pt = min(fmt.base_fontsize * 0.85, 0.62 * row_px * 72.0 / canvas.DESIGN_DPI)
        label_pt = max(label_pt, fmt.base_fontsize * 0.7)
        label_px = 3 * 0.82 * label_pt * canvas.DESIGN_DPI / 72.0 + 30
        pad_laps = max_lap * label_px / max(width_px - 2 * label_px, 1.0)
        ax.set_ylim(max_pos + 0.5, 0.5)
        # Shade first (an open-ended period runs to the right edge of the laps),
        # then widen the view to make room for the end labels.
        ax.set_xlim(-0.5, max_lap + 0.5)
        setup_theme.add_track_status_shading(ax, periods)
        ax.set_xlim(-0.5 - pad_laps, max_lap + 0.5 + pad_laps)

        top_n = _HIGHLIGHT_TOP.get(fmt.name)
        line_w = fmt.base_fontsize * 0.17
        seen_teams: Dict[str, int] = {}
        for d in payload:
            car_index = seen_teams.get(d["team"], 0)
            seen_teams[d["team"]] = car_index + 1
            laps = [p["lap"] for p in d["positions"]]
            positions = [p["position"] for p in d["positions"]]
            if top_n is not None and d["end_pos"] > top_n:
                ax.plot(laps, positions, color="#5a5a5a", lw=line_w * 0.55, alpha=0.5, zorder=2,
                        solid_capstyle="round")
                continue
            color = _driver_color(d["driver"], y, d["color"])
            ax.plot(laps, positions, color=color, lw=line_w, linestyle="--" if car_index >= 1 else "-",
                    zorder=4, solid_capstyle="round")
            ends = [(laps[0], positions[0], -8, "right")]
            if len(laps) > 1:
                ends.append((laps[-1], positions[-1], 8, "left"))
            for x, pos, dx, ha in ends:
                ax.annotate(d["driver"], (x, pos), xytext=(dx, 0), textcoords="offset points", ha=ha,
                            va="center", fontsize=label_pt, color=color, fontweight="bold", zorder=6,
                            annotation_clip=False)

        step = 10 if max_lap > 30 else 5
        ax.set_xticks(list(range(0, max_lap + 1, step)))
        ax.set_yticks(range(1, max_pos + 1))
        ax.set_xlabel("Lap")
        ax.set_ylabel("Position")
        canvas.style_axis(ax, fmt)
        ax.tick_params(axis="y", labelsize=min(fmt.base_fontsize * 0.85, label_pt))
        ax.grid(False, axis="x")
        ax.tick_params(axis="y", length=0)

        if statuses:
            leg = ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.005), borderaxespad=0.0, ncol=len(statuses),
                            frameon=False, fontsize=fmt.base_fontsize * 0.75, labelcolor=canvas.TEXT,
                            columnspacing=1.6, handletextpad=0.6)
            for handle in leg.legend_handles:
                handle.set_alpha(0.75)

        return canvas.save_png(fig, path, fmt)


if __name__ == "__main__":
    logger.info("Testing V2 Position Changes...")
    try:
        data = PositionChangesData()(2025, 1, "R")
        logger.info("Drivers: %s", len(data))
        if data:
            logger.info("Winner: %s", data[0])
        plot_path = PositionChangesPlot()(2025, 1, "R")
        logger.info("Plot: %s", plot_path)
    except Exception as ex:
        logger.error("Error: %s", ex)
        import traceback
        traceback.print_exc()
