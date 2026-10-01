"""
Teammate Battle Tracker (V2, jolpica-backed, seasonal).

Per-constructor head-to-head between the two drivers who shared a seat for
most of the season (mid-season swaps handled by `_season_helpers.pair_teammates`,
which keeps only the two most-frequent drivers and skips rounds where either
is absent).

For every team:
  * ``quali_h2h``     — [wins_a, wins_b] counted every round both drivers set
                        a quali time (any Q1/Q2/Q3 segment recorded).
  * ``race_h2h``      — [wins_a, wins_b] counted only on rounds where BOTH
                        drivers were classified finishers (DNFs excluded so a
                        mechanical failure doesn't count as "losing").
  * ``avg_quali_gap_s`` — mean of the per-round gap (driver_b - driver_a) taken
                        from the deepest common quali segment both drivers
                        reached that round (Q3 preferred, then Q2, then Q1).
                        Positive means driver_a was faster on average.
  * ``rounds_counted`` — number of rounds contributing to the quali gap.

Teams are listed alphabetically. No session parameter — this is a full-season
rollup built from `fetch_season_results`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.v2._season_helpers import (
    fetch_season_results,
    pair_teammates,
    season_cached_or_generate,
)
from src.services.plotting import canvas as cv
from src.services.plotting.canvas import add_legacy_watermark
from src.services.plotting import colors as colors_module
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme

logger = get_logger(__name__)

DATA_TYPE = "teammate_battle"

# Deepest-first quali segment preference for the "common segment" gap.
_QUALI_SEGMENTS = ("q3_s", "q2_s", "q1_s")


def _init(y: int, suffix: str = ""):
    dirOrg.checkForFolder(f"{y}/Season/TeammateBattle")
    location = f"outputs/plots/{y}/Season/TeammateBattle"
    name = f"Teammate battle {y}{suffix}.png"
    return location, name


# ----------------------------------------------------------------------
# Pure payload builders (unit-testable without network access)
# ----------------------------------------------------------------------
def _rows_by_round(results: List[Dict[str, Any]]) -> Dict[int, Dict[str, Dict[str, Any]]]:
    """``{round: {driver_code: row}}`` for quick per-round lookup."""
    by_round: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for row in results:
        rnd = row.get("round")
        driver = row.get("driver_code")
        if rnd is None or not driver:
            continue
        by_round.setdefault(rnd, {})[driver] = row
    return by_round


def _quali_h2h(
    quali_by_round: Dict[int, Dict[str, Dict[str, Any]]], driver_a: str, driver_b: str
) -> List[int]:
    wins = [0, 0]
    for rows in quali_by_round.values():
        row_a = rows.get(driver_a)
        row_b = rows.get(driver_b)
        if row_a is None or row_b is None:
            continue
        pos_a, pos_b = row_a.get("position"), row_b.get("position")
        if pos_a is None or pos_b is None:
            continue
        if pos_a < pos_b:
            wins[0] += 1
        elif pos_b < pos_a:
            wins[1] += 1
    return wins


def _race_h2h(
    race_by_round: Dict[int, Dict[str, Dict[str, Any]]], driver_a: str, driver_b: str
) -> List[int]:
    wins = [0, 0]
    for rows in race_by_round.values():
        row_a = rows.get(driver_a)
        row_b = rows.get(driver_b)
        if row_a is None or row_b is None:
            continue
        if not row_a.get("classified") or not row_b.get("classified"):
            continue
        pos_a, pos_b = row_a.get("position"), row_b.get("position")
        if pos_a is None or pos_b is None:
            continue
        if pos_a < pos_b:
            wins[0] += 1
        elif pos_b < pos_a:
            wins[1] += 1
    return wins


def _deepest_common_gap(row_a: Dict[str, Any], row_b: Dict[str, Any]) -> Optional[float]:
    """Gap (b - a) from the deepest quali segment both drivers reached.

    Tries Q3 first, then Q2, then Q1 — the first segment where BOTH rows have
    a non-None time. Positive means driver_a was faster.
    """
    for segment in _QUALI_SEGMENTS:
        t_a = row_a.get(segment)
        t_b = row_b.get(segment)
        if t_a is not None and t_b is not None:
            return t_b - t_a
    return None


def _avg_quali_gap(
    quali_by_round: Dict[int, Dict[str, Dict[str, Any]]], driver_a: str, driver_b: str
) -> Tuple[Optional[float], int]:
    gaps: List[float] = []
    for rows in quali_by_round.values():
        row_a = rows.get(driver_a)
        row_b = rows.get(driver_b)
        if row_a is None or row_b is None:
            continue
        gap = _deepest_common_gap(row_a, row_b)
        if gap is not None:
            gaps.append(gap)
    if not gaps:
        return None, 0
    return sum(gaps) / len(gaps), len(gaps)


def _driver_name(results_by_round: Dict[int, Dict[str, Dict[str, Any]]], driver: str) -> str:
    for rows in results_by_round.values():
        row = rows.get(driver)
        if row is not None:
            return row.get("driver_name") or driver
    return driver


def build_payload_from_results(
    race_results: List[Dict[str, Any]], quali_results: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Assemble the teammate-battle payload from already-fetched season rows.

    Test seam: takes plain lists of normalized rows (see `_season_helpers`
    schema), no network/cache involved.
    """
    pairs = pair_teammates(race_results)
    race_by_round = _rows_by_round(race_results)
    quali_by_round = _rows_by_round(quali_results)

    records: List[Dict[str, Any]] = []
    for team in sorted(pairs.keys()):
        driver_a, driver_b = pairs[team]
        quali_h2h = _quali_h2h(quali_by_round, driver_a, driver_b)
        race_h2h = _race_h2h(race_by_round, driver_a, driver_b)
        avg_gap, rounds_counted = _avg_quali_gap(quali_by_round, driver_a, driver_b)

        records.append({
            "team": team,
            "color": colors_module.get_team_color(team),
            "driver_a": driver_a,
            "driver_b": driver_b,
            "quali_h2h": quali_h2h,
            "race_h2h": race_h2h,
            "avg_quali_gap_s": round(avg_gap, 3) if avg_gap is not None else None,
            "rounds_counted": rounds_counted,
        })

    return {"teams": records}


