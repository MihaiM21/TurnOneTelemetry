"""
Theoretical Best Lap (V2, livetiming-backed).

For each driver, compares the "theoretical" best lap — the sum of their best
individual sector times (S1 + S2 + S3), taken independently across the whole
session — against their actual best complete lap. The delta shows how much
time was left on the table by never stringing all three sectors together in
the same lap.

Sources:
  * ``_race_helpers.extract_best_sectors`` — per-driver minimum S1/S2/S3 from
    the ``TimingData`` stream's ``Lines.{num}.Sectors`` field (a stream nobody
    else in the codebase parses yet; see the module docstring in
    ``_race_helpers.py`` for the snapshot-vs-incremental shape).
  * ``_race_helpers.extract_lap_times`` — per-driver completed laps; the best
    valid (``time_s > 0``) lap is the "actual" best lap.

Methodology:
  * A driver is skipped entirely if any of S1/S2/S3 is missing (can't compute
    a theoretical lap without all three).
  * ``theoretical_s = s1 + s2 + s3``.
  * ``delta_s = max(0.0, actual_s - theoretical_s)`` — clamped at zero because
    sector-time rounding/measurement noise can occasionally put the summed
    sectors a few milliseconds *above* the actual lap; a negative "time left
    on the table" isn't meaningful and would look like a bug in the plot.
  * Drivers are sorted by ascending theoretical lap time (fastest theoretical
    at the top of the dumbbell chart).

Only meaningful for Qualifying / Sprint Qualifying sessions (single-lap pace).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MultipleLocator

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._helpers import assert_session_type
from src.services.analysis.v2._race_helpers import extract_best_sectors, extract_lap_times
from src.services.analysis.v2.qualifying_results import legible_color
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import canvas as cv
from src.services.plotting.canvas import add_legacy_watermark
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.services.plotting.colors import get_team_color

logger = get_logger(__name__)

DATA_TYPE = "theoretical_best"

# Sessions where a single-lap "theoretical best" is meaningful.
_VALID_SESSIONS = {
    "Q", "QUALIFYING", "SQ", "SPRINT QUALIFYING", "SPRINT SHOOTOUT",
}


def _init(y: int, event_name: str, session_name: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f"Theoretical best {y} {event_name} {session_name}.png"
    return location, name


# Rows shown per format. A story is watched on a phone, so it keeps the front
# of the order; the other formats show the whole grid.
_MAX_ROWS = {"story": 15}

_SESSION_LONG = {
    "Q": "Qualifying", "QUALIFYING": "Qualifying", "SQ": "Sprint Qualifying",
    "SPRINT QUALIFYING": "Sprint Qualifying", "SPRINT SHOOTOUT": "Sprint Shootout",
}


def _session_long(code: str) -> str:
    return _SESSION_LONG.get(str(code).strip().upper(), str(code))


def _fmt_lap_axis(value: float, _pos=None) -> str:
    minutes = int(value // 60)
    return f"{minutes}:{value - 60 * minutes:04.1f}"


def _tick_step(span: float, max_ticks: int = 6) -> float:
    for step in (0.1, 0.2, 0.5, 1.0, 2.0, 5.0):
        if span / step <= max_ticks:
            return step
    return 10.0


def _row_color(row: Dict[str, Any], y: int) -> str:
    color = get_team_color(row.get("team", "Unknown"), y)
    return color if color != "#FFFFFF" or not row.get("color") else row["color"]


def _render_formatted(
    payload: List[Dict[str, Any]], fmt: str, y: int, event_name: str, e: str,
) -> str:
    """Social-format dumbbell: ranked by theoretical best, time left on the table as a number.

    Hollow marker = best sectors combined, filled = actual best lap; the number
    at the right is the actual lap minus the theoretical one.
    """
    canvas_fmt = cv.get_format(fmt)
    location, name = _init(y, event_name, e)

    rows = payload[:_MAX_ROWS.get(canvas_fmt.name, len(payload))]
    n = len(rows)
    colors = [_row_color(r, y) for r in rows]
    base = canvas_fmt.base_fontsize

    fig = cv.new_canvas(canvas_fmt)
    cv.add_header(fig, canvas_fmt, "Theoretical best lap", f"{y} {event_name}  ·  {_session_long(e)}")
    cv.add_footer(fig, canvas_fmt)
    cv.add_watermark(fig, canvas_fmt)

    # A little extra header room for the "LEFT ON TABLE" column title.
    gs = cv.safe_gridspec(fig, canvas_fmt, 1, 2, header=cv.header_height(canvas_fmt) + 0.012,
                          width_ratios=[4.0 if canvas_fmt.is_vertical else 7.5, 1.0], wspace=0.03)
    ax = fig.add_subplot(gs[0, 0])
    ax_num = fig.add_subplot(gs[0, 1], sharey=ax)
    ax_num.set_axis_off()

    marker_area = (base * 1.1) ** 2
    for i, (row, color) in enumerate(zip(rows, colors)):
        ax.plot([row["theoretical_s"], row["actual_s"]], [i, i], color="#666666", lw=2.4, zorder=1,
                solid_capstyle="round")
        ax.scatter([row["theoretical_s"]], [i], s=marker_area, facecolors="none", edgecolors=color,
                   linewidths=2.4, zorder=3)
        ax.scatter([row["actual_s"]], [i], s=marker_area, facecolors=color, edgecolors=cv.FACE,
                   linewidths=1.0, zorder=4)
        ax_num.text(1.0, i, f"+{row['delta_s']:.3f}", ha="right", va="center", fontsize=base,
                    fontweight="bold", color=cv.TEXT)

    lo = min(r["theoretical_s"] for r in rows)
    hi = max(r["actual_s"] for r in rows)
    # Pad by the marker radius (in pixels) so the end markers clear the axis frame.
    axes_px = ax.get_position().width * canvas_fmt.width_px
    pad = max((hi - lo), 0.1) * (0.9 * marker_area ** 0.5 * cv.DESIGN_DPI / 72.0) / max(axes_px, 1.0)
    ax.set_xlim(lo - pad, hi + pad)
    ax.set_ylim(n - 0.5, -0.5)
    # Tick labels ("1:43.5") must not touch: allow as many as the axis width holds.
    label_px = 6 * base * 0.85 * cv.DESIGN_DPI / 72.0 * 0.62 + 24
    ax.xaxis.set_major_locator(MultipleLocator(_tick_step(hi - lo + 2 * pad, max(2, int(axes_px / label_px)))))
    ax.xaxis.set_major_formatter(FuncFormatter(_fmt_lap_axis))
    ax_num.set_xlim(0, 1)

    ax.set_yticks(range(n))
    ax.set_yticklabels([r["driver"] for r in rows], fontweight="bold")
    for tick, color in zip(ax.get_yticklabels(), colors):
        tick.set_color(legible_color(color))
    ax.set_xlabel("Lap time")
    cv.style_axis(ax, canvas_fmt)
    ax.grid(False, axis="y")
    ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0)

    ax_num.text(1.0, 1.012, "LEFT ON TABLE", transform=ax_num.transAxes, ha="right", va="bottom",
                fontsize=base * 0.75, fontweight="semibold", color=cv.MUTED)

    theo_proxy = plt.Line2D([0], [0], marker="o", color="none", markerfacecolor="none",
                            markeredgecolor=cv.TEXT, markeredgewidth=2, markersize=base * 0.8,
                            label="Best sectors")
    actual_proxy = plt.Line2D([0], [0], marker="o", color="none", markerfacecolor=cv.TEXT,
                              markeredgecolor=cv.FACE, markersize=base * 0.8, label="Actual lap")
    ax.legend(handles=[theo_proxy, actual_proxy], loc="upper right", fontsize=base * 0.8,
              facecolor="#141414", edgecolor="#2a2a2a", labelcolor=cv.TEXT)

    stem = name[:-4] if name.lower().endswith(".png") else name
    path = f"{location}/{stem}{cv.format_suffix(canvas_fmt.name)}.png"
    return cv.save_png(fig, path, canvas_fmt)


# ----------------------------------------------------------------------
# Pure payload builders (unit-testable without a live store)
# ----------------------------------------------------------------------
def _best_actual_laps(lap_times: Dict[str, List[Dict[str, Any]]]) -> Dict[str, float]:
    """Per-driver best valid (``time_s > 0``) completed lap time."""
    best: Dict[str, float] = {}
    for num, records in lap_times.items():
        valid = [r["time_s"] for r in records if r.get("time_s") and r["time_s"] > 0]
        if valid:
            best[num] = min(valid)
    return best


def build_payload_from_parts(
    best_sectors: Dict[str, Dict[str, float]],
    lap_times: Dict[str, List[Dict[str, Any]]],
    drivers: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Assemble the theoretical-best payload from already-extracted parts.

    Returns a list of ``{driver, team, color, theoretical_s, actual_s, delta_s}``
    sorted by ascending ``theoretical_s`` (fastest theoretical first). Drivers
    missing any sector, or with no valid actual best lap, are excluded.
    """
    actual_best = _best_actual_laps(lap_times)

    rows: List[Dict[str, Any]] = []
    for num, sectors in best_sectors.items():
        if not all(k in sectors for k in ("s1", "s2", "s3")):
            continue
        actual_s = actual_best.get(num)
        if actual_s is None:
            continue

        theoretical_s = sectors["s1"] + sectors["s2"] + sectors["s3"]
        delta_s = max(0.0, actual_s - theoretical_s)

        info = drivers.get(num, {})
        tla = info.get("tla", num)
        team = info.get("team", "Unknown")
        color = get_team_color(team)

        rows.append({
            "driver": tla,
            "team": team,
            "color": color,
            "theoretical_s": round(theoretical_s, 3),
            "actual_s": round(actual_s, 3),
            "delta_s": round(delta_s, 3),
        })

    rows.sort(key=lambda r: r["theoretical_s"])
    return rows


