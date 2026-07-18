"""
Radar-axis normalization (V2).

Every driver-radar axis is measured in its own incomparable unit — km/h for top
speed, seconds for pace deltas, positions for racecraft, dimensionless ratios
for cornering. Before they can share one 0-100 polar chart they have to be
scaled onto a common axis, and the *direction* has to be unified too (a lower
lap-time delta is good; a lower top speed is bad).

This module is the single place that scaling and direction-inversion live.
Feature/extractor code produces raw per-driver numbers and hands them here; the
renderer only ever sees 0-100. Two scaling methods are supported:

  * ``"percentile"`` — rank the value against the whole field (the session's
    full grid, or every driver in the season). Best for "who is best at this
    right now" axes where the field itself is the reference. A degenerate field
    (fewer than two distinct values, e.g. a single driver or everyone equal)
    collapses to 50.0 so a one-driver hero chart still renders sensibly.
  * ``"absolute"`` — map against fixed ``lo``/``hi`` reference bands. Best for
    self-normalizing ratio axes (cornering, braveness) that are meaningful on
    their own and shouldn't shift as the comparison set changes.

``None`` raw values pass straight through as ``None`` (missing telemetry, too
few laps, a DNF with no classified pace) so the renderer can draw a collapsed
spoke rather than a misleading zero.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

# A field with fewer than this many distinct values can't produce a meaningful
# percentile, so we fall back to the neutral midpoint.
_NEUTRAL = 50.0


def percentile_rank(value: float, population: Sequence[float]) -> float:
    """Percentile of ``value`` within ``population`` on a 0-100 scale.

    Uses the midpoint/average-rank convention: the fraction of the population
    strictly below the value plus half the fraction equal to it, so identical
    values share the same rank instead of one arbitrarily beating the other.
    Returns :data:`_NEUTRAL` when the population has fewer than two distinct
    values (nothing to rank against).
    """
    pts = [p for p in population if p is not None]
    if len(pts) < 2 or len(set(pts)) < 2:
        return _NEUTRAL
    below = sum(1 for p in pts if p < value)
    equal = sum(1 for p in pts if p == value)
    return 100.0 * (below + 0.5 * equal) / len(pts)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def scale_value(
    value: Optional[float],
    population: Sequence[float],
    higher_is_better: bool = True,
    method: str = "percentile",
    lo: Optional[float] = None,
    hi: Optional[float] = None,
) -> Optional[float]:
    """Scale one raw axis value to 0-100, unifying direction.

    ``None`` passes through as ``None``. For ``method="percentile"`` the value is
    ranked within ``population``; for ``method="absolute"`` it is mapped onto the
    ``lo``/``hi`` band. When ``higher_is_better`` is ``False`` the scale is
    inverted so that, on the returned 0-100 axis, larger is always better.
    """
    if value is None:
        return None

    if method == "absolute":
        if lo is None or hi is None or hi == lo:
            return _NEUTRAL
        score = 100.0 * (value - lo) / (hi - lo)
        score = _clamp(score, 0.0, 100.0)
    else:  # percentile
        score = percentile_rank(value, population)

    if not higher_is_better:
        score = 100.0 - score
    return round(score, 1)


def scale_population(
    values: Sequence[Optional[float]],
    higher_is_better: bool = True,
    method: str = "percentile",
    lo: Optional[float] = None,
    hi: Optional[float] = None,
) -> List[Optional[float]]:
    """Scale a whole list of raw values against itself (convenience wrapper).

    The non-``None`` entries form the percentile population, so this is the
    shortcut for the common case where the drivers being scaled *are* the field.
    """
    population = [v for v in values if v is not None]
    return [
        scale_value(v, population, higher_is_better, method, lo, hi)
        for v in values
    ]
