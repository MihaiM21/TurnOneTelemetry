"""Synthetic laps for the Lap Duel tests (no network, no store).

A "lap" here is a CarData frame in raw livetiming keys plus a Position frame,
both on an absolute session clock, sampled at 4 Hz with margin samples either
side of the lap -- exactly what ``lap_duel._load_side`` hands to
``_lap_duel_core.build_lap_trace``.
"""
from __future__ import annotations

from typing import Callable, Optional, Tuple

import numpy as np
import pandas as pd

HZ = 4.0
MARGIN_S = 2.0


def _times(start_t: float, lap_time: float, hz: float = HZ, margin: float = MARGIN_S) -> np.ndarray:
    return np.arange(start_t - margin, start_t + lap_time + margin + 1e-9, 1.0 / hz)


def make_car(
    start_t: float,
    lap_time: float,
    speed_fn: Callable[[np.ndarray], np.ndarray],
    hz: float = HZ,
) -> pd.DataFrame:
    """CarData in raw channel keys. ``speed_fn`` maps lap fraction (0..1, margins outside) -> km/h."""
    t = _times(start_t, lap_time, hz)
    frac = (t - start_t) / lap_time
    speed = np.asarray(speed_fn(frac), dtype=float)
    braking = np.gradient(speed, t) < -25.0
    return pd.DataFrame({
        "Time": t,
        "2": speed,
        "4": np.where(braking, 0.0, 100.0),
        "5": braking.astype(int) * 100,
        "3": np.clip((speed // 40).astype(int) + 1, 1, 8),
        "0": 6000 + speed * 30.0,
        "45": np.where(speed > 250, 12, 0),
    })


def make_circle_pos(
    start_t: float,
    lap_time: float,
    radius_m: float,
    hz: float = HZ,
    angle_fn: Optional[Callable[[np.ndarray], np.ndarray]] = None,
) -> pd.DataFrame:
    """Position on a circle (counter-clockwise), X/Y in tenths of a metre.

    By default one full revolution per lap, uniform in time; ``angle_fn`` maps
    time-since-start (s) -> angle (rad) to override.
    """
    t = _times(start_t, lap_time, hz)
    rel = t - start_t
    theta = 2 * np.pi * rel / lap_time if angle_fn is None else angle_fn(rel)
    return pd.DataFrame({
        "Time": t,
        "X": radius_m * 10.0 * np.cos(theta),
        "Y": radius_m * 10.0 * np.sin(theta),
    })


def constant_speed(kmh: float) -> Callable[[np.ndarray], np.ndarray]:
    return lambda frac: np.full_like(frac, kmh, dtype=float)


def dipped_speed(base: float = 280.0, dips: Tuple[Tuple[float, float], ...] = ((0.2, 90.0), (0.5, 120.0), (0.8, 80.0)),
                 width: float = 0.03) -> Callable[[np.ndarray], np.ndarray]:
    """A lap with braking-corner dips (centre fraction, minimum km/h)."""
    def fn(frac: np.ndarray) -> np.ndarray:
        v = np.full_like(frac, base, dtype=float)
        for centre, vmin in dips:
            depth = base - vmin
            v = np.minimum(v, base - depth * np.clip(1 - ((frac - centre) / width) ** 2, 0, 1))
        return v
    return fn
