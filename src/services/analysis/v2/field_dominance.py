"""
Field dominance map (V2, livetiming-backed): who owns each part of the lap.

Every driver's fastest clean lap is cut into ``NUM_MINISECTORS`` equal
lap-fraction minisectors. For each minisector each candidate's time spent is
``t(end) - t(start)`` on its own lap; the candidate with the least time owns it,
and ``margin_s`` is how much the runner-up lost.

Semantics match the two-driver ``track_comparison`` minisector chart (equal
distance slices, fastest through each slice wins) but rank on time spent rather
than mean speed, which is what actually decides a lap and is exact rather than
sample-count dependent.

Alignment: the slices are cut on the pole lap's racing line and every lap is
timed where its X/Y passes each boundary (:func:`reference_line`). Aligning laps
by their own integrated-speed distance fraction
(:func:`_lap_duel_core.resample_by_fraction`) was tried first and rejected: 4 Hz
CarData makes it drift by tens of metres mid-lap, and a noisy lap then "wins"
minisectors it has no business winning. ``resample_by_fraction`` remains the
fallback for laps without position data and supplies the outline.

``mode="team"`` ranks each team's faster driver; ``mode="driver"`` ranks every
driver. ``top_n`` (2..10) keeps only the fastest ``top_n`` candidates in either
mode. Singleton per (mode, top_n) per session.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2 import _lap_duel_core as laps
from src.services.analysis.v2._field_laps import load_field_laps
from src.services.plotting.colors import CONTRAST_FALLBACKS, color_distance, contrast_color, teammate_alt_color
from src.services.analysis.v2._helpers import (
    XY_UNITS_PER_METRE,
    build_session_store,
    get_circuit_info_for_session,
    shared_session_stores,
)

logger = get_logger(__name__)

DATA_TYPE = "field_dominance"
NUM_MINISECTORS = 25
MODES = ("team", "driver")
MIN_TOP_N = 2
MAX_TOP_N = 10
OUTLINE_POINTS = 400
REF_STEP_M = 1.0        # reference-line resolution
WINDOW_M = 350.0        # how far ahead of the previous sample the projection searches
WRAP_M = 400.0          # reference-line extension either side of the timing line
COLOUR_MIN_DISTANCE = 30.0    # perceptual (CIE76) distance below which two owners look the same
COLOUR_LIGHTEN_AMOUNT = 0.45  # last-resort mix toward white for the slower of two clashing owners

# Livetiming ``TeamName`` -> three-letter label for the owner list.
TEAM_CODES = {
    "mercedes": "MER", "ferrari": "FER", "mclaren": "MCL", "red bull racing": "RBR", "red bull": "RBR",
    "aston martin": "AMR", "alpine": "ALP", "williams": "WIL", "haas f1 team": "HAA", "haas": "HAA",
    "racing bulls": "RB", "rb": "RB", "visa cash app rb": "RB", "kick sauber": "SAU", "sauber": "SAU",
    "audi": "AUD", "cadillac": "CAD", "alphatauri": "AT", "alfa romeo": "ARR", "renault": "REN",
}

METHOD = {
    "minisectors": NUM_MINISECTORS,
    "owner": "least time spent in the minisector, measured on each driver's fastest clean lap",
    "margin_s": "time the second-best candidate lost in that minisector",
    "grid": "equal slices of the pole lap's racing line; each lap is timed where its X/Y passes each boundary "
            "(integrated-distance fraction when there is no position data)",
}


def _unavailable(year: int, identifier: Any, session: str, reason: str) -> DataNotAvailableError:
    return DataNotAvailableError(year=year, gp=identifier, session=session, source="livetiming", reason=reason)


def team_code(team: Optional[str]) -> str:
    """Short team label; falls back to the first three letters, upper case."""
    key = (team or "").strip().lower()
    if key in TEAM_CODES:
        return TEAM_CODES[key]
    letters = [c for c in (team or "?") if c.isalnum()]
    return "".join(letters[:3]).upper() or "?"


def validate_args(mode: str, top_n: Optional[int]) -> Tuple[str, Optional[int]]:
    """Normalise ``(mode, top_n)``; ``ValueError`` on anything unsupported."""
    mode = (mode or "").strip().lower()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)} (got {mode!r})")
    if top_n is not None:
        try:
            top_n = int(top_n)
        except (TypeError, ValueError):
            raise ValueError(f"top_n must be an integer between {MIN_TOP_N} and {MAX_TOP_N}") from None
        if not MIN_TOP_N <= top_n <= MAX_TOP_N:
            raise ValueError(f"top_n must be between {MIN_TOP_N} and {MAX_TOP_N} (got {top_n})")
    return mode, top_n


def data_type(mode: str = "team", top_n: Optional[int] = None) -> str:
    """Stored ``data_type`` key: ``field_dominance_{mode}`` plus ``_top{n}``."""
    key = f"{DATA_TYPE}_{mode}"
    return f"{key}_top{top_n}" if top_n else key


# ----------------------------------------------------------------------
# Pure math
# ----------------------------------------------------------------------
def reference_line(trace) -> Optional[Dict[str, np.ndarray]]:
    """The pole lap's racing line as a dense polyline for projecting other laps onto.

    Returns ``x, y`` (metres) and ``frac`` (lap fraction by arc length), extended
    ``WRAP_M`` before the line (negative fractions) and after it (fractions > 1)
    so a lap whose timing window starts a few metres early or late still lands
    on it. ``None`` when the trace has no usable X/Y.
    """
    x = trace["x"].to_numpy(dtype=float) / XY_UNITS_PER_METRE
    y = trace["y"].to_numpy(dtype=float) / XY_UNITS_PER_METRE
    if len(x) < 3 or np.any(np.isnan(x)) or np.any(np.isnan(y)):
        return None
    s = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    length = float(s[-1])
    if length <= 0:
        return None
    ss = np.arange(0.0, length, REF_STEP_M)
    rx, ry = np.interp(ss, s, x), np.interp(ss, s, y)
    k = int(WRAP_M / REF_STEP_M)
    return {
        "x": np.concatenate([rx[-k:], rx, rx[:k]]),
        "y": np.concatenate([ry[-k:], ry, ry[:k]]),
        "frac": np.concatenate([ss[-k:] / length - 1.0, ss / length, ss[:k] / length + 1.0]),
        "origin": np.array(k),
    }


def _fractions_by_projection(trace, ref: Dict[str, np.ndarray]) -> np.ndarray:
    """Lap fraction of every trace sample: nearest point on ``ref``, searched forward only."""
    x = trace["x"].to_numpy(dtype=float) / XY_UNITS_PER_METRE
    y = trace["y"].to_numpy(dtype=float) / XY_UNITS_PER_METRE
    rx, ry, rf = ref["x"], ref["y"], ref["frac"]
    window = int(WINDOW_M / REF_STEP_M)
    prev = int(ref["origin"])
    out = np.empty(len(x))
    for i in range(len(x)):
        lo = max(prev - window, 0) if i == 0 else prev
        hi = min(prev + window, len(rx))
        prev = lo + int(np.argmin(np.hypot(rx[lo:hi] - x[i], ry[lo:hi] - y[i])))
        out[i] = rf[prev]
    return np.maximum.accumulate(out)


def _time_at(edges: np.ndarray, frac: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Time at each lap-fraction edge, extrapolating linearly just outside the sampled range."""
    out = np.interp(edges, frac, t)
    if frac[1] > frac[0] and edges[0] < frac[0]:
        out[0] = t[0] - (frac[0] - edges[0]) * (t[1] - t[0]) / (frac[1] - frac[0])
    if frac[-1] > frac[-2] and edges[-1] > frac[-1]:
        out[-1] = t[-1] + (edges[-1] - frac[-1]) * (t[-1] - t[-2]) / (frac[-1] - frac[-2])
    return out


