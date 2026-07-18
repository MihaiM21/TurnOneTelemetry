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

from typing import Any, Dict, List, Union

import matplotlib.image as mpimg
import matplotlib.pyplot as plt

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._race_helpers import (
    extract_positions_by_lap,
    get_track_status_periods,
)
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme

logger = get_logger(__name__)

DATA_TYPE = "position_changes"

# Sessions where a lap-by-lap position chart is meaningful.
_VALID_SESSIONS = {"R", "RACE", "S", "SPRINT"}


def _init(y: int, event_name: str, session_name: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f"Position changes {y} {event_name} {session_name}.png"
    return location, name


def _assert_valid_session(session_name: str, year: int, identifier: Any) -> None:
    if session_name.strip().upper() not in _VALID_SESSIONS:
        raise DataNotAvailableError(
            year=year, gp=identifier, session=session_name,
            source="livetiming",
            reason=(
                "Position changes are only available for Race/Sprint sessions "
                f"(got {session_name!r})"
            ),
        )


def _build_payload(store: SessionDataStore) -> List[Dict[str, Any]]:
    """Build the position-changes payload from a resolved store.

    Returns a list ordered by finishing (end) position: each entry has
    ``driver`` (TLA), ``team``, ``color``, ``start_pos``, ``end_pos`` and
    ``positions`` (``[{lap, position}, ...]``, lap 0 = grid).
    """
    positions_by_num = extract_positions_by_lap(store)
    drivers = store.driver_list()

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
            "positions": [
                {"lap": r["lap"], "position": r["position"]} for r in records
            ],
        })

    payload.sort(key=lambda d: d["end_pos"])
    return payload


class PositionChangesData:
    """Callable: ``PositionChangesData()(year, identifier, session) -> list``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> List[Dict[str, Any]]:
        _assert_valid_session(e, y, identifier)

        def _generate() -> List[Dict[str, Any]]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=DATA_TYPE, generator=_generate, version="v2",
        )


class PositionChangesPlot:
    """Callable: ``PositionChangesPlot()(year, identifier, session) -> png path``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> str:
        _assert_valid_session(e, y, identifier)

        payload = PositionChangesData()(y, identifier, e)
        if not payload:
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No position data available to plot",
            )

        store = SessionDataStore(y, identifier, e)
        event_name = store.event_name
        periods = get_track_status_periods(store)

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

        try:
            logo = mpimg.imread('assets/images/logo mic.png')
            fig.figimage(logo, 575, 575, zorder=3, alpha=.6)
        except Exception:
            pass

        plt.suptitle(f"Position changes\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"


if __name__ == "__main__":
    print("Testing V2 Position Changes...")
    try:
        data = PositionChangesData()(2025, 1, "R")
        print(f"Drivers: {len(data)}")
        if data:
            print(f"Winner: {data[0]}")
        plot_path = PositionChangesPlot()(2025, 1, "R")
        print(f"Plot: {plot_path}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
