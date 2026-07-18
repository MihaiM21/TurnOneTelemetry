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

from typing import Any, Dict, List, Union

import matplotlib.image as mpimg
import matplotlib.pyplot as plt

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._race_helpers import extract_best_sectors, extract_lap_times
from src.services.analysis.v2.session_store import SessionDataStore
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


def _assert_valid_session(session_name: str, year: int, identifier: Any) -> None:
    if session_name.strip().upper() not in _VALID_SESSIONS:
        raise DataNotAvailableError(
            year=year, gp=identifier, session=session_name,
            source="livetiming",
            reason=(
                "Theoretical best lap is only available for Qualifying sessions "
                f"(got {session_name!r})"
            ),
        )


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
        _assert_valid_session(e, y, identifier)

        def _generate() -> List[Dict[str, Any]]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=DATA_TYPE, generator=_generate, version="v2",
        )


class TheoreticalBestPlot:
    """Callable: ``TheoreticalBestPlot()(year, identifier, session) -> png path``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> str:
        _assert_valid_session(e, y, identifier)

        payload = TheoreticalBestData()(y, identifier, e)
        if not payload:
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No sector-time data available to plot theoretical best lap",
            )

        store = SessionDataStore(y, identifier, e)
        event_name = store.event_name

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

        try:
            logo = mpimg.imread('assets/images/logo mic.png')
            fig.figimage(logo, 575, 575, zorder=3, alpha=.5)
        except Exception:
            pass

        plt.suptitle(f"Theoretical best lap\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"


if __name__ == "__main__":
    print("Testing V2 Theoretical Best...")
    try:
        data = TheoreticalBestData()(2024, 1, "Q")
        print(f"Drivers: {len(data)}")
        if data:
            fastest = data[0]
            print(f"Fastest theoretical: {fastest['driver']} "
                  f"theo={fastest['theoretical_s']} actual={fastest['actual_s']} "
                  f"delta={fastest['delta_s']}")
        plot_path = TheoreticalBestPlot()(2024, 1, "Q")
        print(f"Plot: {plot_path}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
