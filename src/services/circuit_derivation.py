"""Trace a :class:`CircuitLayout` from one lap of livetiming position data.

Circuit geometry (outline, corner markers, display rotation) is normally seeded
from api.multiviewer.app. A brand-new circuit -- Madring in 2026 was the first
-- can run a whole weekend before multiviewer publishes it, and until then every
map-shaped feature degrades: ``/api/v2/telemetry/track-map`` 404s, the
track-map plot renders unrotated with ``circuit: null`` and corner-duel falls
back to sequential corner numbers.

This module fills the gap from data we already have. ``Position.z`` reports
every car's X/Y in **tenths of a metre** in the same coordinate frame
multiviewer's outlines use, so the fastest clean lap of a session *is* the
track outline:

1. ``build_outline`` -- dedupe, arc-length parametrise, sanity-check (lap
   length, closure) and resample the trace to ``OUTLINE_POINTS`` evenly spaced
   points. Point 0 is the start/finish line because the lap window starts there.
2. ``detect_corners_from_geometry`` -- heading/curvature analysis: contiguous
   runs of curvature tighter than ``max_radius_m``, split where the turn
   direction flips (a chicane is two corners), kept when the total heading
   change reaches ``min_turn_deg``. Apex = the point where half the turn is done,
   which is stable on constant-radius arcs where ``argmax |kappa|`` is noise.
3. ``snap_corners_to_speed_minima`` -- when CarData speed is available, move each
   apex onto the local speed minimum. ``corner_duel`` matches its own
   speed-detected apexes to layout corners within 15 m, so this is what makes
   derived corner numbers line up with the telemetry features.
4. ``auto_rotation`` -- multiviewer's ``rotation`` has no consistent convention,
   so ours is "pit straight runs left-to-right after ``_rotate_xy``"; an
   operator override exists for taste.

Everything above the orchestrator is pure numpy on raw units and is unit-tested
against a synthetic lap. The result is stored via
:mod:`src.ingestion.circuits_store` with ``source="telemetry"`` and provenance
fields, and is provisional: ``circuits_sync`` replaces it once multiviewer
publishes the real layout.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from src.core.logging import get_logger
from src.domain.models.circuits import CircuitLayout, CircuitSummary, Point, TrackMarker, TrackOutline
from src.ingestion import circuits_store
from src.ingestion.reference import get_season_events
from src.services.analysis.v2._helpers import (
    RACE_SESSIONS,
    extract_position_for_lap,
    extract_telemetry_for_lap,
    get_fastest_lap_windows,
    resolve_driver,
)
from src.services.analysis.v2._race_helpers import get_clean_fastest_lap_window
from src.services.analysis.v2.session_store import SessionDataStore

logger = get_logger(__name__)

# Position X/Y/Z arrive in tenths of a metre (see lap_frames.XY_UNITS_PER_METRE;
# duplicated here so this module stays import-light).
RAW_UNITS_PER_METRE = 10.0
# Multiviewer outlines carry 283-1005 points; 750 is the middle of that range.
OUTLINE_POINTS = 750
SOURCE_TELEMETRY = "telemetry"


class CircuitDerivationError(RuntimeError):
    """No usable outline could be traced from the available laps."""


class LayoutExistsError(CircuitDerivationError):
    """A layout is already stored for this circuit/year and ``overwrite`` is off."""

    def __init__(self, year: int, circuit_id: str, path: Any, source: str):
        self.year, self.circuit_id, self.path, self.source = year, circuit_id, path, source
        super().__init__(
            f"A {source} layout already exists for circuit {circuit_id} in {year} ({path}); "
            "pass overwrite=true to replace it."
        )


def _raw(metres: float) -> float:
    return float(metres) * RAW_UNITS_PER_METRE


def _rotate_xy(x: np.ndarray, y: np.ndarray, rotation_deg: float) -> Tuple[np.ndarray, np.ndarray]:
    """Rotate about the origin by ``rotation_deg`` degrees.

    Identical to ``telemetry_track_map._rotate_xy`` (asserted by a test); copied
    so this module does not import a matplotlib-backed one.
    """
    if not rotation_deg:
        return x, y
    theta = np.deg2rad(rotation_deg)
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    return x * cos_t - y * sin_t, x * sin_t + y * cos_t


# ---------------------------------------------------------------------------
# Outline
# ---------------------------------------------------------------------------

@dataclass
class Outline:
    """A closed, evenly spaced track outline in raw units.

    ``x``/``y``/``s`` are the resampled ring (``s`` uniform from 0, the ring is
    open like multiviewer's: the last point does not repeat the first).
    ``raw_s``/``raw_t`` are the arc length and lap-clock time of the deduplicated
    input samples, kept so telemetry sampled on the time axis can be mapped onto
    the outline's arc-length axis.
    """
    x: np.ndarray
    y: np.ndarray
    s: np.ndarray
    length_raw: float
    closure_gap_raw: float
    n_samples: int
    raw_s: np.ndarray = field(default_factory=lambda: np.zeros(0))
    raw_t: Optional[np.ndarray] = None

    @property
    def ds(self) -> float:
        return float(self.length_raw / len(self.s))

    def point_at(self, target_s: float) -> Tuple[float, float]:
        return point_at_s(self, target_s)


def dedupe_points(
    x: Sequence[float], y: Sequence[float], t: Optional[Sequence[float]] = None, *, min_step: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Drop NaNs and consecutive samples closer than ``min_step`` raw units.

    A car waiting on the grid, or two position frames carrying the same fix,
    produce zero-length steps that break arc-length interpolation.
    """
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    ta = None if t is None else np.asarray(t, dtype=float)
    finite = np.isfinite(xa) & np.isfinite(ya)
    if ta is not None:
        finite &= np.isfinite(ta)
    xa, ya = xa[finite], ya[finite]
    ta = None if ta is None else ta[finite]
    if len(xa) == 0:
        return xa, ya, ta

    keep = np.ones(len(xa), dtype=bool)
    last_x, last_y = xa[0], ya[0]
    for i in range(1, len(xa)):
        if math.hypot(xa[i] - last_x, ya[i] - last_y) < min_step:
            keep[i] = False
            continue
        last_x, last_y = xa[i], ya[i]
    return xa[keep], ya[keep], (None if ta is None else ta[keep])


def arc_length(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Cumulative Euclidean distance along the polyline, ``s[0] == 0``."""
    if len(x) == 0:
        return np.zeros(0)
    steps = np.hypot(np.diff(x), np.diff(y))
    return np.concatenate([[0.0], np.cumsum(steps)])


def closure_gap(x: np.ndarray, y: np.ndarray) -> float:
    return float(math.hypot(x[0] - x[-1], y[0] - y[-1])) if len(x) else float("inf")


def resample_by_arc_length(
    x: np.ndarray, y: np.ndarray, s: np.ndarray, n_points: int, *, close_gap: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Resample a lap onto ``n_points`` evenly spaced along its arc length.

    The ring is closed for interpolation by appending the first point at
    ``s[-1] + close_gap`` (the straight-line hop from the last sample back to
    the first), then sampled with ``endpoint=False`` so the output stays open.
    """
    xs = np.concatenate([x, [x[0]]])
    ys = np.concatenate([y, [y[0]]])
    ss = np.concatenate([s, [s[-1] + max(close_gap, 1e-9)]])
    sr = np.linspace(0.0, ss[-1], n_points, endpoint=False)
    return np.interp(sr, ss, xs), np.interp(sr, ss, ys), sr


def smooth_closed(values: np.ndarray, window: int) -> np.ndarray:
    """Circular moving average (odd ``window``; ``<= 1`` is a no-op)."""
    n = len(values)
    if window <= 1 or n < 3:
        return np.asarray(values, dtype=float)
    window = min(window | 1, n if n % 2 else n - 1)
    k = window // 2
    padded = np.concatenate([values[-k:], values, values[:k]])
    kernel = np.ones(window) / window
    return np.convolve(padded, kernel, mode="valid")


def headings_closed(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Unwrapped tangent heading (radians) at each point of a closed ring."""
    dx = np.roll(x, -1) - np.roll(x, 1)
    dy = np.roll(y, -1) - np.roll(y, 1)
    return np.unwrap(np.arctan2(dy, dx))


def _wrap_angle(a: np.ndarray) -> np.ndarray:
    """Wrap radians into ``(-pi, pi]``."""
    return (np.asarray(a, dtype=float) + np.pi) % (2 * np.pi) - np.pi


def curvature_closed(theta: np.ndarray, ds: float) -> np.ndarray:
    """Signed curvature (rad per raw unit) from headings on a closed ring.

    The central difference is wrapped so the +/-2*pi seam of an unwrapped lap
    does not appear as an infinite corner at the start/finish line.
    """
    dtheta = _wrap_angle(np.roll(theta, -1) - np.roll(theta, 1))
    return dtheta / (2.0 * ds)


def build_outline(
    x: Sequence[float],
    y: Sequence[float],
    t: Optional[Sequence[float]] = None,
    *,
    n_points: int = OUTLINE_POINTS,
    min_lap_m: float = 2500.0,
    max_lap_m: float = 8000.0,
    closure_tol_m: float = 100.0,
    min_samples: int = 100,
    smooth_window: int = 3,
) -> Outline:
    """Turn one lap's raw X/Y samples into an evenly spaced closed outline.

    Raises :class:`CircuitDerivationError` when the trace cannot be a full lap:
    too few samples, a length outside ``[min_lap_m, max_lap_m]`` (F1 circuits
    are 3.3-7.0 km), or a start/end gap above ``closure_tol_m`` (a truncated
    window, an in-lap, or a lap-time packet that fired late).
    """
    xd, yd, td = dedupe_points(x, y, t)
    if len(xd) < min_samples:
        raise CircuitDerivationError(f"only {len(xd)} usable position samples (need {min_samples})")

    raw_s = arc_length(xd, yd)
    length = float(raw_s[-1])
    if not (_raw(min_lap_m) <= length <= _raw(max_lap_m)):
        raise CircuitDerivationError(
            f"lap length {length / RAW_UNITS_PER_METRE:.0f} m outside [{min_lap_m:.0f}, {max_lap_m:.0f}] m"
        )

    gap = closure_gap(xd, yd)
    if gap > _raw(closure_tol_m):
        raise CircuitDerivationError(
            f"lap does not close: start/end gap {gap / RAW_UNITS_PER_METRE:.0f} m (tolerance {closure_tol_m:.0f} m)"
        )

    xr, yr, sr = resample_by_arc_length(xd, yd, raw_s, n_points, close_gap=gap)
    if smooth_window > 1:
        xr = smooth_closed(xr, smooth_window)
        yr = smooth_closed(yr, smooth_window)

    return Outline(
        x=xr, y=yr, s=sr,
        length_raw=length + gap,
        closure_gap_raw=gap,
        n_samples=int(len(xd)),
        raw_s=raw_s,
        raw_t=td,
    )


def point_at_s(outline: Outline, target_s: float) -> Tuple[float, float]:
    """Interpolated (x, y) at arc position ``target_s`` (periodic in the lap length)."""
    length = outline.length_raw
    xs = np.concatenate([outline.x, [outline.x[0]]])
    ys = np.concatenate([outline.y, [outline.y[0]]])
    ss = np.concatenate([outline.s, [length]])
    target = float(target_s) % length
    return float(np.interp(target, ss, xs)), float(np.interp(target, ss, ys))


def heading_at_s(outline: Outline, target_s: float, *, half_chord_m: float = 5.0) -> float:
    """Tangent heading in degrees, ``(-180, 180]``, at arc position ``target_s``."""
    h = _raw(half_chord_m)
    x0, y0 = point_at_s(outline, target_s - h)
    x1, y1 = point_at_s(outline, target_s + h)
    return float(math.degrees(math.atan2(y1 - y0, x1 - x0)))


# ---------------------------------------------------------------------------
# Corners
# ---------------------------------------------------------------------------

@dataclass
class GeoCorner:
    index: int
    s: float
    x: float
    y: float
    heading_deg: float
    turn_deg: float
    direction: int                     # +1 left (CCW), -1 right (CW)
    min_speed_kmh: Optional[float] = None


def _runs_on_ring(mask: np.ndarray, sign: np.ndarray) -> List[List[int]]:
    """Contiguous index runs where ``mask`` holds and ``sign`` is constant.

    Walks the ring starting from a ``False`` index so a run that straddles
    index 0 is returned as one run in lap order. A ring that is ``True``
    everywhere has no straight to anchor on and yields nothing.
    """
    n = len(mask)
    false_idx = np.flatnonzero(~mask)
    if len(false_idx) == 0 or not mask.any():
        return []
    start = int(false_idx[0])
    runs: List[List[int]] = []
    current: List[int] = []
    current_sign = 0
    for k in range(n):
        i = (start + k) % n
        if mask[i] and (not current or sign[i] == current_sign):
            if not current:
                current_sign = int(sign[i])
            current.append(i)
        elif mask[i]:
            runs.append(current)
            current, current_sign = [i], int(sign[i])
        elif current:
            runs.append(current)
            current, current_sign = [], 0
    if current:
        runs.append(current)
    return runs


def detect_corners_from_geometry(
    x: np.ndarray,
    y: np.ndarray,
    s: np.ndarray,
    *,
    min_turn_deg: float = 25.0,
    smooth_m: float = 40.0,
    max_radius_m: float = 350.0,
    merge_gap_m: float = 25.0,
    min_separation_m: float = 60.0,
) -> List[GeoCorner]:
    """Detect corners from outline geometry alone (no speed needed).

    ``x``/``y``/``s`` are an evenly spaced closed ring in raw units (as built
    by :func:`build_outline`). Returns corners in lap order.
    """
    n = len(x)
    if n < 16:
        return []
    ds = float(s[1] - s[0]) if n > 1 else 1.0

    # Smooth heading through unit vectors, not the unwrapped angle: a moving
    # average across the ring's +/-2*pi seam would fabricate a corner at S/F.
    theta_raw = headings_closed(x, y)
    window = max(1, int(round(_raw(smooth_m) / ds)) | 1)
    cos_s = smooth_closed(np.cos(theta_raw), window)
    sin_s = smooth_closed(np.sin(theta_raw), window)
    theta = np.unwrap(np.arctan2(sin_s, cos_s))
    kappa = curvature_closed(theta, ds)

    mask = np.abs(kappa) > 1.0 / _raw(max_radius_m)
    sign = np.sign(kappa).astype(int)
    runs = _runs_on_ring(mask, sign)
    if not runs:
        return []

    # Merge same-direction runs separated by a short straight-ish gap.
    merged: List[List[int]] = [runs[0]]
    gap_pts = int(round(_raw(merge_gap_m) / ds))
    for run in runs[1:]:
        prev = merged[-1]
        gap = (run[0] - prev[-1]) % n
        if sign[run[0]] == sign[prev[0]] and gap <= gap_pts:
            merged[-1] = prev + [(prev[-1] + j + 1) % n for j in range(gap - 1)] + run
        else:
            merged.append(run)

    step = _wrap_angle(np.roll(theta, -1) - theta)      # heading change from i to i+1
    corners: List[GeoCorner] = []
    for run in merged:
        increments = np.abs(step[run[:-1]]) if len(run) > 1 else np.zeros(0)
        turn = float(np.degrees(increments.sum()))
        if turn < min_turn_deg:
            continue
        cum = np.cumsum(increments)
        apex_pos = int(np.searchsorted(cum, cum[-1] / 2.0)) if len(cum) else 0
        i = run[min(apex_pos, len(run) - 1)]
        corners.append(GeoCorner(
            index=int(i), s=float(s[i]), x=float(x[i]), y=float(y[i]),
            heading_deg=float(math.degrees(math.atan2(math.sin(theta[i]), math.cos(theta[i])))),
            turn_deg=turn, direction=int(sign[run[0]]),
        ))

    corners.sort(key=lambda c: c.s)
    # Enforce separation only between same-direction apexes: a chicane's two
    # opposite turns are legitimately close and are two numbered corners.
    kept: List[GeoCorner] = []
    for c in corners:
        if kept and c.direction == kept[-1].direction and c.s - kept[-1].s < _raw(min_separation_m):
            if c.turn_deg > kept[-1].turn_deg:
                kept[-1] = c
            continue
        kept.append(c)
    return kept


def speed_vs_arclength(
    pos_t: np.ndarray, pos_s_raw: np.ndarray, tel_t: np.ndarray, tel_speed: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Map time-sampled speed onto the lap's arc-length axis.

    Returns ``(speed_s, speed_kmh)`` sorted by ``speed_s``. Telemetry samples
    outside the position trace's time span are dropped rather than clamped.
    """
    pos_t = np.asarray(pos_t, dtype=float)
    pos_s_raw = np.asarray(pos_s_raw, dtype=float)
    tel_t = np.asarray(tel_t, dtype=float)
    tel_speed = np.asarray(tel_speed, dtype=float)
    if len(pos_t) < 2 or len(tel_t) == 0:
        return np.zeros(0), np.zeros(0)
    order = np.argsort(pos_t)
    pos_t, pos_s_raw = pos_t[order], pos_s_raw[order]
    inside = (tel_t >= pos_t[0]) & (tel_t <= pos_t[-1]) & np.isfinite(tel_speed)
    tel_t, tel_speed = tel_t[inside], tel_speed[inside]
    speed_s = np.interp(tel_t, pos_t, pos_s_raw)
    order = np.argsort(speed_s)
    return speed_s[order], tel_speed[order]


def snap_corners_to_speed_minima(
    corners: List[GeoCorner],
    outline: Outline,
    speed_s: np.ndarray,
    speed_kmh: np.ndarray,
    *,
    search_m: float = 75.0,
    min_dip_kmh: float = 3.0,
) -> Tuple[List[GeoCorner], int]:
    """Move each apex onto the nearest local speed minimum, if there is one.

    A corner is snapped only when the minimum inside ``+/- search_m`` sits at
    least ``min_dip_kmh`` below *both* window edges -- a flat-out kink or a
    minimum that is still falling at the window edge stays where geometry put
    it. Returns ``(corners, snapped_count)``; corners are renumbered by ``s``.
    """
    if len(speed_s) < 5 or not corners:
        return corners, 0
    smoothed = pd.Series(speed_kmh).rolling(window=5, center=True, min_periods=1).median().to_numpy()
    length = outline.length_raw
    half = _raw(search_m)
    snapped = 0
    result: List[GeoCorner] = []
    for c in corners:
        offset = (speed_s - c.s + length / 2.0) % length - length / 2.0
        idx = np.flatnonzero(np.abs(offset) <= half)
        if len(idx) < 3:
            result.append(c)
            continue
        idx = idx[np.argsort(offset[idx])]
        local = smoothed[idx]
        j = int(np.argmin(local))
        v_min = float(local[j])
        dip_ok = (local[0] - v_min >= min_dip_kmh) and (local[-1] - v_min >= min_dip_kmh)
        if not dip_ok:
            result.append(replace(c, min_speed_kmh=v_min))
            continue
        new_s = float(speed_s[idx[j]]) % length
        nx, ny = point_at_s(outline, new_s)
        result.append(replace(
            c, s=new_s, x=nx, y=ny,
            index=int(np.argmin(np.abs(outline.s - new_s))),
            heading_deg=heading_at_s(outline, new_s),
            min_speed_kmh=v_min,
        ))
        snapped += 1
    result.sort(key=lambda c: c.s)
    return result, snapped


# ---------------------------------------------------------------------------
# Rotation + preview
# ---------------------------------------------------------------------------

def auto_rotation(outline: Outline, *, straight_m: float = 120.0) -> float:
    """Rotation (degrees) that lays the pit straight left-to-right.

    Point 0 is the start/finish line; the chord to ``straight_m`` further along
    is the pit-straight heading. After ``_rotate_xy(x, y, rotation)`` that
    chord has ``dy ~ 0, dx > 0``.
    """
    x1, y1 = point_at_s(outline, _raw(straight_m))
    phi = math.degrees(math.atan2(y1 - outline.y[0], x1 - outline.x[0]))
    return round((-phi) % 360.0, 1) % 360.0


def build_preview_geometry(layout: CircuitLayout, rotation: Optional[float] = None, *, pad: float = 0.05) -> Dict:
    """Rotated, y-flipped geometry for an inline SVG preview.

    SVG's y axis grows downward, so after the same rotation the API consumers
    apply the y coordinate is negated -- otherwise the preview is a mirror
    image of the PNG the plot endpoints render.
    """
    rot = layout.rotation if rotation is None else rotation
    x = np.asarray(layout.track_outline.x, dtype=float)
    y = np.asarray(layout.track_outline.y, dtype=float)
    if len(x) == 0:
        return {"viewbox": "0 0 1 1", "polyline_points": "", "corners": [], "sf": None, "width": 1, "height": 1}
    xr, yr = _rotate_xy(x, y, rot)
    yr = -yr
    cx = np.asarray([c.position.x for c in layout.corners], dtype=float)
    cy = np.asarray([c.position.y for c in layout.corners], dtype=float)
    cxr, cyr = _rotate_xy(cx, cy, rot) if len(cx) else (cx, cy)
    cyr = -cyr

    min_x, max_x = float(xr.min()), float(xr.max())
    min_y, max_y = float(yr.min()), float(yr.max())
    span = max(max_x - min_x, max_y - min_y, 1.0)
    margin = span * pad
    width = (max_x - min_x) + 2 * margin
    height = (max_y - min_y) + 2 * margin
    return {
        "viewbox": f"{min_x - margin:.0f} {min_y - margin:.0f} {width:.0f} {height:.0f}",
        "width": round(width),
        "height": round(height),
        "polyline_points": " ".join(f"{px:.0f},{py:.0f}" for px, py in zip(xr, yr)),
        "corners": [
            {"number": c.number, "x": round(float(px)), "y": round(float(py))}
            for c, px, py in zip(layout.corners, cxr, cyr)
        ],
        "sf": {"x": round(float(xr[0])), "y": round(float(yr[0]))},
        "rotation": rot,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeriveOptions:
    driver: Optional[str] = None            # TLA; None = the session's fastest lap
    rotation: Optional[float] = None        # None = auto_rotation
    min_turn_deg: float = 25.0
    n_points: int = OUTLINE_POINTS
    max_candidates: int = 5                 # laps to try before giving up


@dataclass
class DerivedCircuit:
    layout: CircuitLayout
    summary: CircuitSummary
    stats: Dict[str, Any]


def _meeting_metadata(store: Any) -> Dict[str, Any]:
    """Identity fields for the layout, from ``SessionInfo.json``."""
    try:
        info = store.session_info()
    except Exception as exc:
        raise CircuitDerivationError(f"SessionInfo unavailable: {exc}") from exc
    meeting = info.get("Meeting") if isinstance(info, dict) else None
    if not isinstance(meeting, dict):
        raise CircuitDerivationError("SessionInfo has no Meeting block")
    circuit = meeting.get("Circuit") or {}
    key = circuit.get("Key") if isinstance(circuit, dict) else None
    if key is None:
        raise CircuitDerivationError("SessionInfo has no Meeting.Circuit.Key")
    country = meeting.get("Country") or {}
    if not isinstance(country, dict):
        country = {}
    return {
        "circuit_id": str(key),
        "circuit_key": int(key),
        "name": circuit.get("ShortName") or f"Circuit {key}",
        "country": country.get("Name") or "",
        "country_code": country.get("Code") or "",
        "location": meeting.get("Location") or "",
        "meeting_name": meeting.get("Name") or getattr(store, "event_name", None),
        "round": getattr(store, "round_nr", None),
        "race_date": _race_date(store, info),
    }


def _race_date(store: Any, info: Dict[str, Any]) -> Optional[str]:
    """``YYYY-MM-DD`` of the meeting's race, or ``None``. Never raises."""
    try:
        if str(getattr(store, "session_name", "")).upper() in ("R", "RACE"):
            start = info.get("StartDate")
            if isinstance(start, str) and len(start) >= 10:
                return start[:10]
        for event in get_season_events(store.year):
            if event.get("round") != getattr(store, "round_nr", None):
                continue
            for sess in event.get("sessions", []):
                if str(sess.get("name", "")).lower() == "race":
                    start = sess.get("startTime") or sess.get("startDate")
                    if isinstance(start, datetime):
                        return start.date().isoformat()
                    if isinstance(start, str) and len(start) >= 10:
                        return start[:10]
    except Exception:
        logger.debug("Race date lookup failed for %s", getattr(store, "event_name", "?"), exc_info=True)
    return None


def _candidate_windows(store: Any, driver: Optional[str], max_candidates: int) -> List[Dict[str, Any]]:
    """Lap windows to try, fastest first: ``{driver_num, tla, start, end, lap_time}``.

    Race-like sessions prefer the clean-lap selector (excludes pit in/out and
    non-green laps); everything else uses the personal-best scan.
    """
    driver_list = store.driver_list() or {}
    targets: Optional[List[str]] = None
    if driver:
        num, _ = resolve_driver(store, driver)
        targets = [num]

    def _tla(num: str) -> str:
        return str((driver_list.get(str(num)) or {}).get("tla") or num)

    windows: List[Dict[str, Any]] = []
    race_like = str(getattr(store, "session_name", "")).upper() in RACE_SESSIONS
    if race_like and hasattr(store, "lap_times"):
        for num in (targets or list(driver_list.keys())):
            try:
                win = get_clean_fastest_lap_window(store, str(num))
            except Exception:
                logger.debug("Clean-lap selection failed for %s", num, exc_info=True)
                win = None
            if win:
                windows.append({
                    "driver_num": str(num), "tla": _tla(str(num)),
                    "start": float(win["StartTime"]), "end": float(win["EndTime"]),
                    "lap_time": float(win["LapTime"]),
                })

    if not windows:
        df = get_fastest_lap_windows(
            store.base_url, store.client,
            target_driver_num=(targets[0] if targets else None), store=store,
        )
        for _, row in df.iterrows():
            windows.append({
                "driver_num": str(row["DriverNum"]), "tla": _tla(str(row["DriverNum"])),
                "start": float(row["StartTime"]), "end": float(row["EndTime"]),
                "lap_time": float(row["LapTime"]),
            })

    if not windows:
        raise CircuitDerivationError("no completed laps found in this session")
    windows.sort(key=lambda w: w["lap_time"])
    return windows[:max_candidates]


def derive_circuit_layout(store: Any, options: Optional[DeriveOptions] = None) -> DerivedCircuit:
    """Trace a layout from the session's best usable lap. Does not persist."""
    options = options or DeriveOptions()
    meta = _meeting_metadata(store)
    candidates = _candidate_windows(store, options.driver, options.max_candidates)

    rejected: List[str] = []
    chosen: Optional[Dict[str, Any]] = None
    outline: Optional[Outline] = None
    for cand in candidates:
        pos = extract_position_for_lap(
            store.base_url, store.client, cand["driver_num"], cand["start"], cand["end"], store=store,
        )
        if pos is None or pos.empty:
            rejected.append(f"{cand['tla']} {cand['lap_time']:.3f}s: no position samples")
            continue
        try:
            outline = build_outline(
                pos["X"].to_numpy(dtype=float), pos["Y"].to_numpy(dtype=float),
                pos["Time"].to_numpy(dtype=float), n_points=options.n_points,
            )
        except CircuitDerivationError as exc:
            rejected.append(f"{cand['tla']} {cand['lap_time']:.3f}s: {exc}")
            continue
        chosen = cand
        break

    if chosen is None or outline is None:
        raise CircuitDerivationError(
            "no candidate lap produced a closed outline (" + "; ".join(rejected) + ")"
        )

    corners = detect_corners_from_geometry(outline.x, outline.y, outline.s, min_turn_deg=options.min_turn_deg)

    snapped = 0
    speed_available = False
    try:
        tel = extract_telemetry_for_lap(
            store.base_url, store.client, chosen["driver_num"], chosen["start"], chosen["end"],
            channels=["2"], store=store,
        )
    except Exception:
        logger.debug("Speed channel unavailable for %s; corners stay geometric", chosen["tla"], exc_info=True)
        tel = pd.DataFrame()
    if tel is not None and not tel.empty and "Speed" in tel.columns and outline.raw_t is not None:
        speed_s, speed_kmh = speed_vs_arclength(
            outline.raw_t, outline.raw_s, tel["Time"].to_numpy(dtype=float), tel["Speed"].to_numpy(dtype=float),
        )
        speed_available = len(speed_s) > 0
        corners, snapped = snap_corners_to_speed_minima(corners, outline, speed_s, speed_kmh)

    rotation = float(options.rotation) if options.rotation is not None else auto_rotation(outline)

    markers = [
        TrackMarker(
            number=i + 1,
            angle=round(c.heading_deg, 3),
            length=round(c.s, 3),
            position=Point(x=round(c.x, 3), y=round(c.y, 3)),
        )
        for i, c in enumerate(corners)
    ]
    now = datetime.now(timezone.utc).isoformat()
    layout = CircuitLayout(
        circuit_id=meta["circuit_id"],
        year=int(store.year),
        name=meta["name"],
        country=meta["country"],
        country_code=meta["country_code"],
        location=meta["location"],
        rotation=rotation,
        round=meta["round"],
        race_date=meta["race_date"],
        meeting_name=meta["meeting_name"],
        track_outline=TrackOutline(
            x=[round(float(v), 3) for v in outline.x],
            y=[round(float(v), 3) for v in outline.y],
        ),
        corners=markers,
        marshal_lights=[],
        marshal_sectors=[],
        source=SOURCE_TELEMETRY,
        source_fetched_at=now,
        source_session=f"{store.year}/{getattr(store, 'event_name', '')}/{getattr(store, 'session_name', '')}",
        source_driver=chosen["tla"],
        source_lap_time_s=round(float(chosen["lap_time"]), 3),
    )
    summary = CircuitSummary(
        circuit_id=meta["circuit_id"],
        name=meta["name"],
        country=meta["country"],
        country_code=meta["country_code"],
        circuit_key=meta["circuit_key"],
        years_available=[int(store.year)],
    )
    stats = {
        "lap_length_m": round(outline.length_raw / RAW_UNITS_PER_METRE, 1),
        "n_points": int(len(outline.x)),
        "n_corners": len(markers),
        "closure_gap_m": round(outline.closure_gap_raw / RAW_UNITS_PER_METRE, 1),
        "samples": outline.n_samples,
        "speed_available": speed_available,
        "snapped": snapped,
        "rotation": rotation,
        "auto_rotation": options.rotation is None,
        "source_driver": chosen["tla"],
        "source_lap_time_s": round(float(chosen["lap_time"]), 3),
        "candidates_tried": len(rejected) + 1,
        "rejected": rejected,
        "corners": [
            {
                "number": m.number,
                "length_m": round(m.length / RAW_UNITS_PER_METRE, 1),
                "turn_deg": round(c.turn_deg, 1),
                "direction": "left" if c.direction > 0 else "right",
                "min_speed_kmh": None if c.min_speed_kmh is None else round(c.min_speed_kmh, 1),
            }
            for m, c in zip(markers, corners)
        ],
    }
    return DerivedCircuit(layout=layout, summary=summary, stats=stats)


def derive_and_store(
    year: int,
    gp: Any,
    session: str,
    options: Optional[DeriveOptions] = None,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
    store: Any = None,
) -> Dict[str, Any]:
    """Resolve the session, derive its layout and (unless ``dry_run``) persist it.

    The existence check runs *before* any multi-megabyte stream is touched, so
    the background hook is cheap for every circuit that already has a layout.
    Raises :class:`LayoutExistsError` when a layout exists and ``overwrite`` is
    off (ignored for a dry run, which only previews).
    """
    options = options or DeriveOptions()
    if store is None:
        store = SessionDataStore(year, gp, session)

    meta = _meeting_metadata(store)
    existing = circuits_store.find_circuit_file(store.year, meta["circuit_id"])
    existing_source = circuits_store.read_source(existing) if existing else None
    if existing is not None and not overwrite and not dry_run:
        raise LayoutExistsError(store.year, meta["circuit_id"], existing, existing_source or "unknown")

    derived = derive_circuit_layout(store, options)

    path = None
    if not dry_run:
        path = circuits_store.save_circuit_layout(derived.layout, derived.summary)
        logger.info(
            "Derived circuit layout %s (%s) from %s: %s corners, %.0f m, rotation %s",
            derived.layout.circuit_id, derived.layout.name, derived.layout.source_session,
            derived.stats["n_corners"], derived.stats["lap_length_m"], derived.layout.rotation,
        )

    return {
        "year": int(store.year),
        "circuit_id": derived.layout.circuit_id,
        "circuit_name": derived.layout.name,
        "session": derived.layout.source_session,
        "dry_run": bool(dry_run),
        "written": path is not None,
        "path": str(path) if path else None,
        "existing_source": existing_source,
        "rotation": derived.layout.rotation,
        "stats": derived.stats,
        "corners": derived.stats["corners"],
        "layout": derived.layout.model_dump(),
    }


def ensure_circuit_layout(year: int, gp: Any, session: str, *, store: Any = None) -> Optional[Dict[str, Any]]:
    """Derive and store a layout only if none exists. Never raises.

    Returns the ``derive_and_store`` result when a layout was written, else
    ``None`` (already present, or derivation failed -- logged at WARNING).
    """
    try:
        return derive_and_store(year, gp, session, overwrite=False, dry_run=False, store=store)
    except LayoutExistsError:
        return None
    except Exception:
        logger.warning("Automatic circuit derivation failed for %s %s %s", year, gp, session, exc_info=True)
        return None


__all__ = [
    "CircuitDerivationError", "LayoutExistsError", "DeriveOptions", "DerivedCircuit", "Outline", "GeoCorner",
    "build_outline", "detect_corners_from_geometry", "snap_corners_to_speed_minima", "speed_vs_arclength",
    "auto_rotation", "build_preview_geometry", "derive_circuit_layout", "derive_and_store",
    "ensure_circuit_layout", "point_at_s", "heading_at_s", "RAW_UNITS_PER_METRE", "OUTLINE_POINTS",
]