def _build_payload(year: int) -> Dict[str, Any]:
    race_results = fetch_season_results(year, "race")
    quali_results = fetch_season_results(year, "qualifying")
    return build_payload_from_results(race_results, quali_results)


class TeammateBattleData:
    """Callable: ``TeammateBattleData()(year) -> dict``."""

    def __call__(self, y: int) -> Dict[str, Any]:
        def _generate() -> Dict[str, Any]:
            return _build_payload(y)

        return season_cached_or_generate(y, DATA_TYPE, _generate)


# ----------------------------------------------------------------------
# Social-format render (numbers and labels only; see services/plotting/canvas.py)
# ----------------------------------------------------------------------
_MUTED = cv.MUTED
_UNKNOWN = "#777777"


def _pt_px(size_pt: float) -> float:
    return size_pt * cv.DESIGN_DPI / 72.0


def _legible(color: str, floor: float = 0.42) -> str:
    """Lift a colour that would vanish on the dark canvas (Cadillac's near-black)."""
    try:
        rgb = mcolors.to_rgb(color)
    except ValueError:
        return _UNKNOWN
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    if lum >= floor:
        return mcolors.to_hex(rgb)
    mix = (floor - lum) / (1.0 - lum)
    return mcolors.to_hex(tuple(c + (1.0 - c) * mix for c in rgb))


def _team_color(team: Dict[str, Any], y: int) -> str:
    """Year-aware team colour; a name the palette does not know falls back to the driver's colour."""
    color = colors_module.get_team_color(team["team"], y)
    if color.upper() == "#FFFFFF":
        color = colors_module.get_driver_color(team["driver_a"], y)
    if color.upper() == "#FFFFFF":
        color = team.get("color") or _UNKNOWN
    return _legible(color)


