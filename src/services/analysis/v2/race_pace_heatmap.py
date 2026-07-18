"""
Race Pace Heatmap (V2, livetiming-backed).

Driver x lap grid of each lap's delta to the field median lap time for that
lap, rendered as a dark-themed heatmap. This is a fast, zero-new-helpers
feature: everything it needs already exists in ``_race_helpers``.

Payload shape:
  * ``drivers``    — TLAs in finish order (row order, winner first).
  * ``laps``       — sorted list of lap numbers forming the grid columns.
  * ``grid``       — ``{tla: [delta_s_or_None, ...]}`` aligned to ``laps``;
                     ``None`` marks a missing/retired lap (masked cell).
  * ``pit_laps``   — ``{tla: [lap, ...]}`` laps where the driver pitted
                     (in or out), used to stamp a "P" marker on the plot.
  * ``sc_laps``    — sorted list of laps under SC/VSC/RED, used for masking.

Methodology:
  * The field median for a given lap excludes pit-in/pit-out laps (those are
    slow for reasons unrelated to pace) so the comparison stays meaningful.
  * Rows are ordered by finishing position (last recorded position), with
    retirees keeping their last known spot and cars with no position data
    pushed to the bottom.
  * Safety Car / VSC / Red-flag laps are masked out entirely (not just
    colored) since lap times under those conditions aren't representative of
    race pace.

Only meaningful for Race / Sprint sessions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import TwoSlopeNorm

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._race_helpers import (
    extract_lap_times,
    extract_positions_by_lap,
    get_track_status_periods,
)
from src.services.analysis.v2.session_store import SessionDataStore
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme

logger = get_logger(__name__)

DATA_TYPE = "race_pace_heatmap"

# Sessions where lap-by-lap race pace is meaningful (racing, not single-lap).
_VALID_SESSIONS = {"R", "RACE", "S", "SPRINT"}

# Track statuses whose laps are masked out of the heatmap entirely.
_MASKED_STATUSES = {"SC", "VSC", "RED"}

# Color-scale bounds: capped so a handful of outlier laps (spins, red flags
# that slip past masking) don't wash out the green/yellow/red contrast in the
# midfield pack, which is where the story usually is.
_VMIN = -2.0
_VMAX = 2.5
_MASK_COLOR = "#2a2a2a"


def _init(y: int, event_name: str, session_name: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f"Race pace heatmap {y} {event_name} {session_name}.png"
    return location, name


def _assert_valid_session(session_name: str, year: int, identifier: Any) -> None:
    if session_name.strip().upper() not in _VALID_SESSIONS:
        raise DataNotAvailableError(
            year=year, gp=identifier, session=session_name,
            source="livetiming",
            reason=(
                "Race pace heatmap is only available for Race/Sprint sessions "
                f"(got {session_name!r})"
            ),
        )


# ----------------------------------------------------------------------
# Pure payload builders (unit-testable without a live store)
# ----------------------------------------------------------------------
def _sc_laps_from_periods(periods: List[Dict[str, Any]], max_lap: int) -> List[int]:
    """Sorted list of laps that fall inside any SC/VSC/RED period."""
    laps: set = set()
    for p in periods:
        if p.get("status") not in _MASKED_STATUSES:
            continue
        start = p.get("start_lap")
        if start is None:
            continue
        end = p.get("end_lap")
        if end is None:
            end = max_lap
        for lap in range(int(start), int(end) + 1):
            laps.add(lap)
    return sorted(laps)


def _pit_laps_by_driver(lap_times: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[int]]:
    """``{tla_or_num: [lap, ...]}`` for laps flagged pit_in or pit_out."""
    result: Dict[str, List[int]] = {}
    for num, records in lap_times.items():
        laps = sorted(
            rec["lap"] for rec in records if rec.get("pit_in") or rec.get("pit_out")
        )
        if laps:
            result[num] = laps
    return result


def _field_median_by_lap(
    lap_times: Dict[str, List[Dict[str, Any]]]
) -> Dict[int, float]:
    """Median lap time per lap, excluding pit-in/pit-out laps and invalid times."""
    by_lap: Dict[int, List[float]] = {}
    for records in lap_times.values():
        for rec in records:
            if rec.get("pit_in") or rec.get("pit_out"):
                continue
            t = rec.get("time_s")
            if not t or t <= 0:
                continue
            by_lap.setdefault(rec["lap"], []).append(t)
    return {lap: float(np.median(times)) for lap, times in by_lap.items() if times}


def _finish_order(
    drivers: Dict[str, Dict[str, Any]],
    positions_by_lap: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    """Car numbers ordered by last-known race position (winner first).

    Drivers with no position data are pushed to the bottom, in TLA order for
    determinism.
    """
    last_pos: Dict[str, int] = {}
    for num, records in positions_by_lap.items():
        if records:
            last_pos[num] = records[-1]["position"]

    def _key(num: str):
        pos = last_pos.get(num)
        tla = drivers.get(num, {}).get("tla", num)
        if pos is None:
            return (1, 9999, tla)
        return (0, pos, tla)

    return sorted(drivers.keys(), key=_key)


def build_payload_from_parts(
    lap_times: Dict[str, List[Dict[str, Any]]],
    positions_by_lap: Dict[str, List[Dict[str, Any]]],
    periods: List[Dict[str, Any]],
    drivers: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Assemble the full payload from already-extracted parts (test seam)."""
    max_lap = max(
        (rec["lap"] for records in lap_times.values() for rec in records),
        default=0,
    )
    median_by_lap = _field_median_by_lap(lap_times)
    sc_laps = _sc_laps_from_periods(periods, max_lap)
    sc_lap_set = set(sc_laps)
    pit_laps = _pit_laps_by_driver(lap_times)

    laps = sorted(median_by_lap.keys())
    order = _finish_order(drivers, positions_by_lap)
    tla_order = [drivers.get(num, {}).get("tla", num) for num in order]

    grid: Dict[str, List[Optional[float]]] = {}
    time_by_num_lap: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for num, records in lap_times.items():
        time_by_num_lap[num] = {rec["lap"]: rec for rec in records}

    for num, tla in zip(order, tla_order):
        by_lap = time_by_num_lap.get(num, {})
        row: List[Optional[float]] = []
        for lap in laps:
            rec = by_lap.get(lap)
            if rec is None or lap in sc_lap_set:
                row.append(None)
                continue
            if rec.get("pit_in") or rec.get("pit_out"):
                row.append(None)
                continue
            t = rec.get("time_s")
            median = median_by_lap.get(lap)
            if not t or t <= 0 or median is None:
                row.append(None)
                continue
            row.append(round(t - median, 3))
        grid[tla] = row

    pit_laps_tla = {
        drivers.get(num, {}).get("tla", num): laps_
        for num, laps_ in pit_laps.items()
    }

    return {
        "drivers": tla_order,
        "laps": laps,
        "grid": grid,
        "pit_laps": pit_laps_tla,
        "sc_laps": sc_laps,
    }


