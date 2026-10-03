"""
Lap Duel -- pure math (no network, no store).

Everything here takes plain arrays/DataFrames so it can be unit-tested with
synthetic laps. :mod:`lap_duel` does the fetching and :mod:`_lap_duel_render`
the drawing.

Conventions
-----------
* Distance is metres along the lap from the timing line.
* Each lap's distance comes from integrating its own speed trace (the FastF1
  convention; position samples at ~4 Hz cut the corners and come out ~1 %
  short). The two laps are then put on ONE grid by *lap fraction*: sample ``i``
  of both traces is the same fraction of each lap, labelled with the reference
  lap's metres. So ``delta`` at the finish line is exactly the lap-time
  difference, and two laps from different years line up corner for corner.
* ``delta(d) = t_b(d) - t_a(d)``. **Positive means B is behind A** at ``d`` --
  the same convention as ``corner_duel`` and the Remotion ``telemetry-compare``
  schema.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from src.services.analysis.v2._helpers import (
    CAR_DATA_CHANNELS,
    XY_UNITS_PER_METRE,
    detect_corners,
    parse_f1_time,
)

G = 9.80665

# Grid resolution: ~4 m on a 5.5 km lap. Fine enough for apexes, small enough
# that the JSON stays well under a megabyte with every channel on it.
GRID_POINTS = 1400

CONTINUOUS = ("t", "speed", "throttle", "rpm", "x", "y")
DISCRETE = ("gear", "brake", "drs")

FULL_THROTTLE_PCT = 98.0
MIN_APEX_DROP_KMH = 35.0       # a speed minimum this far below its entry speed is a braking corner
SECTION_MAX_HALF_WIDTH_M = 400.0
BRAKE_SCAN_BACK_M = 350.0
G_CLAMP = 6.0


# ----------------------------------------------------------------------
# Lap selection helpers
# ----------------------------------------------------------------------
def qualifying_part_bounds(session_data: Sequence[Dict[str, Any]]) -> Dict[int, Tuple[float, float]]:
    """``{part: (start_s, end_s)}`` session-time bounds of Q1/Q2/Q3.

    Read from ``SessionData.jsonStream``: each ``Series`` entry carries
    ``QualifyingPart`` and the stream's ``_timestamp`` says when it began. The
    first part may arrive in the initial snapshot as a list. A part ends where
    the next begins; the last runs to +inf.
    """
    starts: Dict[int, float] = {}
    for entry in session_data or []:
        series = entry.get("Series")
        if not series:
            continue
        items = series.values() if isinstance(series, dict) else series
        t = parse_f1_time(entry.get("_timestamp", entry.get("T", "")))
        for item in items:
            if not isinstance(item, dict) or "QualifyingPart" not in item:
                continue
            try:
                part = int(item["QualifyingPart"])
            except (TypeError, ValueError):
                continue
            if part > 0 and part not in starts:
                starts[part] = t
    ordered = sorted(starts.items())
    bounds: Dict[int, Tuple[float, float]] = {}
    for i, (part, start) in enumerate(ordered):
        end = ordered[i + 1][1] if i + 1 < len(ordered) else float("inf")
        bounds[part] = (start, end)
    return bounds


def window_for_lap(records: Sequence[Dict[str, Any]], lap: int) -> Optional[Tuple[float, float, float]]:
    """``(start, end, lap_time)`` of lap number ``lap`` from lap-time records."""
    for r in records:
        if int(r.get("lap", -1)) == int(lap) and float(r.get("time_s") or 0) > 0:
            end = float(r["timestamp_s"])
            return end - float(r["time_s"]), end, float(r["time_s"])
    return None


def best_window_in_range(
    records: Sequence[Dict[str, Any]], start: float, end: float,
) -> Optional[Tuple[float, float, float, int]]:
    """Fastest non-pit lap *completed* inside ``[start, end)`` -> ``(start, end, time, lap)``."""
    best = None
    for r in records:
        ts, lt = float(r.get("timestamp_s") or 0), float(r.get("time_s") or 0)
        if lt <= 0 or r.get("pit_in") or r.get("pit_out"):
            continue
        if not (start <= ts < end):
            continue
        if best is None or lt < best[2]:
            best = (ts - lt, ts, lt, int(r["lap"]))
    return best


def lap_number_for_window(records: Sequence[Dict[str, Any]], end_t: float, tol_s: float = 1.0) -> Optional[int]:
    """Lap number whose completion timestamp matches ``end_t``."""
    for r in records:
        if abs(float(r.get("timestamp_s") or 0) - end_t) <= tol_s:
            return int(r["lap"])
    return None


# ----------------------------------------------------------------------
# Trace assembly
# ----------------------------------------------------------------------
def build_lap_trace(car: pd.DataFrame, pos: pd.DataFrame, start_t: float, lap_time: float) -> pd.DataFrame:
    """One lap as a time-ordered frame with ``t, d, speed, throttle, brake, gear, rpm, drs, x, y``.

    ``car`` holds raw CarData keys (``raw_names=True``) on the absolute session
    clock, ``pos`` holds ``Time/X/Y``; both may include margin samples outside
    the lap, which are used to anchor ``d = 0`` at the timing line and are then
    dropped. ``d`` integrates speed; ``x``/``y`` are interpolated in time.
    """
    if car is None or car.empty:
        return pd.DataFrame()
    df = car.rename(columns=CAR_DATA_CHANNELS).sort_values("Time").copy()
    df["t"] = df["Time"].astype(float) - float(start_t)
    for col in ("speed", "throttle", "brake", "gear", "rpm", "drs"):
        if col not in df.columns:
            df[col] = 0.0
    df = df.dropna(subset=["speed"])
    t = df["t"].to_numpy(dtype=float)
    v = df["speed"].to_numpy(dtype=float) / 3.6
    if len(t) < 3:
        return pd.DataFrame()

    cum = np.concatenate([[0.0], np.cumsum(0.5 * (v[1:] + v[:-1]) * np.diff(t))])
    d0 = float(np.interp(0.0, t, cum))
    df["d"] = cum - d0

    if pos is not None and not pos.empty:
        p = pos.sort_values("Time")
        pt = p["Time"].to_numpy(dtype=float) - float(start_t)
        df["x"] = np.interp(t, pt, p["X"].to_numpy(dtype=float))
        df["y"] = np.interp(t, pt, p["Y"].to_numpy(dtype=float))
    else:
        df["x"] = np.nan
        df["y"] = np.nan

    # Close the lap exactly at t = 0 and t = lap_time so every lap starts at
    # d = 0 and ends at its own full length.
    edge_rows = []
    for edge in (0.0, float(lap_time)):
        row = {"t": edge}
        for col in ("speed", "throttle", "rpm", "x", "y", "d"):
            row[col] = float(np.interp(edge, t, df[col].to_numpy(dtype=float)))
        idx = int(np.clip(np.searchsorted(t, edge, side="right") - 1, 0, len(t) - 1))
        for col in ("brake", "gear", "drs"):
            row[col] = float(df[col].iloc[idx])
        edge_rows.append(row)

    inner = df[(df["t"] > 0.0) & (df["t"] < float(lap_time))]
    cols = ["t", "d", "speed", "throttle", "brake", "gear", "rpm", "drs", "x", "y"]
    out = pd.concat([pd.DataFrame([edge_rows[0]]), inner[cols], pd.DataFrame([edge_rows[1]])], ignore_index=True)
    return out[cols].reset_index(drop=True)


def resample_by_fraction(trace: pd.DataFrame, ref_length: float, n: int = GRID_POINTS) -> Dict[str, np.ndarray]:
    """Resample a lap onto ``n`` points by lap fraction, labelled in ``ref_length`` metres.

    Continuous channels are interpolated; discrete ones (gear, brake, DRS) take
    the previous sample -- averaging gear 4 and 5 into 4.5 invents a gear.
    """
    own = trace["d"].to_numpy(dtype=float)
    length = float(own[-1]) if len(own) else 0.0
    if length <= 0:
        raise ValueError("lap has no distance")
    frac_src = own / length
    frac = np.linspace(0.0, 1.0, n)
    out: Dict[str, np.ndarray] = {"distance": frac * float(ref_length), "own_distance": frac * length}
    for col in CONTINUOUS:
        out[col] = np.interp(frac, frac_src, trace[col].to_numpy(dtype=float))
    idx = np.clip(np.searchsorted(frac_src, frac, side="right") - 1, 0, len(frac_src) - 1)
    for col in DISCRETE:
        out[col] = trace[col].to_numpy(dtype=float)[idx]
    out["length_m"] = np.array(length)
    return out


# ----------------------------------------------------------------------
# Derived quantities
# ----------------------------------------------------------------------
def smooth(values: np.ndarray, window: int) -> np.ndarray:
    """Centred moving average with edge padding (odd ``window`` >= 1)."""
    window = max(1, int(window) | 1)
    if window == 1 or len(values) < window:
        return np.asarray(values, dtype=float)
    pad = window // 2
    padded = np.pad(np.asarray(values, dtype=float), pad, mode="edge")
    kernel = np.ones(window) / window
    return np.convolve(padded, kernel, mode="valid")


def compute_delta(t_a: np.ndarray, t_b: np.ndarray) -> np.ndarray:
    """``t_b - t_a`` on the shared grid. Positive => B behind."""
    return np.asarray(t_b, dtype=float) - np.asarray(t_a, dtype=float)


def compute_accelerations(grid: Dict[str, np.ndarray], smooth_m: float = 40.0) -> Tuple[np.ndarray, np.ndarray]:
    """Longitudinal and lateral acceleration in g, clamped to +-``G_CLAMP``.

    Derived from ~4 Hz data, so heavily smoothed: long = v dv/ds, lat = v^2 k
    with k the signed curvature of the smoothed X/Y path. Left turns positive.
    """
    s = grid["own_distance"]
    step = float(np.mean(np.diff(s))) if len(s) > 1 else 1.0
    win = max(3, int(round(smooth_m / max(step, 1e-6))))
    v = smooth(grid["speed"], win) / 3.6
    dv_ds = np.gradient(v, s)
    long_g = smooth(v * dv_ds / G, win)

    x = grid["x"] / XY_UNITS_PER_METRE
    y = grid["y"] / XY_UNITS_PER_METRE
    if np.all(np.isnan(x)) or np.all(np.isnan(y)):
        lat_g = np.full_like(v, np.nan)
    else:
        xs, ys = smooth(x, win * 2 + 1), smooth(y, win * 2 + 1)
        dx, dy = np.gradient(xs, s), np.gradient(ys, s)
        ddx, ddy = np.gradient(dx, s), np.gradient(dy, s)
        denom = np.power(dx * dx + dy * dy, 1.5)
        with np.errstate(divide="ignore", invalid="ignore"):
            kappa = np.where(denom > 1e-9, (dx * ddy - dy * ddx) / denom, 0.0)
        lat_g = smooth(v * v * kappa / G, win)
    return np.clip(long_g, -G_CLAMP, G_CLAMP), np.clip(lat_g, -G_CLAMP, G_CLAMP)


def place_corners(
    grid_a: Dict[str, np.ndarray], circuit_corners: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Corner numbers at grid distances.

    Circuit corners are projected onto lap A's own X/Y path (nearest point), so
    they land in *this* lap's distance frame even though the circuit JSON's
    ``distance_m`` was measured on someone else's lap. Falls back to
    ``distance_m``, then to speed-minimum detection numbered in track order.
    """
    dist = grid_a["distance"]
    corners: List[Dict[str, Any]] = []
    have_xy = not (np.all(np.isnan(grid_a["x"])) or np.all(np.isnan(grid_a["y"])))
    for c in circuit_corners or []:
        number = c.get("number")
        if number is None:
            continue
        d = None
        if have_xy and c.get("x") is not None and c.get("y") is not None:
            dd = np.hypot(grid_a["x"] - float(c["x"]), grid_a["y"] - float(c["y"]))
            i = int(np.nanargmin(dd))
            if dd[i] <= 400.0:  # raw units: 40 m
                d = float(dist[i])
        if d is None and c.get("distance_m") is not None:
            d = float(c["distance_m"])
        if d is not None and 0.0 <= d <= float(dist[-1]):
            corners.append({"number": int(number), "distance_m": round(d, 1)})
    if corners:
        return sorted(corners, key=lambda c: c["distance_m"])

    detected = detect_corners(pd.DataFrame({"Distance": dist, "Speed": grid_a["speed"]}),
                              min_separation_m=60.0, min_drop_kmh=20.0)
    return [{"number": i, "distance_m": round(c["distance_m"], 1)} for i, c in enumerate(detected, start=1)]