def _driver_color(code: str, y: int, fallback: str) -> str:
    color = colors_module.get_driver_color(code, y)
    return _legible(fallback if color.upper() == "#FFFFFF" else color)


def _fmt_gap(team: Dict[str, Any]):
    """``(text, faster_code)`` for the average qualifying gap, ``('-', None)`` when unknown."""
    gap = team.get("avg_quali_gap_s")
    if gap is None:
        return "–", None
    faster = team["driver_a"] if gap > 0 else team["driver_b"]
    return f"{faster} +{abs(gap):.2f}s", faster


def _draw_team_row(ax, fmt: cv.CanvasFormat, team: Dict[str, Any], y: int, top: float, row_h: float,
                   width: float, scale: float, stacked: bool) -> None:
    """One team: stripe, name, A|B mirrored bars (quali over race), TLAs and the quali gap."""
    fs = fmt.base_fontsize
    name_size, num_size, tla_size = fs * 0.85, fs * 0.8, fs * 0.9
    color = _team_color(team, y)
    color_a = _driver_color(team["driver_a"], y, color)
    color_b = _driver_color(team["driver_b"], y, color)
    pad = 0.12 * row_h
    inner = row_h - 2 * pad

    ax.add_patch(plt.Rectangle((0, top + 1), width, row_h - 2, color=color, alpha=0.07, lw=0, zorder=0))
    ax.add_patch(plt.Rectangle((0, top + pad), 6, inner, color=color, lw=0, zorder=2))

    gap_text, faster = _fmt_gap(team)
    gap_color = (color_a if faster == team["driver_a"] else color_b) if faster else _MUTED
    tla_w = 3 * 0.85 * _pt_px(tla_size)
    num_w = 2 * 0.7 * _pt_px(num_size) + 8

    if stacked:
        line_h = min(1.6 * _pt_px(name_size), 0.36 * inner)
        ax.text(16, top + pad + line_h / 2, team["team"], ha="left", va="center", fontsize=name_size,
                color=cv.TEXT, fontweight="semibold")
        ax.text(width, top + pad + line_h / 2, gap_text, ha="right", va="center", fontsize=name_size,
                color=gap_color, fontweight="bold")
        bars_top, bars_h = top + pad + line_h, inner - line_h
        left, right = 16, width
    else:
        ax.text(16, top + row_h / 2, team["team"], ha="left", va="center", fontsize=name_size,
                color=cv.TEXT, fontweight="semibold")
        ax.text(width, top + row_h / 2, gap_text, ha="right", va="center", fontsize=name_size,
                color=gap_color, fontweight="bold")
        bars_top, bars_h = top + pad, inner
        left, right = 0.25 * width, width - 0.19 * width

    cx = (left + right) / 2.0
    ax.text(left + tla_w / 2, bars_top + bars_h / 2, team["driver_a"], ha="center", va="center",
            fontsize=tla_size, color=color_a, fontweight="bold")
    ax.text(right - tla_w / 2, bars_top + bars_h / 2, team["driver_b"], ha="center", va="center",
            fontsize=tla_size, color=color_b, fontweight="bold")
    mid_gap = 1.1 * _pt_px(fs * 0.7)  # holds the Q / R keys
    half = (right - left) / 2.0 - tla_w - num_w - mid_gap - 10
    bar_h = min(bars_h / 2.0 - 3, 1.5 * _pt_px(num_size))

    for j, (key, letter, alpha) in enumerate((("quali_h2h", "Q", 0.95), ("race_h2h", "R", 0.6))):
        wins_a, wins_b = team[key]
        yc = bars_top + bars_h * (0.25 + 0.5 * j)
        ax.text(cx, yc, letter, ha="center", va="center", fontsize=fs * 0.7, color=_MUTED,
                fontweight="semibold")
        for wins, sign, col in ((wins_a, -1, color_a), (wins_b, 1, color_b)):
            length = wins * scale * half
            start = cx + sign * mid_gap
            if length > 0:
                ax.barh(yc, sign * length, left=start, height=bar_h, color=col, alpha=alpha, lw=0, zorder=3)
            ax.text(start + sign * (length + 5), yc, str(wins), ha="left" if sign > 0 else "right",
                    va="center", fontsize=num_size, color=cv.TEXT, fontweight="bold", zorder=4)


