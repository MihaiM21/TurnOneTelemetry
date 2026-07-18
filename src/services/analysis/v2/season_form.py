"""
Season Form Guide (V2, jolpica-backed, seasonal).

Rolling-average finishing/qualifying position per driver across a season, so
a fan can see who's trending up or down. No session parameter — a full
season rollup built on top of `_season_helpers.fetch_season_results`
(reused from the teammate battle tracker).

Default driver selection is the top 10 by mean finishing position (drivers
with no race results are excluded from the ranking); a caller can narrow the
selection with the ``drivers`` param, applied AFTER the full payload is
fetched/cached so the cache stays shared across different driver filters.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import matplotlib.image as mpimg
import matplotlib.pyplot as plt

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.v2._season_helpers import (
    fetch_season_results,
    season_cached_or_generate,
)
from src.services.plotting import colors as colors_module
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme

logger = get_logger(__name__)

DATA_TYPE_PREFIX = "season_form"

_DEFAULT_WINDOW = 3
_DEFAULT_TOP_N = 10


def _init(y: int, window: int):
    dirOrg.checkForFolder(f"{y}/Season/FormGuide")
    location = f"outputs/plots/{y}/Season/FormGuide"
    name = f"Season form guide {y} w{window}.png"
    return location, name


# ----------------------------------------------------------------------
# Pure payload builders (unit-testable without network access)
# ----------------------------------------------------------------------
def _rolling_mean(values: Sequence[Optional[float]], window: int) -> List[Optional[float]]:
    """Rolling mean over the last ``window`` entries, skipping ``None``.

    Requires at least 2 non-None points within the trailing window for an
    index to produce a value; otherwise the output at that index is ``None``.
    This avoids single-point "averages" that look noisy/misleading at the
    start of a season.
    """
    out: List[Optional[float]] = []
    for i in range(len(values)):
        lo = max(0, i - window + 1)
        chunk = [v for v in values[lo:i + 1] if v is not None]
        if len(chunk) >= 2:
            out.append(sum(chunk) / len(chunk))
        else:
            out.append(None)
    return out


def _rounds_present(results: List[Dict[str, Any]]) -> List[int]:
    rounds = sorted({r["round"] for r in results if r.get("round") is not None})
    return rounds


def _driver_series(
    results: List[Dict[str, Any]], rounds: List[int], driver: str
) -> List[Optional[int]]:
    by_round = {r["round"]: r for r in results if r.get("driver_code") == driver}
    return [by_round[r]["position"] if r in by_round else None for r in rounds]


def _driver_meta(results: List[Dict[str, Any]], driver: str) -> Dict[str, Any]:
    for row in results:
        if row.get("driver_code") == driver:
            team = row.get("constructor", "")
            return {
                "tla": driver,
                "team": team,
                "color": colors_module.get_team_color(team) if team else "#777777",
            }
    return {"tla": driver, "team": "", "color": "#777777"}


def _default_top_drivers(race_results: List[Dict[str, Any]], top_n: int) -> List[str]:
    """Top-N drivers by mean finishing position (lower = better), classified
    results only. Ties broken alphabetically by driver code for determinism.
    """
    positions_by_driver: Dict[str, List[int]] = {}
    for row in race_results:
        driver = row.get("driver_code")
        pos = row.get("position")
        if not driver or pos is None or not row.get("classified"):
            continue
        positions_by_driver.setdefault(driver, []).append(pos)

    ranked = sorted(
        positions_by_driver.items(),
        key=lambda kv: (sum(kv[1]) / len(kv[1]), kv[0]),
    )
    return [driver for driver, _ in ranked[:top_n]]


def _default_top_drivers_from_payload(payload: Dict[str, Any], top_n: int) -> List[str]:
    """Same ranking as `_default_top_drivers`, computed from an already-built
    payload's ``finish`` series (avoids re-fetching raw results)."""
    positions_by_driver: Dict[str, List[int]] = {
        d["tla"]: [p for p in d["finish"] if p is not None]
        for d in payload["drivers"]
    }
    ranked = sorted(
        (
            (drv, sum(pos_list) / len(pos_list))
            for drv, pos_list in positions_by_driver.items() if pos_list
        ),
        key=lambda kv: (kv[1], kv[0]),
    )
    return [drv for drv, _ in ranked[:top_n]]