def minisector_times(trace, n: int = NUM_MINISECTORS, ref: Optional[Dict[str, np.ndarray]] = None) -> np.ndarray:
    """Seconds spent in each of ``n`` equal lap-fraction minisectors.

    With ``ref`` (see :func:`reference_line`) the boundaries are placed by
    *position*: the moment the car passes each point of the reference line.
    Without it the lap is aligned by its own integrated-speed distance
    (:func:`_lap_duel_core.resample_by_fraction`), which is exact for synthetic
    laps but drifts by tens of metres mid-lap on real 4 Hz CarData.
    """
    edges = np.linspace(0.0, 1.0, n + 1)
    if ref is not None:
        frac = _fractions_by_projection(trace, ref)
        if frac[-1] - frac[0] > 0.5:
            return np.diff(_time_at(edges, frac, trace["t"].to_numpy(dtype=float)))
    grid = laps.resample_by_fraction(trace, 1.0)
    return np.diff(np.interp(edges, np.linspace(0.0, 1.0, len(grid["t"])), grid["t"]))


def _lighten(hex_colour: str, amount: float) -> str:
    h = hex_colour.lstrip("#")
    if len(h) != 6:
        return hex_colour
    rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    rgb = [round(c + (255 - c) * amount) for c in rgb]
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _rgb(hex_colour: str) -> Optional[Tuple[int, int, int]]:
    h = (hex_colour or "").lstrip("#")
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def _colour_distance(a: str, b: str) -> float:
    """Perceptual (CIE76) distance between two ``#RRGGBB`` colours (``inf`` when either does not parse).

    RGB distance let Ferrari red and Audi red (~17 perceptually) pass as different.
    """
    if _rgb(a) is None or _rgb(b) is None:
        return float("inf")
    return color_distance(a, b)


