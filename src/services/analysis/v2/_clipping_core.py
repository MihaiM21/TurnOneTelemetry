"""
Energy clipping detection -- pure math (no network, no store).

2026 power units split roughly 50/50 between combustion and electric. When the
battery can no longer deploy (the lap's energy budget is spent, or the car is
harvesting on the straight -- "super clipping"), the car loses a large slice of
its power *while the driver is still flat out*. On a speed trace that shows as
speed falling at 100 % throttle, typically mid- to late-straight: the "U" in the
middle of Baku's back straight, or a top speed reached early then bled away.

Definition used here (deliberately conservative):

    clipping = speed decreasing while throttle >= 98 % and the brake is off,
               sustained for >= MIN_RUN_M metres and >= MIN_LOSS_KMH km/h.

Drag alone cannot make a car at full throttle *slow down*, so this never fires
on a normal drag-limited plateau -- which also means it under-counts clipping
that merely flattens the curve. The output is labelled "estimated" for that
reason.
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from src.services.analysis.v2._lap_duel_core import smooth

GRID_STEP_M = 5.0
FULL_THROTTLE = 98.0
SMOOTH_M = 35.0
DECEL_THRESHOLD = -0.008     # km/h per metre: -0.8 km/h over 100 m
MERGE_GAP_M = 25.0
MIN_RUN_M = 40.0
MIN_LOSS_KMH = 3.0
# Ignore the approach to a lift/brake: the centred smoothing leaks braking
# deceleration backwards, and at ~4 Hz the brake flag lands a sample late.
BRAKE_GUARD_M = 50.0

_trapezoid = getattr(np, "trapezoid", None) or np.trapz  # numpy 2 renamed it


def resample_by_distance(trace: pd.DataFrame, step_m: float = GRID_STEP_M) -> Dict[str, np.ndarray]:
    """Uniform-distance grid over one lap trace (``_lap_duel_core.build_lap_trace`` output)."""
    d = trace["d"].to_numpy(dtype=float)
    grid = np.arange(0.0, float(d[-1]) + 1e-9, step_m)
    out = {"d": grid}
    for col in ("t", "speed", "throttle", "x", "y"):
        out[col] = np.interp(grid, d, trace[col].to_numpy(dtype=float))
    idx = np.clip(np.searchsorted(d, grid, side="right") - 1, 0, len(d) - 1)
    out["brake"] = trace["brake"].to_numpy(dtype=float)[idx]
    return out


def _runs(mask: np.ndarray) -> List[tuple]:
    """``[(start_idx, end_idx_inclusive), ...]`` of consecutive True values."""
    runs = []
    start = None
    for i, on in enumerate(mask):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs


def detect_clipping(grid: Dict[str, np.ndarray]) -> List[Dict[str, Any]]:
    """Clipping runs on one lap's distance grid.

    Each run: ``start_m``, ``end_m``, ``length_m``, ``speed_in_kmh`` (speed when
    it started falling), ``speed_out_kmh`` (lowest speed before the driver
    lifts or brakes), ``kmh_lost`` and ``time_lost_s`` -- the time the run cost
    compared with holding ``speed_in`` over the same distance.
    """
    d = grid["d"]
    if len(d) < 5:
        return []
    step = float(np.mean(np.diff(d)))
    win = max(3, int(round(SMOOTH_M / step)))
    v = smooth(grid["speed"], win)
    dv = np.gradient(v, d)
    flat_out = (grid["throttle"] >= FULL_THROTTLE) & (grid["brake"] <= 0)
    mask = flat_out & (dv < DECEL_THRESHOLD) & (_distance_to_next_lift(d, flat_out) > BRAKE_GUARD_M)

    # Bridge tiny gaps (sensor noise) without bridging a lift.
    gap = int(round(MERGE_GAP_M / step))
    raw = _runs(mask)
    merged: List[List[int]] = []
    for s, e in raw:
        if merged and s - merged[-1][1] - 1 <= gap and np.all(flat_out[merged[-1][1]:s + 1]):
            merged[-1][1] = e
        else:
            merged.append([s, e])

    # Back each run up to where the decline began (the local speed peak), then
    # merge runs whose extended spans overlap -- several short dips on one
    # straight back up to the same peak and must not be counted twice.
    spans: List[List[int]] = []
    for s, e in merged:
        i0 = s
        while i0 > 0 and flat_out[i0 - 1] and v[i0 - 1] >= v[i0]:
            i0 -= 1
        if spans and i0 <= spans[-1][1] + gap:
            spans[-1][1] = max(spans[-1][1], e)
        else:
            spans.append([i0, e])

    out = []
    for i0, e in spans:
        length = float(d[e] - d[i0])
        v_in = float(v[i0])
        v_out = float(np.min(v[i0:e + 1]))
        loss = v_in - v_out
        if length < MIN_RUN_M or loss < MIN_LOSS_KMH:
            continue
        seg_v = np.maximum(v[i0:e + 1], 1.0) / 3.6
        seg_d = d[i0:e + 1]
        dt_actual = float(_trapezoid(1.0 / seg_v, seg_d))
        dt_held = length / (v_in / 3.6)
        out.append({
            "start_m": round(float(d[i0]), 1),
            "end_m": round(float(d[e]), 1),
            "length_m": round(length, 1),
            "speed_in_kmh": round(v_in, 1),
            "speed_out_kmh": round(v_out, 1),
            "kmh_lost": round(loss, 1),
            "time_lost_s": round(max(dt_actual - dt_held, 0.0), 3),
        })
    return out


def _distance_to_next_lift(d: np.ndarray, flat_out: np.ndarray) -> np.ndarray:
    """Metres from each sample to the next sample that is not flat out (inf if none)."""
    out = np.full(len(d), np.inf)
    next_d = np.inf
    for i in range(len(d) - 1, -1, -1):
        if not flat_out[i]:
            next_d = d[i]
        out[i] = next_d - d[i]
    return out


def summarise(runs: List[Dict[str, Any]]) -> Dict[str, float]:
    return {
        "clip_m": round(sum(r["length_m"] for r in runs), 1),
        "kmh_lost_max": round(max((r["kmh_lost"] for r in runs), default=0.0), 1),
        "time_lost_s": round(sum(r["time_lost_s"] for r in runs), 3),
        "zones": len(runs),
    }
