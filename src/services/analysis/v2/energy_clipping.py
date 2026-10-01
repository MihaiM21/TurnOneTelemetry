"""
Energy clipping (V2, livetiming-backed, 2026+ power units).

For every driver's fastest clean lap, finds the stretches where speed falls at
full throttle -- the car running out of deployable electrical energy -- and
measures how long they are, how much speed they cost and how much time. See
:mod:`_clipping_core` for the definition and why it is conservative.

Payload: a per-driver leaderboard (``time_lost_s``, ``clip_m``,
``kmh_lost_max``, the individual zones), each driver's speed trace downsampled
for plotting, and the pole lap's racing line so zones can be drawn on a map.
Zones from any driver map onto that line by lap fraction.

Singleton per session; sessions before 2026 are refused (404) because the
earlier hybrid rules make the measurement meaningless -- a 2025 lap reads zero.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import numpy as np

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2 import _clipping_core as clip
from src.services.analysis.v2 import _lap_duel_core as laps
from src.services.analysis.v2._field_laps import load_field_laps
from src.services.analysis.v2._helpers import (
    build_session_store,
    get_circuit_info_for_session,
    shared_session_stores,
)

logger = get_logger(__name__)

DATA_TYPE = "energy_clipping"
FIRST_YEAR = 2026

_TRACE_POINTS = 500
_OUTLINE_POINTS = 400

METHOD = {
    "definition": "speed decreasing while throttle >= 98% and brake off",
    "min_zone_m": clip.MIN_RUN_M,
    "min_loss_kmh": clip.MIN_LOSS_KMH,
    "brake_guard_m": clip.BRAKE_GUARD_M,
    "time_lost": "time over the zone versus holding the zone's entry speed",
    "estimated": True,
}


def _unavailable(year: int, identifier: Any, session: str, reason: str) -> DataNotAvailableError:
    return DataNotAvailableError(year=year, gp=identifier, session=session, source="livetiming", reason=reason)


def _driver_entry(lap: Dict[str, Any]) -> Dict[str, Any]:
    """Clipping analysis of one driver's lap (a :func:`_field_laps.load_field_laps` value)."""
    grid = clip.resample_by_distance(lap["trace"])
    zones = clip.detect_clipping(grid)
    length = float(grid["d"][-1])
    idx = np.linspace(0, len(grid["d"]) - 1, _TRACE_POINTS).round().astype(int)
    return {
        "driver": lap["tla"],
        "team": lap["team"],
        "color": lap["color"],
        "lap_time_s": round(lap["lap_time"], 3),
        "lapTime": laps.format_lap_time(lap["lap_time"]),
        "length_m": round(length, 1),
        **clip.summarise(zones),
        "zones": zones,
        "trace": {
            "distance": [round(float(v), 1) for v in grid["d"][idx]],
            "speed": [round(float(v), 1) for v in grid["speed"][idx]],
        },
        "_grid": grid,  # stripped before returning; used for the outline
    }


def _outline(grid: Dict[str, np.ndarray]) -> Optional[Dict[str, List[float]]]:
    if np.all(np.isnan(grid["x"])):
        return None
    idx = np.linspace(0, len(grid["d"]) - 1, _OUTLINE_POINTS).round().astype(int)
    length = float(grid["d"][-1])
    return {
        "x": [round(float(v), 0) for v in grid["x"][idx]],
        "y": [round(float(v), 0) for v in grid["y"][idx]],
        "fraction": [round(float(v) / length, 4) for v in grid["d"][idx]],
    }


def build_payload(entries: List[Dict[str, Any]], rotation: float, year: int,
                  event_name: str, session: str) -> Dict[str, Any]:
    """Assemble the payload from per-driver entries (test seam)."""
    if not entries:
        raise ValueError("no drivers")
    reference = min(entries, key=lambda e: e["lap_time_s"])
    outline = _outline(reference["_grid"])
    drivers = []
    for e in sorted(entries, key=lambda e: (-e["time_lost_s"], e["lap_time_s"])):
        entry = {k: v for k, v in e.items() if k != "_grid"}
        for z in entry["zones"]:
            z["start_fraction"] = round(z["start_m"] / e["length_m"], 4)
            z["end_fraction"] = round(z["end_m"] / e["length_m"], 4)
        drivers.append(entry)

    lost = [d["time_lost_s"] for d in drivers]
    highlights = {
        "most_time_lost": {"driver": drivers[0]["driver"], "seconds": drivers[0]["time_lost_s"]},
        "least_time_lost": {"driver": drivers[-1]["driver"], "seconds": drivers[-1]["time_lost_s"]},
        "field_median_time_lost_s": round(float(np.median(lost)), 3),
        "field_median_clip_m": round(float(np.median([d["clip_m"] for d in drivers])), 1),
        "biggest_single_drop": max(
            ({"driver": d["driver"], "kmh": z["kmh_lost"], "start_m": z["start_m"], "end_m": z["end_m"],
              "speed_in_kmh": z["speed_in_kmh"], "speed_out_kmh": z["speed_out_kmh"]}
             for d in drivers for z in d["zones"]),
            key=lambda z: z["kmh"], default=None,
        ),
        "pole_lap": {"driver": reference["driver"], "time_lost_s": reference["time_lost_s"]},
    }
    return {
        "drivers": drivers,
        "reference_driver": reference["driver"],
        "track": {**outline, "rotation": rotation} if outline else None,
        "highlights": highlights,
        "method": METHOD,
        "session_info": {"year": year, "event_name": event_name, "session_name": session},
    }


def _generate(year: int, identifier: Any, session: str) -> Dict[str, Any]:
    with shared_session_stores():
        store = build_session_store(year, identifier, session)
        if store is None:
            raise _unavailable(year, identifier, session, "Session could not be resolved")
        entries = [_driver_entry(lap) for lap in load_field_laps(store).values()]
        if not entries:
            raise _unavailable(year, identifier, session, "No lap telemetry available")
        circuit = get_circuit_info_for_session(store) or {}
        return build_payload(entries, circuit.get("rotation", 0), year, store.event_name, session)


class EnergyClippingData:
    """Callable: ``EnergyClippingData()(year, identifier, session) -> dict``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> Dict[str, Any]:
        if int(y) < FIRST_YEAR:
            raise _unavailable(y, identifier, e,
                               f"Energy clipping is measured for the {FIRST_YEAR}+ power units only")
        return cached_or_generate(
            year=y, identifier=identifier, session=e, data_type=DATA_TYPE,
            generator=lambda: _generate(y, identifier, e), version="v2",
        )


class EnergyClippingPlot:
    """Callable: ``EnergyClippingPlot()(year, identifier, session, driver=None, fmt=None) -> PNG path``.

    ``driver`` picks whose clipping zones are highlighted (default: the pole
    lap). ``fmt`` is one of ``canvas.FORMAT_NAMES`` (``None`` renders landscape).
    """

    def __call__(self, y: int, identifier: Union[int, str], e: str,
                 driver: Optional[str] = None, fmt: Optional[str] = None) -> str:
        from src.services.analysis.v2 import _clipping_render
        from src.services.plotting.canvas import get_format

        if fmt is not None:
            get_format(fmt)  # validate before the expensive part
        payload = EnergyClippingData()(y, identifier, e)
        return _clipping_render.render(payload, fmt, driver=driver)