def _render_formatted(payload: Dict[str, Any], y: int, fmt_name: str) -> str:
    """Recompose the season head-to-head for a social format: one mirrored-bar row per team."""
    fmt = cv.get_format(fmt_name)
    location, name = _init(y, cv.format_suffix(fmt.name))
    teams = payload.get("teams", [])
    fig = cv.new_canvas(fmt)
    cv.add_header(fig, fmt, "Teammate battles", f"{y} season")
    cv.add_footer(fig, fmt)
    cv.add_watermark(fig, fmt)

    gs = cv.safe_gridspec(fig, fmt, 1, 1)
    gs.update(left=fmt.safe[0], bottom=fmt.safe[1] + 2.6 * _pt_px(fmt.base_fontsize * 0.8) / fmt.height_px)
    ax = fig.add_subplot(gs[0])
    width_px = ax.get_position().width * fmt.width_px
    height_px = ax.get_position().height * fmt.height_px
    ax.set_axis_off()
    ax.set_xlim(0, width_px)
    ax.set_ylim(height_px, 0)

    fs = fmt.base_fontsize
    head = 1.9 * _pt_px(fs * 0.72)
    ax.text(width_px / 2, 0, "Q = QUALI    R = RACE", ha="center", va="top", fontsize=fs * 0.72,
            color=_MUTED, fontweight="semibold")
    ax.text(width_px, 0, "AVG QUALI GAP", ha="right", va="top", fontsize=fs * 0.72, color=_MUTED,
            fontweight="semibold")

    max_row = 3.6 * _pt_px(fs * 0.85) * (1.6 if fmt.is_vertical else 1.0)
    row_h = min((height_px - head) / max(len(teams), 1), max_row)
    peak = max([max(t["quali_h2h"] + t["race_h2h"], default=0) for t in teams] + [1])
    stacked = fmt.name in ("portrait", "story")
    for i, team in enumerate(teams):
        _draw_team_row(ax, fmt, team, y, head + i * row_h, row_h, width_px, 1.0 / peak, stacked)
    return cv.save_png(fig, f"{location}/{name}", fmt)