def build_payload_from_results(
    race_results: List[Dict[str, Any]],
    quali_results: List[Dict[str, Any]],
    window: int,
    selected: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Assemble the full-field season-form payload (test seam).

    ``selected`` overrides the default top-10-by-mean-finish selection; when
    omitted, all drivers with race results are considered for the top-N pick,
    but the RETURNED payload always contains every driver with any race/quali
    rows — caller-side filtering (``drivers`` query param) happens after this
    payload is cached, on the already-built ``drivers`` list.
    """
    rounds = _rounds_present(race_results + quali_results)
    all_drivers = sorted({
        r["driver_code"] for r in (race_results + quali_results) if r.get("driver_code")
    })

    drivers_payload: List[Dict[str, Any]] = []
    for driver in all_drivers:
        finish = _driver_series(race_results, rounds, driver)
        quali = _driver_series(quali_results, rounds, driver)
        meta = _driver_meta(race_results, driver) or _driver_meta(quali_results, driver)
        drivers_payload.append({
            "tla": driver,
            "team": meta["team"],
            "color": meta["color"],
            "rounds": rounds,
            "finish": finish,
            "quali": quali,
            "finish_rolling": _rolling_mean(finish, window),
            "quali_rolling": _rolling_mean(quali, window),
        })

    return {"window": window, "rounds": rounds, "drivers": drivers_payload}


def _filter_drivers(payload: Dict[str, Any], selected: List[str]) -> Dict[str, Any]:
    wanted = set(selected)
    filtered = [d for d in payload["drivers"] if d["tla"] in wanted]
    return {"window": payload["window"], "rounds": payload["rounds"], "drivers": filtered}


def _build_full_payload(year: int, window: int) -> Dict[str, Any]:
    race_results = fetch_season_results(year, "race")
    quali_results = fetch_season_results(year, "qualifying")
    return build_payload_from_results(race_results, quali_results, window)


class SeasonFormData:
    """Callable: ``SeasonFormData()(year, window=3, drivers=None) -> dict``.

    The full-field payload is cached under ``data_type=f"season_form_w{window}"``;
    an explicit ``drivers`` selection is applied AFTER cache retrieval so
    different filters share one cached payload per (year, window).
    """

    def __call__(
        self, y: int, window: int = _DEFAULT_WINDOW, drivers: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        data_type = f"{DATA_TYPE_PREFIX}_w{window}"

        def _generate() -> Dict[str, Any]:
            return _build_full_payload(y, window)

        payload = season_cached_or_generate(y, data_type, _generate)

        selected = drivers
        if not selected:
            selected = _default_top_drivers_from_payload(payload, _DEFAULT_TOP_N)

        return _filter_drivers(payload, selected)


class SeasonFormPlot:
    """Callable: ``SeasonFormPlot()(year, window=3, drivers=None) -> png path``."""

    def __call__(
        self, y: int, window: int = _DEFAULT_WINDOW, drivers: Optional[List[str]] = None
    ) -> str:
        payload = SeasonFormData()(y, window, drivers)
        if not payload.get("drivers"):
            raise DataNotAvailableError(
                year=y, gp="season", session="Season", source="jolpica",
                reason="No season form data available",
            )
        return self._render(payload, y)

    @staticmethod
    def _render(payload: Dict[str, Any], y: int) -> str:
        setup_theme.setup_turnone_theme()
        window = payload["window"]
        location, name = _init(y, window)

        rounds = payload["rounds"]
        drivers_payload = payload["drivers"]

        fig, (ax_race, ax_quali) = plt.subplots(
            2, 1, figsize=(14, 10), sharex=True, layout='constrained'
        )

        seen_teams: Dict[str, int] = {}
        for d in drivers_payload:
            team = d["team"]
            seen_teams[team] = seen_teams.get(team, 0) + 1

        for d in drivers_payload:
            team = d["team"]
            is_teammate_2 = seen_teams.get(team, 0) > 1 and d is not next(
                x for x in drivers_payload if x["team"] == team
            )
            linestyle = "--" if is_teammate_2 else "-"

            SeasonFormPlot._render_panel(
                ax_race, rounds, d["finish"], d["finish_rolling"], d["color"],
                d["tla"], linestyle,
            )
            SeasonFormPlot._render_panel(
                ax_quali, rounds, d["quali"], d["quali_rolling"], d["color"],
                d["tla"], linestyle,
            )

        ax_race.set_title("Race form")
        ax_race.set_ylabel("Finish position")
        ax_race.invert_yaxis()
        ax_race.set_yticks(range(1, 21))
        ax_race.set_yticklabels([f"P{i}" for i in range(1, 21)], fontsize=8)

        ax_quali.set_title("Qualifying form")
        ax_quali.set_ylabel("Quali position")
        ax_quali.set_xlabel("Round")
        ax_quali.invert_yaxis()
        ax_quali.set_yticks(range(1, 21))
        ax_quali.set_yticklabels([f"P{i}" for i in range(1, 21)], fontsize=8)

        try:
            logo = mpimg.imread('assets/images/logo mic.png')
            fig.figimage(logo, 575, 575, zorder=3, alpha=.5)
        except Exception:
            pass

        plt.suptitle(f"{y} Form Guide (rolling {window}-race average)")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"

    @staticmethod
    def _render_panel(ax, rounds, raw, rolling, color, tla, linestyle):
        # Faint raw markers.
        xs_raw = [r for r, v in zip(rounds, raw) if v is not None]
        ys_raw = [v for v in raw if v is not None]
        if xs_raw:
            ax.scatter(xs_raw, ys_raw, color=color, alpha=0.25, s=18, zorder=2)

        # Rolling-mean line, thick, team-colored (teammate #2 dashed).
        xs_roll = [r for r, v in zip(rounds, rolling) if v is not None]
        ys_roll = [v for v in rolling if v is not None]
        if xs_roll:
            ax.plot(
                xs_roll, ys_roll, color=color, linewidth=2.6,
                linestyle=linestyle, zorder=3, solid_capstyle="round",
            )
            ax.annotate(
                tla, xy=(xs_roll[-1], ys_roll[-1]),
                xytext=(6, 0), textcoords="offset points",
                fontsize=9, fontweight="bold", color=color,
                va="center", zorder=4,
            )


if __name__ == "__main__":
    print("Testing V2 Season Form...")
    try:
        data = SeasonFormData()(2025)
        print(f"Drivers: {len(data['drivers'])}")
        print(f"Rounds: {data['rounds']}")
        plot_path = SeasonFormPlot()(2025)
        print(f"Plot: {plot_path}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