def _build_payload(store: SessionDataStore) -> List[Dict[str, Any]]:
    """Build the theoretical-best payload from a resolved store."""
    best_sectors = extract_best_sectors(store)
    lap_times = extract_lap_times(store)
    drivers = store.driver_list()
    return build_payload_from_parts(best_sectors, lap_times, drivers)


class TheoreticalBestData:
    """Callable: ``TheoreticalBestData()(year, identifier, session) -> list``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> List[Dict[str, Any]]:
        assert_session_type(
            e, y, identifier, allowed=_VALID_SESSIONS, feature="Theoretical best lap", sessions_label="Qualifying",
        )

        def _generate() -> List[Dict[str, Any]]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=DATA_TYPE, generator=_generate, version="v2",
        )


class TheoreticalBestPlot:
    """Callable: ``TheoreticalBestPlot()(year, identifier, session, fmt=None) -> png path``.

    ``fmt`` (a name from ``canvas.FORMAT_NAMES``) renders the social layout to a
    file with the format suffix; ``None`` keeps the legacy figure and file name.
    """

    def __call__(self, y: int, identifier: Union[int, str], e: str, fmt: Optional[str] = None) -> str:
        if fmt is not None:
            cv.get_format(fmt)  # fail fast on a bad name, before any data work
        assert_session_type(
            e, y, identifier, allowed=_VALID_SESSIONS, feature="Theoretical best lap", sessions_label="Qualifying",
        )

        payload = TheoreticalBestData()(y, identifier, e)
        if not payload:
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No sector-time data available to plot theoretical best lap",
            )

        store = SessionDataStore(y, identifier, e)
        event_name = store.event_name

        if fmt is not None:
            return _render_formatted(payload, fmt, y, event_name, e)
        return self._render(payload, y, event_name, e)

    @staticmethod
    def _render(
        payload: List[Dict[str, Any]],
        y: int,
        event_name: str,
        e: str,
    ) -> str:
        setup_theme.setup_turnone_theme()
        location, name = _init(y, event_name, e)

        # Fastest theoretical at the TOP of the chart -> reverse for a
        # bottom-up y-axis (matplotlib draws index 0 at the bottom).
        rows = list(reversed(payload))
        n = len(rows)

        fig, ax = plt.subplots(figsize=(13, 13), layout='constrained')

        y_positions = range(n)
        for yi, row in zip(y_positions, rows):
            color = row["color"]
            theo = row["theoretical_s"]
            actual = row["actual_s"]

            # Grey connector between theoretical and actual.
            ax.plot([theo, actual], [yi, yi], color="#555555", linewidth=2.0, zorder=1)

            # Hollow circle = theoretical best (sum of best sectors).
            ax.scatter(
                [theo], [yi], s=180, facecolors="none", edgecolors=color,
                linewidths=2.2, zorder=3,
            )
            # Filled circle = actual best lap.
            ax.scatter(
                [actual], [yi], s=180, facecolors=color, edgecolors="#0d0d0d",
                linewidths=1.0, zorder=3,
            )

            # Right-margin delta text (time left on the table).
            x_text = max(theo, actual)
            ax.text(
                x_text + 0.05, yi, f"+{row['delta_s']:.3f}s",
                va="center", ha="left", fontsize=9, color="#E9E9E9",
            )

        ax.set_yticks(list(y_positions))
        ax.set_yticklabels([row["driver"] for row in rows], fontsize=10)
        ax.set_ylim(-0.6, n - 0.4)
        ax.set_xlabel("Lap time (s)")
        ax.set_title(
            "Theoretical best lap — hollow = best sectors combined, "
            "filled = actual best lap"
        )
        ax.grid(True, axis="x", alpha=0.3)

        # Legend proxies for the two marker styles.
        theo_proxy = plt.Line2D(
            [0], [0], marker="o", color="none", markerfacecolor="none",
            markeredgecolor="#E9E9E9", markeredgewidth=2, markersize=10,
            label="Theoretical (best sectors)",
        )
        actual_proxy = plt.Line2D(
            [0], [0], marker="o", color="none", markerfacecolor="#E9E9E9",
            markeredgecolor="#0d0d0d", markersize=10, label="Actual best lap",
        )
        ax.legend(handles=[theo_proxy, actual_proxy], loc="lower right", fontsize=9)

        add_legacy_watermark(fig, 575, 575, alpha=0.5, zorder=3)

        plt.suptitle(f"Theoretical best lap\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"


if __name__ == "__main__":
    logger.info("Testing V2 Theoretical Best...")
    try:
        data = TheoreticalBestData()(2024, 1, "Q")
        logger.info("Drivers: %s", len(data))
        if data:
            fastest = data[0]
            logger.info("Fastest theoretical: %s theo=%s actual=%s delta=%s",
                        fastest['driver'], fastest['theoretical_s'], fastest['actual_s'], fastest['delta_s'])
        plot_path = TheoreticalBestPlot()(2024, 1, "Q")
        logger.info("Plot: %s", plot_path)
    except Exception as ex:
        logger.error("Error: %s", ex)
        import traceback
        traceback.print_exc()