class TeammateBattlePlot:
    """Callable: ``TeammateBattlePlot()(year, fmt=None) -> png path``.

    ``fmt`` (one of :data:`canvas.FORMAT_NAMES`) recomposes the same payload for a
    social format; ``None`` keeps the original figure and file name.
    """

    def __call__(self, y: int, fmt: Optional[str] = None) -> str:
        if fmt is not None:
            cv.get_format(fmt)  # ValueError before any data is fetched
        payload = TeammateBattleData()(y)
        if not payload.get("teams"):
            raise DataNotAvailableError(
                year=y, gp="season", session="Season", source="jolpica",
                reason="No teammate battle data available for this season",
            )
        if fmt is not None:
            return _render_formatted(payload, y, fmt)
        return self._render(payload, y)

    @staticmethod
    def _render(payload: Dict[str, Any], y: int) -> str:
        setup_theme.setup_turnone_theme()
        location, name = _init(y)

        teams = payload.get("teams", [])
        n = len(teams)

        fig, ax = plt.subplots(figsize=(13, 13), layout='constrained')

        band_h = 1.0
        max_h2h = max(
            [max(t["quali_h2h"] + t["race_h2h"], default=0) for t in teams] + [1]
        )
        bar_scale = 0.42 / max(max_h2h, 1)

        for i, team in enumerate(teams):
            yc = n - i  # top-to-bottom
            color = team.get("color") or "#777777"

            # Team-color accent stripe on the left edge of the band.
            ax.axhspan(yc - band_h / 2, yc + band_h / 2, color=color, alpha=0.06, zorder=0)
            ax.axhline(yc - band_h / 2, color="#2a2a2a", linewidth=0.8, zorder=1)
            ax.add_patch(plt.Rectangle(
                (-1.0, yc - band_h / 2), 0.02, band_h,
                transform=ax.get_yaxis_transform(), clip_on=False,
                color=color, zorder=2,
            ))

            wins_a_q, wins_b_q = team["quali_h2h"]
            wins_a_r, wins_b_r = team["race_h2h"]

            # Quali bar (top half of the band).
            TeammateBattlePlot._draw_diverging_bar(
                ax, yc + 0.22, wins_a_q, wins_b_q, bar_scale, height=0.28,
                color_a=color, color_b=color, alpha=0.85,
            )
            # Race bar (bottom half of the band).
            TeammateBattlePlot._draw_diverging_bar(
                ax, yc - 0.22, wins_a_r, wins_b_r, bar_scale, height=0.28,
                color_a=color, color_b=color, alpha=0.55,
            )

            # TLA labels on each side.
            ax.text(
                -1.05, yc, team["driver_a"], ha="right", va="center",
                fontsize=13, fontweight="bold", color="white",
            )
            ax.text(
                1.05, yc, team["driver_b"], ha="left", va="center",
                fontsize=13, fontweight="bold", color="white",
            )

            # Center gap text.
            gap = team.get("avg_quali_gap_s")
            if gap is not None:
                faster = team["driver_a"] if gap > 0 else team["driver_b"]
                center_txt = f"{faster} +{abs(gap):.2f}s avg"
            else:
                center_txt = "n/a"
            ax.text(
                0, yc, center_txt, ha="center", va="center",
                fontsize=9.5, color="#cfcfcf", zorder=6,
                bbox=dict(
                    boxstyle="round,pad=0.25", facecolor="#0d0d0d",
                    edgecolor="#2a2a2a", linewidth=0.8,
                ),
            )

            ax.text(
                0, yc + 0.42, team["team"], ha="center", va="bottom",
                fontsize=9, color="#888888", zorder=6,
            )

        ax.set_xlim(-1.3, 1.3)
        ax.set_ylim(0.3, n + 0.7)
        ax.axvline(0, color="#444444", linewidth=1.0, zorder=1)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

        add_legacy_watermark(fig, 575, 575, alpha=0.5, zorder=3)

        plt.suptitle(f"{y} Teammate Battles")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"

    @staticmethod
    def _draw_diverging_bar(ax, yc, wins_a, wins_b, scale, height, color_a, color_b, alpha):
        """Centered diverging bar: driver_a extends left, driver_b extends right."""
        len_a = wins_a * scale
        len_b = wins_b * scale

        if len_a > 0:
            ax.barh(yc, -len_a, height=height, left=0, color=color_a,
                    alpha=alpha, edgecolor="#0d0d0d", linewidth=0.8, zorder=4)
            ax.text(-len_a / 2 if len_a > 0.08 else -0.05, yc, str(wins_a),
                    ha="center", va="center", fontsize=9, color="#0d0d0d",
                    fontweight="bold", zorder=5)
        if len_b > 0:
            ax.barh(yc, len_b, height=height, left=0, color=color_b,
                    alpha=alpha, edgecolor="#0d0d0d", linewidth=0.8, zorder=4)
            ax.text(len_b / 2 if len_b > 0.08 else 0.05, yc, str(wins_b),
                    ha="center", va="center", fontsize=9, color="#0d0d0d",
                    fontweight="bold", zorder=5)


if __name__ == "__main__":
    logger.info("Testing V2 Teammate Battle...")
    try:
        data = TeammateBattleData()(2025)
        logger.info("Teams: %s", len(data['teams']))
        for t in data["teams"]:
            logger.info("%s", t)
        plot_path = TeammateBattlePlot()(2025)
        logger.info("Plot: %s", plot_path)
    except Exception as ex:
        logger.error("Error: %s", ex)
        import traceback
        traceback.print_exc()
