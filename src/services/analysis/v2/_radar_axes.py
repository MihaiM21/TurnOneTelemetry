"""
Driver-radar axis registry + metric extractors (V2).

This is the one seam the rest of the radar feature is organized around. An
:class:`AxisSpec` says *what* a spoke is (its label, which raw metric feeds it,
whether higher is better, and how :mod:`_radar_scale` should normalize it); the
three registries below say *which* spokes each scope shows. Adding, removing or
reordering a spoke is an edit to a registry list — never to the renderer or the
endpoints.

Two families of metric live here, kept deliberately as pure functions so they
are unit-testable on fixtures without any network:

  * **Cheap, whole-field metrics** built from a single ``TimingData`` parse
    (top speed via the speed trap, race pace, one-lap pace, consistency) and
    from jolpica season rows (race pace, qualifying, consistency, racecraft,
    reliability, peak). These are normalized *relative to the field*.
  * **Telemetry ratio metrics** (cornering, braveness) that need a driver's
    fastest-lap ``CarData`` — expensive per driver, so only computed for the
    handful being charted. They are self-normalizing ratios (min corner speed /
    entry speed) mapped against fixed bands, so they don't need the whole field.

Reused rather than re-implemented: :func:`detect_corners` and
:func:`get_fastest_lap_telemetry` from :mod:`_helpers`; the speed-trap parsing
mirrors :mod:`top_speed` but keyed per driver instead of per team.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.core.logging import get_logger
from src.ingestion.static_client import F1StaticClient
from src.services.analysis.v2._helpers import detect_corners, get_fastest_lap_telemetry

logger = get_logger(__name__)

# A corner whose entry speed clears this threshold counts as "high speed" for
# the braveness axis (commitment where it actually costs nerve).
HIGH_SPEED_ENTRY_KMH = 200.0

# Fixed bands for the self-normalizing corner ratios. A driver who scrubs off
# most of the entry speed sits near ``lo``; one who carries speed through sits
# near ``hi``. Tuned from typical mid-corner/entry ratios.
_CORNER_RATIO_LO = 0.30
_CORNER_RATIO_HI = 0.75


@dataclass(frozen=True)
class AxisSpec:
    """One radar spoke: label, raw-metric key, direction and scaling method."""

    name: str
    key: str
    higher_is_better: bool = True
    method: str = "percentile"  # "percentile" | "absolute"
    lo: Optional[float] = None
    hi: Optional[float] = None


# Single-session (telemetry available). Six spokes: four cheap field-relative
# axes + two self-normalizing telemetry ratios.
SINGLE_SESSION_AXES: List[AxisSpec] = [
    AxisSpec("Top Speed", "top_speed", higher_is_better=True, method="percentile"),
    AxisSpec("Cornering", "cornering", higher_is_better=True, method="absolute",
             lo=_CORNER_RATIO_LO, hi=_CORNER_RATIO_HI),
    AxisSpec("Race Pace", "race_pace", higher_is_better=False, method="percentile"),
    AxisSpec("Qualifying", "quali_pace", higher_is_better=False, method="percentile"),
    AxisSpec("Consistency", "consistency", higher_is_better=False, method="percentile"),
    AxisSpec("Braveness", "braveness", higher_is_better=True, method="absolute",
             lo=_CORNER_RATIO_LO, hi=_CORNER_RATIO_HI),
]

# Season / career (results-based, jolpica). All field-relative percentiles.
SEASON_AXES: List[AxisSpec] = [
    AxisSpec("Race Pace", "race_pace", higher_is_better=False, method="percentile"),
    AxisSpec("Qualifying", "quali_pace", higher_is_better=False, method="percentile"),
    AxisSpec("Consistency", "consistency", higher_is_better=False, method="percentile"),
    AxisSpec("Racecraft", "racecraft", higher_is_better=True, method="percentile"),
    AxisSpec("Reliability", "reliability", higher_is_better=True, method="percentile"),
    AxisSpec("Peak", "peak", higher_is_better=True, method="percentile"),
]

AXES_BY_SCOPE: Dict[str, List[AxisSpec]] = {
    "session": SINGLE_SESSION_AXES,
    "season": SEASON_AXES,
    "career": SEASON_AXES,
}


# ----------------------------------------------------------------------
# Cheap single-session field metrics (from a single TimingData parse)
# ----------------------------------------------------------------------
def _clean_laps(records: List[Dict[str, Any]]) -> List[float]:
    """Valid green-running lap times for a driver (excludes in/out laps)."""
    return [
        r["time_s"] for r in records
        if r.get("time_s") and r["time_s"] > 0
        and not r.get("pit_in") and not r.get("pit_out")
    ]


def top_speeds_by_driver(timing_entries: List[Dict[str, Any]]) -> Dict[str, float]:
    """Per-driver best speed-trap reading from ``TimingData`` entries.

    Mirrors :func:`top_speed.extract_top_speeds_from_speed_trap` but keyed per
    car number (``Lines.{num}.Speeds.ST.Value``) instead of aggregated per team.
    """
    best: Dict[str, float] = {}
    for entry in timing_entries:
        lines = entry.get("Lines")
        if not isinstance(lines, dict):
            continue
        for num, line in lines.items():
            if not isinstance(line, dict):
                continue
            st = (line.get("Speeds") or {}).get("ST") if isinstance(line.get("Speeds"), dict) else None
            val = st.get("Value") if isinstance(st, dict) else None
            if not val:
                continue
            try:
                speed = float(val)
            except (TypeError, ValueError):
                continue
            if speed > 0 and speed > best.get(str(num), 0.0):
                best[str(num)] = speed
    return best


def single_session_field_metrics(
    timing_entries: List[Dict[str, Any]],
    lap_times: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Dict[str, Optional[float]]]:
    """Cheap per-driver metrics for the whole field (test seam).

    Returns ``{car_num: {top_speed, race_pace, quali_pace, consistency}}`` where
    ``race_pace`` is the median clean lap (lower better), ``quali_pace`` the best
    clean lap (lower better) and ``consistency`` the clean-lap standard deviation
    (lower better). Values are ``None`` when a driver has too little running.
    """
    top_speeds = top_speeds_by_driver(timing_entries)
    metrics: Dict[str, Dict[str, Optional[float]]] = {}

    all_nums = set(lap_times) | set(top_speeds)
    for num in all_nums:
        clean = _clean_laps(lap_times.get(num, []))
        race_pace = statistics.median(clean) if clean else None
        quali_pace = min(clean) if clean else None
        consistency = statistics.pstdev(clean) if len(clean) >= 3 else None
        metrics[str(num)] = {
            "top_speed": top_speeds.get(str(num)),
            "race_pace": race_pace,
            "quali_pace": quali_pace,
            "consistency": consistency,
        }
    return metrics


# ----------------------------------------------------------------------
# Telemetry ratio metrics (cornering, braveness) — per requested driver
# ----------------------------------------------------------------------
def corner_ratios_from_frame(
    df, high_speed_entry_kmh: float = HIGH_SPEED_ENTRY_KMH
) -> Dict[str, Optional[float]]:
    """Cornering + braveness ratios from a fastest-lap telemetry frame (pure).

    ``cornering`` is the mean of ``min_speed / entry_speed`` across every
    detected corner (how much speed is carried through the corner on average);
    ``braveness`` is the same mean restricted to high-speed corners (entry above
    ``high_speed_entry_kmh``). Both are ``None`` when there are no qualifying
    corners. Reuses :func:`detect_corners` for apex detection.
    """
    corners = detect_corners(df) if df is not None and not df.empty else []

    def _ratio(subset: List[Dict[str, float]]) -> Optional[float]:
        vals = [
            c["min_speed_kmh"] / c["entry_speed_kmh"]
            for c in subset
            if c.get("entry_speed_kmh") and c["entry_speed_kmh"] > 0
        ]
        return sum(vals) / len(vals) if vals else None

    cornering = _ratio(corners)
    high_speed = [c for c in corners if (c.get("entry_speed_kmh") or 0) >= high_speed_entry_kmh]
    braveness = _ratio(high_speed)
    return {"cornering": cornering, "braveness": braveness}


def telemetry_ratios_for_driver(
    base_url: str, client: F1StaticClient, driver_num: str
) -> Dict[str, Optional[float]]:
    """Fetch a driver's fastest-lap telemetry and compute corner ratios.

    Fails soft: any missing/short telemetry yields ``{cornering: None,
    braveness: None}`` so the axis renders as a collapsed spoke rather than
    aborting the whole radar.
    """
    try:
        df = get_fastest_lap_telemetry(base_url, client, driver_num, channels=["2"])
    except Exception as exc:  # pragma: no cover - network/parse failure
        logger.warning("Radar telemetry fetch failed for driver %s: %s", driver_num, exc)
        return {"cornering": None, "braveness": None}
    return corner_ratios_from_frame(df)


# ----------------------------------------------------------------------
# Season / career metrics (from jolpica result rows)
# ----------------------------------------------------------------------
def _quali_position(row: Dict[str, Any]) -> Optional[int]:
    """Best available one-lap grid slot for a season row: grid, else position."""
    grid = row.get("grid")
    if isinstance(grid, int) and grid > 0:
        return grid
    pos = row.get("position")
    return pos if isinstance(pos, int) and pos > 0 else None


def season_field_metrics(
    race_rows: List[Dict[str, Any]],
    quali_rows: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Optional[float]]]:
    """Per-driver season metrics keyed by driver code (test seam).

    Returns ``{driver_code: {race_pace, quali_pace, consistency, racecraft,
    reliability, peak}}``:
      * ``race_pace`` — mean classified finishing position (lower better)
      * ``quali_pace`` — mean grid position (lower better)
      * ``consistency`` — stdev of finishing positions (lower better)
      * ``racecraft`` — mean positions gained grid->finish (higher better)
      * ``reliability`` — share of entries that were classified (higher better)
      * ``peak`` — share of races finished in the top 3 (higher better)
    """
    finishes: Dict[str, List[int]] = {}
    gained: Dict[str, List[int]] = {}
    starts: Dict[str, int] = {}
    classified: Dict[str, int] = {}
    podiums: Dict[str, int] = {}
    quali: Dict[str, List[int]] = {}

    for row in race_rows:
        drv = row.get("driver_code")
        if not drv:
            continue
        starts[drv] = starts.get(drv, 0) + 1
        pos = row.get("position")
        if row.get("classified") and isinstance(pos, int) and pos > 0:
            classified[drv] = classified.get(drv, 0) + 1
            finishes.setdefault(drv, []).append(pos)
            if pos <= 3:
                podiums[drv] = podiums.get(drv, 0) + 1
            grid = row.get("grid")
            if isinstance(grid, int) and grid > 0:
                gained.setdefault(drv, []).append(grid - pos)

    for row in quali_rows:
        drv = row.get("driver_code")
        if not drv:
            continue
        qp = _quali_position(row)
        if qp is not None:
            quali.setdefault(drv, []).append(qp)

    def _mean(xs: List[int]) -> Optional[float]:
        return sum(xs) / len(xs) if xs else None

    metrics: Dict[str, Dict[str, Optional[float]]] = {}
    for drv in set(starts) | set(quali):
        fin = finishes.get(drv, [])
        metrics[drv] = {
            "race_pace": _mean(fin),
            "quali_pace": _mean(quali.get(drv, [])),
            "consistency": statistics.pstdev(fin) if len(fin) >= 3 else None,
            "racecraft": _mean(gained.get(drv, [])),
            "reliability": (classified.get(drv, 0) / starts[drv]) if starts.get(drv) else None,
            "peak": (podiums.get(drv, 0) / starts[drv]) if starts.get(drv) else None,
        }
    return metrics


__all__ = [
    "AxisSpec",
    "SINGLE_SESSION_AXES",
    "SEASON_AXES",
    "AXES_BY_SCOPE",
    "HIGH_SPEED_ENTRY_KMH",
    "top_speeds_by_driver",
    "single_session_field_metrics",
    "corner_ratios_from_frame",
    "telemetry_ratios_for_driver",
    "season_field_metrics",
]