def _build_payload(store: SessionDataStore) -> Dict[str, Any]:
    """Build the race-pace-heatmap payload from a resolved store."""
    drivers = store.driver_list()
    lap_times = extract_lap_times(store)
    positions_by_lap = extract_positions_by_lap(store)
    periods = get_track_status_periods(store)
    return build_payload_from_parts(lap_times, positions_by_lap, periods, drivers)


class RacePaceHeatmapData:
    """Callable: ``RacePaceHeatmapData()(year, identifier, session) -> dict``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> Dict[str, Any]:
        _assert_valid_session(e, y, identifier)

        def _generate() -> Dict[str, Any]:
            store = SessionDataStore(y, identifier, e)
            return _build_payload(store)

        return cached_or_generate(
            year=y, identifier=identifier, session=e,
            data_type=DATA_TYPE, generator=_generate, version="v2",
        )


class RacePaceHeatmapPlot:
    """Callable: ``RacePaceHeatmapPlot()(year, identifier, session) -> png path``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> str:
        _assert_valid_session(e, y, identifier)

        payload = RacePaceHeatmapData()(y, identifier, e)
        if not payload.get("laps") or not payload.get("drivers"):
            raise DataNotAvailableError(
                year=y, gp=identifier, session=e, source="livetiming",
                reason="No lap-time data available to plot",
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
        laps = payload["laps"]
        grid = payload["grid"]
        pit_laps = payload.get("pit_laps", {})

        n_drivers = len(drivers)
        n_laps = len(laps)

        # Build a masked array: rows top-to-bottom follow finish order, so we
        # flip vertically for pcolormesh (which draws row 0 at the bottom).
        data = np.full((n_drivers, n_laps), np.nan)
        for i, tla in enumerate(drivers):
            row = grid.get(tla, [])
            for j, val in enumerate(row):
                if val is not None:
                    data[i, j] = val
        data = np.flipud(data)
        masked = np.ma.masked_invalid(data)

        fig, ax = plt.subplots(figsize=(16, 9), layout='constrained')

        cmap = plt.get_cmap("RdYlGn_r").copy()
        cmap.set_bad(_MASK_COLOR)
        norm = TwoSlopeNorm(vcenter=0.0, vmin=_VMIN, vmax=_VMAX)

        mesh = ax.pcolormesh(
            np.arange(n_laps + 1), np.arange(n_drivers + 1), masked,
            cmap=cmap, norm=norm, edgecolors="#0d0d0d", linewidth=0.4,
        )

        # Pit-lap "P" annotations.
        flipped_drivers = list(reversed(drivers))
        lap_index = {lap: idx for idx, lap in enumerate(laps)}
        for i, tla in enumerate(flipped_drivers):
            for lap in pit_laps.get(tla, []):
                j = lap_index.get(lap)
                if j is None:
                    continue
                ax.text(
                    j + 0.5, i + 0.5, "P",
                    ha="center", va="center", fontsize=8, fontweight="bold",
                    color="white", zorder=5,
                )

        # X ticks: lap numbers, sparse if there are many laps.
        step = max(1, n_laps // 30)
        xticks = list(range(0, n_laps, step))
        ax.set_xticks([x + 0.5 for x in xticks])
        ax.set_xticklabels([str(laps[x]) for x in xticks], fontsize=8)
        ax.set_xlabel("Lap")

        # Y ticks: TLA, winner at the top.
        ax.set_yticks([i + 0.5 for i in range(n_drivers)])
        ax.set_yticklabels(flipped_drivers, fontsize=9)
        ax.set_ylabel("Driver (finish order)")

        ax.set_title("Race pace vs field median lap time")

        cbar = fig.colorbar(mesh, ax=ax, pad=0.01)
        cbar.set_label("Δ to field median lap (s)")

        try:
            logo = mpimg.imread('assets/images/logo mic.png')
            fig.figimage(logo, 575, 575, zorder=3, alpha=.5)
        except Exception:
            pass

        plt.suptitle(f"Race pace heatmap\n{y} {event_name} {e}")
        plt.savefig(f"{location}/{name}")
        plt.close(fig)
        return f"{location}/{name}"


if __name__ == "__main__":
    print("Testing V2 Race Pace Heatmap...")
    try:
        data = RacePaceHeatmapData()(2025, 1, "R")
        print(f"Drivers: {len(data['drivers'])}")
        print(f"Laps: {len(data['laps'])}")
        print(f"SC laps: {data['sc_laps']}")
        plot_path = RacePaceHeatmapPlot()(2025, 1, "R")
        print(f"Plot: {plot_path}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
