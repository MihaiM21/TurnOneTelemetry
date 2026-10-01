"""
Car characteristics (V2, livetiming-backed): two field-wide charts from each team's best lap.

* ``corner_speed_profile`` -- how fast every team takes the slow, medium and
  fast corners of the circuit (mean apex speed per class, gap to the class best).
* ``efficiency_scatter`` -- top speed against mean apex speed, the classic
  drag-versus-downforce picture.

Both start from :func:`_field_laps.load_field_laps` (every driver's fastest
clean lap on a distance axis with X/Y) and share ONE private computation,
:func:`analyse`, so the two charts always agree on which corners exist, which
class each belongs to and what every team's apex speed was.

Method
------
* Per team, the faster driver's fastest lap.
* The corner set is the circuit's corner list projected onto the pole lap
  (:func:`_lap_duel_core.place_corners`, which falls back to speed-minimum
  detection). A corner's distance on another team's lap is found by lap
  *fraction*, so a 0.5 % difference in integrated distance does not slide the
  window off the apex.
* Apex speed = the minimum speed within ``+-APEX_WINDOW_M`` of the corner.
* A corner's class comes from the FIELD MEDIAN apex speed there: slow
  ``< SLOW_MAX_KMH``, fast ``> FAST_MIN_KMH``, medium in between. Corners whose
  median apex is above ``KINK_MIN_KMH`` are flat-out kinks and are dropped;
  pieces of a chicane closer than ``MERGE_GAP_M`` collapse to the slower one.

Singleton per session, any session type. Payloads are documented on
:func:`compute_profile` and :func:`compute_efficiency`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2 import _lap_duel_core as laps
from src.services.analysis.v2._field_laps import load_field_laps
from src.services.analysis.v2._helpers import (
    build_session_store,
    get_circuit_info_for_session,
    shared_session_stores,
)

logger = get_logger(__name__)

PROFILE_DATA_TYPE = "corner_speed_profile"
EFFICIENCY_DATA_TYPE = "efficiency_scatter"

CLASSES = ("slow", "medium", "fast")
SLOW_MAX_KMH = 120.0       # median apex below this: slow
FAST_MIN_KMH = 200.0       # median apex above this: fast (medium is 120..200 inclusive)
KINK_MIN_KMH = 280.0       # median apex above this: not a corner, a flat-out kink
APEX_WINDOW_M = 50.0
MERGE_GAP_M = 60.0

CLASS_RULES = {
    "slow": {"max_kmh": SLOW_MAX_KMH},
    "medium": {"min_kmh": SLOW_MAX_KMH, "max_kmh": FAST_MIN_KMH},
    "fast": {"min_kmh": FAST_MIN_KMH, "max_kmh": KINK_MIN_KMH},
}

_SHORT_NAMES = {
    "red bull racing": "Red Bull",
    "haas f1 team": "Haas",
    "kick sauber": "Sauber",
    "racing bulls": "RB",
    "visa cash app rb": "RB",
    "rb": "RB",
    "alphatauri": "AlphaTauri",
    "scuderia ferrari": "Ferrari",
}
_DROP_WORDS = {"f1", "team", "racing", "scuderia", "formula", "one"}


def _unavailable(year: int, identifier: Any, session: str, reason: str) -> DataNotAvailableError:
    return DataNotAvailableError(year=year, gp=identifier, session=session, source="livetiming", reason=reason)


def short_team_name(team: Optional[str]) -> str:
    """A label-sized team name ("Red Bull Racing" -> "Red Bull")."""
    name = (team or "").strip()
    if not name:
        return "?"
    known = _SHORT_NAMES.get(name.lower())
    if known:
        return known
    kept = [w for w in name.split() if w.lower() not in _DROP_WORDS]
    return " ".join(kept) or name


def classify(median_apex_kmh: float) -> Optional[str]:
    """``slow`` / ``medium`` / ``fast`` for a field-median apex speed, ``None`` for a flat-out kink."""
    if median_apex_kmh > KINK_MIN_KMH:
        return None
    if median_apex_kmh < SLOW_MAX_KMH:
        return "slow"
    if median_apex_kmh > FAST_MIN_KMH:
        return "fast"
    return "medium"


# ----------------------------------------------------------------------
# Shared computation
# ----------------------------------------------------------------------
def best_lap_per_team(field_laps: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One entry per team: its faster driver's lap, fastest team first."""
    best: Dict[str, Dict[str, Any]] = {}
    for num, lap in field_laps.items():
        trace = lap.get("trace")
        if trace is None or len(trace) < 20 or not lap.get("lap_time"):
            continue
        team = lap.get("team") or lap.get("tla") or str(num)
        if team not in best or lap["lap_time"] < best[team]["lap_time"]:
            best[team] = {**lap, "team": team, "number": str(num)}
    return sorted(best.values(), key=lambda lap: lap["lap_time"])