def separate_colours(candidates: List[Dict[str, Any]]) -> None:
    """Recolour any candidate within ``COLOUR_MIN_DISTANCE`` of an earlier one (in place).

    Different teams can share a livery colour -- Ferrari and Audi are both red, Haas and Cadillac both grey --
    and a dominance map is unreadable when two owners look identical. ``candidates`` is fastest first, so the
    slower car is the one that changes: to its livery's second colour when that is clear of every other
    owner, else the fixed contrast palette or a lightened primary, whichever is furthest from all of them.
    (Lightening alone cannot separate two greys.)
    """
    for i, cand in enumerate(candidates):
        earlier = [c["color"] for c in candidates[:i]]
        if all(_colour_distance(cand["color"], o) >= COLOUR_MIN_DISTANCE for o in earlier):
            continue
        options = [teammate_alt_color(cand.get("team") or cand.get("name") or "")]
        options += list(CONTRAST_FALLBACKS) + [_lighten(cand["color"], COLOUR_LIGHTEN_AMOUNT)]
        # Stay clear of the slower owners too, so a replacement does not push its clash further down.
        others = earlier + [c["color"] for c in candidates[i + 1:]]
        options = [o for o in options if o and o not in others]

        def clearance(colour: str) -> float:
            return min(_colour_distance(colour, o) for o in others)

        clear = [o for o in options if clearance(o) >= COLOUR_MIN_DISTANCE]
        cand["color"] = clear[0] if clear else max(options, key=clearance)


def select_candidates(field: Dict[str, Dict[str, Any]], mode: str, top_n: Optional[int]) -> List[Dict[str, Any]]:
    """Candidates ranked by lap time (fastest first), one per team or per driver.

    In driver mode teammates share a colour; the slower one takes the livery's
    second colour (:func:`colors.contrast_color`) so the map can tell them apart.
    In team mode teams whose colours are nearly identical get lightened
    (:func:`separate_colours`).
    """
    rows = []
    for num, lap in field.items():
        rows.append({
            "number": str(num), "driver": lap.get("tla") or str(num), "team": lap.get("team") or "Unknown",
            "color": lap.get("color") or "#888888", "trace": lap["trace"], "lap_time": float(lap["lap_time"]),
        })
    rows.sort(key=lambda r: r["lap_time"])
    if mode == "team":
        seen: set = set()
        picked = []
        for r in rows:
            if r["team"] in seen:
                continue
            seen.add(r["team"])
            picked.append({**r, "name": r["team"], "code": team_code(r["team"])})
        separate_colours(picked)
        rows = picked
    else:
        team_lead: Dict[str, str] = {}
        for r in rows:
            r["name"], r["code"] = r["driver"], r["driver"]
            if r["team"] in team_lead:
                r["color"] = contrast_color(team_lead[r["team"]], r["color"], r["team"])
            else:
                team_lead[r["team"]] = r["color"]
    return rows[:top_n] if top_n else rows


def compute_ownership(candidates: List[Dict[str, Any]], n: int = NUM_MINISECTORS) -> Dict[str, Any]:
    """Owner index, margin and per-candidate times for every minisector.

    ``candidates`` must be sorted fastest lap first: ties go to the earlier one.
    """
    if len(candidates) < 2:
        raise ValueError("need at least two candidates")
    ref = reference_line(candidates[0]["trace"])
    times = np.vstack([minisector_times(c["trace"], n, ref) for c in candidates])   # (candidates, n)
    order = np.argsort(times, axis=0, kind="stable")
    owner = order[0]
    cols = np.arange(n)
    margin = times[order[1], cols] - times[owner, cols]
    return {"times": times, "owner": owner, "margin": margin}


def _outline(trace) -> Optional[Dict[str, List[float]]]:
    x = trace["x"].to_numpy(dtype=float)
    if len(x) < 2 or np.all(np.isnan(x)):
        return None
    grid = laps.resample_by_fraction(trace, 1.0)
    idx = np.linspace(0, len(grid["x"]) - 1, OUTLINE_POINTS).round().astype(int)
    return {
        "x": [round(float(v), 0) for v in grid["x"][idx]],
        "y": [round(float(v), 0) for v in grid["y"][idx]],
        "fraction": [round(float(v), 4) for v in grid["distance"][idx]],
    }