def find_apexes(grid_a: Dict[str, np.ndarray], grid_b: Dict[str, np.ndarray]) -> List[Dict[str, Any]]:
    """Braking-corner apexes (on A) with both drivers' minimum speed nearby."""
    dist = grid_a["distance"]
    detected = detect_corners(pd.DataFrame({"Distance": dist, "Speed": grid_a["speed"]}),
                              min_separation_m=80.0, min_drop_kmh=MIN_APEX_DROP_KMH)
    out = []
    for c in detected:
        mask = np.abs(dist - c["distance_m"]) <= 50.0
        if not np.any(mask):
            continue
        ia = int(np.argmin(np.where(mask, grid_a["speed"], np.inf)))
        ib = int(np.argmin(np.where(mask, grid_b["speed"], np.inf)))
        out.append({
            "distance_m": round(float(dist[ia]), 1),
            "min_speed_a": round(float(grid_a["speed"][ia]), 1),
            "min_speed_b": round(float(grid_b["speed"][ib]), 1),
        })
    return out


def braking_point(dist: np.ndarray, brake: np.ndarray, apex_d: float,
                  scan_back: float = BRAKE_SCAN_BACK_M) -> Optional[float]:
    """Start of the braking zone that ends at/just before the apex."""
    idx = np.where((dist <= apex_d) & (dist >= apex_d - scan_back))[0]
    if len(idx) == 0:
        return None
    on = brake[idx] > 0
    if not np.any(on):
        return None
    last_on = idx[np.where(on)[0][-1]]
    i = last_on
    while i > idx[0] and brake[i - 1] > 0:
        i -= 1
    return float(dist[i])


