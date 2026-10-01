"""
Every driver's fastest clean lap as a distance trace -- shared loader.

Field-wide telemetry features (energy clipping, corner-speed profile, efficiency
scatter, field dominance) all start from the same thing: each driver's fastest
clean lap with speed/throttle/brake/gear on a distance axis and X/Y attached.
Call :func:`load_field_laps` inside :func:`shared_session_stores` (or pass one
store) so the multi-megabyte CarData/Position streams are parsed once.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from src.core.logging import get_logger
from src.services.analysis.v2 import _lap_duel_core as laps
from src.services.analysis.v2._helpers import (
    CAR_DATA_CHANNELS,
    extract_channels_window,
    extract_positions_window,
    resolve_fastest_lap_window,
)
from src.services.plotting.colors import get_driver_color

logger = get_logger(__name__)


def driver_colour(info: Dict[str, Any], year: int) -> str:
    """Livetiming team colour, falling back to the year-aware palette."""
    colour = info.get("color") or ""
    if isinstance(colour, str) and colour.startswith("#") and colour.upper() not in ("#FFFFFF", "#FFF"):
        return colour
    return get_driver_color(info.get("tla", ""), year)


def load_lap(store: Any, num: str) -> Optional[Dict[str, Any]]:
    """One driver's fastest clean lap, or ``None`` if it has no usable telemetry.

    Returns ``{"trace": DataFrame, "lap_time": float, "start": float, "end": float}``
    where ``trace`` is :func:`_lap_duel_core.build_lap_trace` output: columns
    ``t, d, speed, throttle, brake, gear, rpm, drs, x, y`` (d in metres from
    the timing line, x/y in raw tenths of a metre).
    """
    window = resolve_fastest_lap_window(store.base_url, store.client, num, store=store)
    if window is None:
        return None
    start, end, lap_time = window
    car = extract_channels_window(store.base_url, store.client, [num], start, end,
                                  channels=list(CAR_DATA_CHANNELS), store=store, raw_names=True).get(num)
    pos = extract_positions_window(store.base_url, store.client, [num], start, end, store=store).get(num)
    trace = laps.build_lap_trace(car, pos, start, lap_time)
    if trace.empty or len(trace) < 20:
        return None
    return {"trace": trace, "lap_time": float(lap_time), "start": float(start), "end": float(end)}


def load_field_laps(store: Any) -> Dict[str, Dict[str, Any]]:
    """``{car_number: {tla, team, color, trace, lap_time, ...}}`` for every driver with a lap.

    A driver whose lap cannot be built is logged and skipped rather than
    failing the whole field.
    """
    year = int(getattr(store, "year", 0) or 0)
    out: Dict[str, Dict[str, Any]] = {}
    for num, info in store.driver_list().items():
        try:
            lap = load_lap(store, str(num))
        except Exception:
            logger.exception("Fastest-lap trace failed for car %s", num)
            lap = None
        if lap is None:
            continue
        out[str(num)] = {
            "tla": info.get("tla"),
            "team": info.get("team"),
            "color": driver_colour(info, year),
            **lap,
        }
    return out


def empty_trace() -> pd.DataFrame:
    return pd.DataFrame(columns=["t", "d", "speed", "throttle", "brake", "gear", "rpm", "drs", "x", "y"])