def build_payload(candidates: List[Dict[str, Any]], rotation: float, mode: str, top_n: Optional[int],
                  year: int, event_name: str, session: str, n: int = NUM_MINISECTORS) -> Dict[str, Any]:
    """Assemble the payload from ranked candidates (test seam)."""
    res = compute_ownership(candidates, n)
    owner_idx, margin = res["owner"], res["margin"]
    counts = np.bincount(owner_idx, minlength=len(candidates))

    minisectors = [{
        "index": i,
        "start_fraction": round(i / n, 4),
        "end_fraction": round((i + 1) / n, 4),
        "owner": candidates[int(owner_idx[i])]["name"],
        "owner_color": candidates[int(owner_idx[i])]["color"],
        "margin_s": round(float(margin[i]), 3),
    } for i in range(n)]

    def _cand(c: Dict[str, Any], count: int) -> Dict[str, Any]:
        return {"name": c["name"], "code": c["code"], "color": c["color"], "count": int(count),
                "driver": c["driver"], "team": c["team"]}

    ranked = sorted(range(len(candidates)), key=lambda i: (-int(counts[i]), candidates[i]["lap_time"]))
    owners = [_cand(candidates[i], counts[i]) for i in ranked if counts[i] > 0]
    pole = candidates[0]
    big = int(np.argmax(margin))
    close = int(np.argmin(margin))
    top = owners[0]
    highlights = {
        "pole": {"driver": pole["driver"], "team": pole["team"], "lap_time_s": round(pole["lap_time"], 3),
                 "lapTime": laps.format_lap_time(pole["lap_time"])},
        "most_owned": {"name": top["name"], "code": top["code"], "count": top["count"]},
        "biggest_margin": {"index": big, "owner": minisectors[big]["owner"], "margin_s": minisectors[big]["margin_s"]},
        "closest_margin": {"index": close, "owner": minisectors[close]["owner"],
                           "margin_s": minisectors[close]["margin_s"]},
        "owner_count": len(owners),
    }
    outline = _outline(pole["trace"])
    return {
        "mode": mode,
        "top_n": top_n,
        "minisector_count": n,
        "minisectors": minisectors,
        "track": {**outline, "rotation": rotation} if outline else None,
        "owners": owners,
        "candidates": [{**_cand(c, counts[i]), "lap_time_s": round(c["lap_time"], 3)}
                       for i, c in enumerate(candidates)],
        "highlights": highlights,
        "method": METHOD,
        "session_info": {"year": year, "event_name": event_name, "session_name": session},
    }


# ----------------------------------------------------------------------
# Fetch + public callables
# ----------------------------------------------------------------------
def _generate(year: int, identifier: Any, session: str, mode: str, top_n: Optional[int]) -> Dict[str, Any]:
    with shared_session_stores():
        store = build_session_store(year, identifier, session)
        if store is None:
            raise _unavailable(year, identifier, session, "Session could not be resolved")
        candidates = select_candidates(load_field_laps(store), mode, top_n)
        if len(candidates) < 2:
            raise _unavailable(year, identifier, session, "Fewer than two comparable laps")
        circuit = get_circuit_info_for_session(store) or {}
        return build_payload(candidates, circuit.get("rotation", 0) or 0, mode, top_n, year,
                             store.event_name, session)


class FieldDominanceData:
    """Callable: ``FieldDominanceData()(year, identifier, session, mode="team", top_n=None) -> dict``."""

    def __call__(self, y: int, identifier: Union[int, str], e: str, mode: str = "team",
                 top_n: Optional[int] = None) -> Dict[str, Any]:
        mode, top_n = validate_args(mode, top_n)
        return cached_or_generate(
            year=y, identifier=identifier, session=e, data_type=data_type(mode, top_n),
            generator=lambda: _generate(y, identifier, e, mode, top_n), version="v2",
        )


class FieldDominancePlot:
    """Callable: same arguments as :class:`FieldDominanceData` plus ``fmt`` -> PNG path.

    ``fmt`` is one of ``canvas.FORMAT_NAMES``; ``None`` renders landscape.
    """

    def __call__(self, y: int, identifier: Union[int, str], e: str, mode: str = "team",
                 top_n: Optional[int] = None, fmt: Optional[str] = None) -> str:
        from src.services.analysis.v2 import _field_render
        from src.services.plotting.canvas import get_format

        if fmt is not None:
            get_format(fmt)  # validate before the expensive part
        mode, top_n = validate_args(mode, top_n)
        payload = FieldDominanceData()(y, identifier, e, mode=mode, top_n=top_n)
        return _field_render.render_dominance(payload, fmt)