def build_sections(corners: List[Dict[str, Any]], lap_length: float) -> List[Dict[str, Any]]:
    """Split the lap at midpoints between corners, one section per corner.

    Each section runs from halfway along the approach to halfway along the
    exit, capped at ``SECTION_MAX_HALF_WIDTH_M`` either side, so a long
    straight is its own section rather than being credited to a corner.
    """
    if not corners:
        return [{"start_m": 0.0, "end_m": lap_length, "corners": []}]
    ds = [c["distance_m"] for c in corners]
    bounds = [0.0]
    for a, b in zip(ds, ds[1:]):
        bounds.append((a + b) / 2.0)
    bounds.append(lap_length)

    sections: List[Dict[str, Any]] = []
    for i, c in enumerate(corners):
        lo = max(bounds[i], c["distance_m"] - SECTION_MAX_HALF_WIDTH_M)
        hi = min(bounds[i + 1], c["distance_m"] + SECTION_MAX_HALF_WIDTH_M)
        if sections and lo > sections[-1]["end_m"] + 1.0:
            sections.append({"start_m": sections[-1]["end_m"], "end_m": lo, "corners": []})
        elif not sections and lo > 1.0:
            sections.append({"start_m": 0.0, "end_m": lo, "corners": []})
        sections.append({"start_m": lo, "end_m": hi, "corners": [c["number"]]})
    if sections[-1]["end_m"] < lap_length - 1.0:
        sections.append({"start_m": sections[-1]["end_m"], "end_m": lap_length, "corners": []})

    # Merge adjacent corner sections that are closer than 120 m (chicanes)
    # so "T8-T12" reads as one zone, the way the example charts group them.
    merged: List[Dict[str, Any]] = []
    for sec in sections:
        prev = merged[-1] if merged else None
        contiguous = prev is not None and abs(sec["start_m"] - prev["end_m"]) <= 1.0
        if (contiguous and sec["corners"] and prev["corners"]
                and _corner_gap(prev, sec, corners) < 120.0):
            prev["end_m"] = sec["end_m"]
            prev["corners"] = prev["corners"] + sec["corners"]
        else:
            merged.append(dict(sec))
    return merged


