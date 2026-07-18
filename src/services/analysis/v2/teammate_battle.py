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

import matplotlib.image as mpimg
import matplotlib.pyplot as plt

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.v2._season_helpers import (
    fetch_season_results,
    pair_teammates,
    season_cached_or_generate,
)
from src.services.plotting import colors as colors_module
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme

logger = get_logger(__name__)

DATA_TYPE = "teammate_battle"

# Deepest-first quali segment preference for the "common segment" gap.
_QUALI_SEGMENTS = ("q3_s", "q2_s", "q1_s")


def _init(y: int):
    dirOrg.checkForFolder(f"{y}/Season/TeammateBattle")
    location = f"outputs/plots/{y}/Season/TeammateBattle"
    name = f"Teammate battle {y}.png"
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


class TeammateBattlePlot:
    """Callable: ``TeammateBattlePlot()(year) -> png path``."""

    def __call__(self, y: int) -> str:
        payload = TeammateBattleData()(y)
        if not payload.get("teams"):
            raise DataNotAvailableError(
                year=y, gp="season", session="Season", source="jolpica",
                reason="No teammate battle data available for this season",
            )
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

        try:
            logo = mpimg.imread('assets/images/logo mic.png')
            fig.figimage(logo, 575, 575, zorder=3, alpha=.5)
        except Exception:
            pass

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
    print("Testing V2 Teammate Battle...")
    try:
        data = TeammateBattleData()(2025)
        print(f"Teams: {len(data['teams'])}")
        for t in data["teams"]:
            print(t)
        plot_path = TeammateBattlePlot()(2025)
        print(f"Plot: {plot_path}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