def _apex_speeds(grid: Dict[str, np.ndarray], fractions: Sequence[float]) -> List[float]:
    """Minimum speed within ``APEX_WINDOW_M`` of each lap fraction, on this lap's own distance."""
    dist = grid["own_distance"]
    length = float(grid["length_m"])
    out = []
    for frac in fractions:
        centre = float(frac) * length
        mask = np.abs(dist - centre) <= APEX_WINDOW_M
        if not np.any(mask):
            mask = np.abs(dist - centre) <= float(np.min(np.abs(dist - centre))) + 1e-9
        out.append(float(np.min(grid["speed"][mask])))
    return out


def _merge_chicanes(corners: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse runs of corners each within ``MERGE_GAP_M`` of the previous, keeping the slowest."""
    merged: List[List[Dict[str, Any]]] = []
    for c in sorted(corners, key=lambda c: c["distance_m"]):
        if merged and c["distance_m"] - merged[-1][-1]["distance_m"] <= MERGE_GAP_M:
            merged[-1].append(c)
        else:
            merged.append([c])
    out = []
    for group in merged:
        keep = dict(min(group, key=lambda c: c["median_apex_kmh"]))
        keep["merged"] = [c["number"] for c in group if c is not keep and c["number"] != keep["number"]]
        out.append(keep)
    return out


def analyse(field_laps: Dict[str, Dict[str, Any]],
            circuit_corners: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Everything both charts need, computed once (pure: no store, no network).

    Returns ``{"teams": [...], "corners": [...], "skipped": [...], "reference": {...}}``.
    Each team is ``{team, short, driver, color, lap_time_s, top_speed_kmh, apex}`` where ``apex`` lists the
    team's apex speed at each of ``corners`` (same order). Each corner is
    ``{number, distance_m, median_apex_kmh, class, merged}`` on the pole lap's distance frame.
    """
    entries = best_lap_per_team(field_laps)
    if not entries:
        raise ValueError("no lap telemetry")
    pole = entries[0]
    pole_len = float(pole["trace"]["d"].iloc[-1])
    if pole_len <= 0:
        raise ValueError("pole lap has no distance")
    pole_grid = laps.resample_by_fraction(pole["trace"], pole_len)
    placed = laps.place_corners(pole_grid, circuit_corners)
    fractions = [c["distance_m"] / pole_len for c in placed]

    grids = [laps.resample_by_fraction(e["trace"], float(e["trace"]["d"].iloc[-1])) for e in entries]
    apexes = [_apex_speeds(g, fractions) for g in grids]
    medians = np.median(np.array(apexes), axis=0) if fractions else np.array([])

    kept_idx: List[int] = []
    skipped: List[int] = []
    candidates: List[Dict[str, Any]] = []
    for i, c in enumerate(placed):
        cls = classify(float(medians[i]))
        if cls is None:
            skipped.append(c["number"])
            continue
        candidates.append({"number": c["number"], "distance_m": c["distance_m"],
                           "median_apex_kmh": round(float(medians[i]), 1), "class": cls, "_idx": i})
    corners = _merge_chicanes(candidates)
    kept_idx = [c.pop("_idx") for c in corners]

    teams = []
    for e, apex in zip(entries, apexes):
        teams.append({
            "team": e["team"],
            "short": short_team_name(e["team"]),
            "driver": e.get("tla"),
            "color": e.get("color"),
            "lap_time_s": round(float(e["lap_time"]), 3),
            "top_speed_kmh": round(float(np.nanmax(e["trace"]["speed"].to_numpy(dtype=float))), 1),
            "apex": [round(apex[i], 1) for i in kept_idx],
        })
    return {
        "teams": teams,
        "corners": corners,
        "skipped": skipped,
        "reference": {"driver": pole.get("tla"), "team": pole["team"],
                      "lap_time_s": round(float(pole["lap_time"]), 3), "length_m": round(pole_len, 1)},
    }


def _session_info(year: int, event_name: str, session: str) -> Dict[str, Any]:
    return {"year": year, "event_name": event_name, "session_name": session}


def _mean(values: Sequence[float]) -> float:
    return float(np.mean(values)) if len(values) else float("nan")


# ----------------------------------------------------------------------
# Payloads
# ----------------------------------------------------------------------
def compute_profile(field_laps: Dict[str, Dict[str, Any]], circuit_corners: Optional[List[Dict[str, Any]]],
                    year: int = 0, event_name: str = "", session: str = "") -> Dict[str, Any]:
    """The ``corner_speed_profile`` payload.

    ``{"classes": {"slow"|"medium"|"fast": {"corners": [numbers], "teams": [{team, short, driver, color,
    avg_kmh, delta_kmh}]}}, "corners": [{number, distance_m, median_apex_kmh, class, merged}],
    "highlights": {"best_per_class", "biggest_spread"}, "rules", "reference", "session_info"}``.

    ``teams`` is sorted fastest first; ``delta_kmh`` is the gap to the class best (0 for the best, negative
    for everyone else). A class with no corners has empty lists.
    """
    a = analyse(field_laps, circuit_corners)
    classes: Dict[str, Dict[str, Any]] = {}
    for cls in CLASSES:
        idx = [i for i, c in enumerate(a["corners"]) if c["class"] == cls]
        rows = []
        for t in a["teams"]:
            avg = _mean([t["apex"][i] for i in idx])
            if not np.isnan(avg):
                rows.append({"team": t["team"], "short": t["short"], "driver": t["driver"],
                             "color": t["color"], "avg_kmh": round(avg, 1)})
        rows.sort(key=lambda r: -r["avg_kmh"])
        best = rows[0]["avg_kmh"] if rows else 0.0
        for r in rows:
            r["delta_kmh"] = round(r["avg_kmh"] - best, 1)
        classes[cls] = {"corners": [a["corners"][i]["number"] for i in idx], "teams": rows}

    best_per_class = {cls: {"team": c["teams"][0]["team"], "avg_kmh": c["teams"][0]["avg_kmh"]}
                      for cls, c in classes.items() if c["teams"]}
    spreads = [(cls, c["teams"][0]["avg_kmh"] - c["teams"][-1]["avg_kmh"], c) for cls, c in classes.items()
               if len(c["teams"]) > 1]
    biggest = None
    if spreads:
        cls, spread, c = max(spreads, key=lambda s: s[1])
        biggest = {"class": cls, "spread_kmh": round(spread, 1),
                   "best_team": c["teams"][0]["team"], "worst_team": c["teams"][-1]["team"]}
    return {
        "classes": classes,
        "corners": a["corners"],
        "highlights": {"best_per_class": best_per_class, "biggest_spread": biggest},
        "rules": {**CLASS_RULES, "apex_window_m": APEX_WINDOW_M, "merge_gap_m": MERGE_GAP_M,
                  "skipped_corners": a["skipped"]},
        "reference": a["reference"],
        "session_info": _session_info(year, event_name, session),
    }


def compute_efficiency(field_laps: Dict[str, Dict[str, Any]], circuit_corners: Optional[List[Dict[str, Any]]],
                       year: int = 0, event_name: str = "", session: str = "") -> Dict[str, Any]:
    """The ``efficiency_scatter`` payload.

    ``{"teams": [{team, short, driver, color, top_speed_kmh, avg_apex_kmh, lap_time_s}], "field_median": {x, y},
    "highlights": {top_speed, apex, most_efficient}, "corners": [numbers used], "reference", "session_info"}``.
    ``avg_apex_kmh`` averages the apex speeds over the SAME classified corners as the corner profile.
    """
    a = analyse(field_laps, circuit_corners)
    teams = []
    for t in a["teams"]:
        if not t["apex"]:
            continue
        teams.append({"team": t["team"], "short": t["short"], "driver": t["driver"], "color": t["color"],
                      "top_speed_kmh": t["top_speed_kmh"], "avg_apex_kmh": round(_mean(t["apex"]), 1),
                      "lap_time_s": t["lap_time_s"]})
    if not teams:
        raise ValueError("no classified corners")
    xs = np.array([t["top_speed_kmh"] for t in teams])
    ys = np.array([t["avg_apex_kmh"] for t in teams])

    def norm(v: np.ndarray) -> np.ndarray:
        span = float(v.max() - v.min())
        return (v - v.min()) / span if span > 0 else np.zeros_like(v)

    score = norm(xs) + norm(ys)
    fastest = max(teams, key=lambda t: t["top_speed_kmh"])
    grippiest = max(teams, key=lambda t: t["avg_apex_kmh"])
    return {
        "teams": teams,
        "field_median": {"x": round(float(np.median(xs)), 1), "y": round(float(np.median(ys)), 1)},
        "highlights": {
            "top_speed": {"team": fastest["team"], "kmh": fastest["top_speed_kmh"]},
            "apex": {"team": grippiest["team"], "kmh": grippiest["avg_apex_kmh"]},
            "most_efficient": {"team": teams[int(np.argmax(score))]["team"]},
        },
        "corners": [c["number"] for c in a["corners"]],
        "reference": a["reference"],
        "session_info": _session_info(year, event_name, session),
    }


# ----------------------------------------------------------------------
# Fetching
# ----------------------------------------------------------------------
def _generate(year: int, identifier: Any, session: str, builder) -> Dict[str, Any]:
    with shared_session_stores():
        store = build_session_store(year, identifier, session)
        if store is None:
            raise _unavailable(year, identifier, session, "Session could not be resolved")
        field = load_field_laps(store)
        if not field:
            raise _unavailable(year, identifier, session, "No lap telemetry available")
        circuit = get_circuit_info_for_session(store) or {}
        try:
            return builder(field, circuit.get("corners"), year, store.event_name, session)
        except ValueError as exc:
            raise _unavailable(year, identifier, session, str(exc)) from exc


class CornerSpeedProfileData:
    """Callable: ``CornerSpeedProfileData()(year, identifier, session) -> dict``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> Dict[str, Any]:
        return cached_or_generate(
            year=y, identifier=identifier, session=e, data_type=PROFILE_DATA_TYPE,
            generator=lambda: _generate(y, identifier, e, compute_profile), version="v2",
        )


class EfficiencyScatterData:
    """Callable: ``EfficiencyScatterData()(year, identifier, session) -> dict``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> Dict[str, Any]:
        return cached_or_generate(
            year=y, identifier=identifier, session=e, data_type=EFFICIENCY_DATA_TYPE,
            generator=lambda: _generate(y, identifier, e, compute_efficiency), version="v2",
        )


class CornerSpeedProfilePlot:
    """Callable: same arguments as the data class plus ``fmt`` (``None`` = landscape) -> PNG path."""

    def __call__(self, y: int, identifier: Union[int, str], e: str, fmt: Optional[str] = None) -> str:
        from src.services.analysis.v2 import _car_characteristics_render as render
        from src.services.plotting.canvas import get_format

        if fmt is not None:
            get_format(fmt)  # validate before the expensive part
        return render.render_profile(CornerSpeedProfileData()(y, identifier, e), fmt)


class EfficiencyScatterPlot:
    """Callable: same arguments as the data class plus ``fmt`` (``None`` = landscape) -> PNG path."""

    def __call__(self, y: int, identifier: Union[int, str], e: str, fmt: Optional[str] = None) -> str:
        from src.services.analysis.v2 import _car_characteristics_render as render
        from src.services.plotting.canvas import get_format

        if fmt is not None:
            get_format(fmt)  # validate before the expensive part
        return render.render_efficiency(EfficiencyScatterData()(y, identifier, e), fmt)