def _corner_gap(prev: Dict[str, Any], nxt: Dict[str, Any], corners: List[Dict[str, Any]]) -> float:
    pos = {c["number"]: c["distance_m"] for c in corners}
    return min(pos[n] for n in nxt["corners"]) - max(pos[n] for n in prev["corners"])


def section_gains(sections: List[Dict[str, Any]], dist: np.ndarray, delta: np.ndarray) -> List[Dict[str, Any]]:
    """Time B gained (negative) or lost (positive) to A in each section."""
    out = []
    for s in sections:
        d0 = float(np.interp(s["start_m"], dist, delta))
        d1 = float(np.interp(s["end_m"], dist, delta))
        out.append({**s, "start_m": round(s["start_m"], 1), "end_m": round(s["end_m"], 1),
                    "delta_change_s": round(d1 - d0, 3)})
    return out


def section_label(corner_numbers: List[int]) -> str:
    if not corner_numbers:
        return "straight"
    if len(corner_numbers) == 1:
        return f"T{corner_numbers[0]}"
    return f"T{min(corner_numbers)}-T{max(corner_numbers)}"


def full_throttle_pct(throttle: np.ndarray) -> float:
    return round(100.0 * float(np.mean(throttle >= FULL_THROTTLE_PCT)), 1)


def format_lap_time(seconds: float) -> str:
    millis = int(round(float(seconds) * 1000))   # round first: 119.9996 is 2:00.000, not 1:60.000
    minutes, rest_ms = divmod(millis, 60_000)
    rest = rest_ms / 1000.0
    return f"{minutes}:{rest:06.3f}" if minutes else f"{rest:.3f}"
