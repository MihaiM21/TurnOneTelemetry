"""
Lap Duel (V2, livetiming-backed) -- the flagship two-lap comparison.

Two laps, any two: each side is ``(year, gp, session, driver, lap?, segment?)``.

* default: each driver's fastest clean lap in the session
* ``lap=N``: that lap number (a race battle, a driver against themselves)
* ``segment=Q1|Q2|Q3``: the driver's best lap completed inside that part of
  qualifying (bounds from ``SessionData.jsonStream``)
* side B may name a different session entirely (``year2``/``gp2``/``session2``):
  2025 pole vs 2026 pole, or qualifying vs race pace

Both laps land on one distance grid by lap fraction (see
:mod:`_lap_duel_core`), so the delta ends at exactly the lap-time difference
and cross-year laps line up corner for corner.

The payload's core -- ``a``/``b`` with ``driverCode``, ``lapTime``, ``speed``,
``throttle`` (0..1), ``brake`` (0..1), plus ``delta`` -- matches the Remotion
``telemetry-compare`` schema, so the same JSON drives the animated version.
``highlights`` carries the numbers worth quoting in a post; nothing in it is
drawn as text on the plot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2 import _lap_duel_core as core
from src.services.analysis.v2._helpers import (
    CAR_DATA_CHANNELS,
    build_session_store,
    extract_channels_window,
    extract_positions_window,
    get_circuit_info_for_session,
    resolve_driver,
    resolve_fastest_lap_window,
    shared_session_stores,
)
from src.services.analysis.v2._race_helpers import extract_lap_times
from src.services.plotting.colors import get_driver_color, pair_colors

logger = get_logger(__name__)

DATA_TYPE = "lap_duel"
VALID_SEGMENTS = ("Q1", "Q2", "Q3")
VALID_DETAIL = ("standard", "full")

_TRACK_POINTS = 360
_SERIES_DECIMALS = {"speed": 1, "throttle": 3, "brake": 0, "gear": 0, "rpm": 0, "drs": 0}


@dataclass(frozen=True)
class LapRef:
    """One side of a duel."""

    year: int
    identifier: Union[int, str]
    session: str
    driver: str
    lap: Optional[int] = None
    segment: Optional[str] = None

    @property
    def is_default(self) -> bool:
        return self.lap is None and self.segment is None

    def tag(self) -> str:
        if self.lap is not None:
            return f"L{self.lap}"
        if self.segment:
            return self.segment
        return "best"


def _data_type(tla1: str, tla2: str) -> str:
    """Stored key for the default variant (both fastest laps, same session). Ordered."""
    return f"{DATA_TYPE}_{tla1.upper()}_{tla2.upper()}"


def _variant_key(a: LapRef, b: LapRef, detail: str) -> str:
    """Cache key for any variant; equals :func:`_data_type` for the default one."""
    same_session = (a.year, str(a.identifier), a.session) == (b.year, str(b.identifier), b.session)
    base = _data_type(a.driver, b.driver)
    if a.is_default and b.is_default and same_session and detail == "standard":
        return base
    parts = [base, a.tag(), b.tag()]
    if not same_session:
        parts.append(f"vs_{b.year}_{b.identifier}_{b.session}".replace(" ", ""))
    if detail != "standard":
        parts.append(detail)
    return "_".join(str(p) for p in parts)


def _normalise_segment(segment: Optional[str]) -> Optional[str]:
    if segment is None or str(segment).strip() == "":
        return None
    seg = str(segment).strip().upper()
    if seg not in VALID_SEGMENTS:
        raise ValueError(f"segment must be one of {', '.join(VALID_SEGMENTS)} (got {segment!r})")
    return seg


# ----------------------------------------------------------------------
# Fetching
# ----------------------------------------------------------------------
def _unavailable(ref: LapRef, reason: str) -> DataNotAvailableError:
    return DataNotAvailableError(year=ref.year, gp=ref.identifier, session=ref.session,
                                 source="livetiming", reason=reason)


def _resolve_window(store: Any, ref: LapRef, num: str) -> Tuple[float, float, float, Optional[int]]:
    records = extract_lap_times(store).get(str(num), [])
    if ref.lap is not None:
        win = core.window_for_lap(records, ref.lap)
        if win is None:
            laps = [int(r["lap"]) for r in records if float(r.get("time_s") or 0) > 0]
            span = f"{min(laps)}-{max(laps)}" if laps else "none"
            raise _unavailable(ref, f"{ref.driver} has no timed lap {ref.lap} (timed laps: {span})")
        return win[0], win[1], win[2], ref.lap

    if ref.segment:
        try:
            bounds = core.qualifying_part_bounds(store.extra_stream("session_data"))
        except Exception as exc:
            logger.warning("SessionData unavailable for segment lookup: %s", exc)
            bounds = {}
        part = int(ref.segment[1])
        if part not in bounds:
            raise _unavailable(ref, f"No {ref.segment} in this session")
        best = core.best_window_in_range(records, *bounds[part])
        if best is None:
            raise _unavailable(ref, f"{ref.driver} set no timed lap in {ref.segment}")
        return best

    win = resolve_fastest_lap_window(store.base_url, store.client, num, store=store)
    if win is None:
        raise _unavailable(ref, f"No fastest lap available for {ref.driver}")
    return win[0], win[1], win[2], core.lap_number_for_window(records, win[1])


def _load_side(ref: LapRef) -> Dict[str, Any]:
    store = build_session_store(ref.year, ref.identifier, ref.session)
    if store is None:
        raise _unavailable(ref, "Session could not be resolved")
    num, info = resolve_driver(store, ref.driver)
    start, end, lap_time, lap_no = _resolve_window(store, ref, num)

    car = extract_channels_window(store.base_url, store.client, [num], start, end,
                                  channels=list(CAR_DATA_CHANNELS), store=store, raw_names=True).get(num)
    pos = extract_positions_window(store.base_url, store.client, [num], start, end, store=store).get(num)
    trace = core.build_lap_trace(car, pos, start, lap_time)
    if trace.empty or len(trace) < 20:
        raise _unavailable(ref, f"No telemetry for {ref.driver}'s lap")

    return {
        "ref": ref,
        "store": store,
        "trace": trace,
        "lap_time": lap_time,
        "lap_number": lap_no,
        "team": info.get("team"),
        "color": _color_for(info, ref),
        "racing_number": info.get("racing_number"),
    }


def _color_for(info: Dict[str, Any], ref: LapRef) -> str:
    colour = info.get("color") or ""
    if isinstance(colour, str) and colour.startswith("#") and colour.upper() not in ("#FFFFFF", "#FFF"):
        return colour
    return get_driver_color(ref.driver, ref.year)


# ----------------------------------------------------------------------
# Payload
# ----------------------------------------------------------------------
def _round_list(arr: np.ndarray, decimals: int) -> List[float]:
    if decimals == 0:
        return [int(round(float(v))) for v in arr]
    return [round(float(v), decimals) for v in arr]


def _side_payload(side: Dict[str, Any], grid: Dict[str, np.ndarray], event_name: str) -> Dict[str, Any]:
    ref: LapRef = side["ref"]
    return {
        "driverCode": ref.driver,
        "team": side["team"],
        "color": side["color"],
        "racingNumber": side["racing_number"],
        "lapTime": core.format_lap_time(side["lap_time"]),
        "lap_time_s": round(side["lap_time"], 3),
        "lap_number": side["lap_number"],
        "segment": ref.segment,
        "selection": "segment" if ref.segment else ("lap" if ref.lap is not None else "fastest"),
        "year": ref.year,
        "event_name": event_name,
        "session": ref.session,
        "length_m": round(float(grid["length_m"]), 1),
        "speed": _round_list(grid["speed"], 1),
        "throttle": _round_list(np.clip(grid["throttle"], 0, 100) / 100.0, 3),
        "brake": _round_list((grid["brake"] > 0).astype(float), 0),
        "gear": _round_list(grid["gear"], 0),
        "rpm": _round_list(grid["rpm"], 0),
        "drs": _round_list(grid["drs"], 0),
    }


def _track_payload(grid_a, sections: List[Dict[str, Any]], rotation: float) -> Optional[Dict[str, Any]]:
    """Downsampled racing line of A, each point tagged with who gained in its section.

    Uses the same sections as the speed-panel tint, so map and chart agree.
    """
    if np.all(np.isnan(grid_a["x"])):
        return None
    dist = grid_a["distance"]
    idx = np.linspace(0, len(dist) - 1, _TRACK_POINTS).round().astype(int)
    owner = []
    for i in idx:
        d = dist[i]
        sec = next((s for s in sections if s["start_m"] <= d <= s["end_m"]), None)
        owner.append(sec["gainer"] if sec is not None else "a")
    return {
        "x": _round_list(grid_a["x"][idx], 0),
        "y": _round_list(grid_a["y"][idx], 0),
        "faster": owner,
        "rotation": rotation,
    }


def build_payload(side_a: Dict[str, Any], side_b: Dict[str, Any], circuit_info: Optional[Dict[str, Any]],
                  detail: str = "standard") -> Dict[str, Any]:
    """Assemble the Lap Duel payload from two loaded sides (test seam)."""
    ref_a: LapRef = side_a["ref"]
    ref_b: LapRef = side_b["ref"]
    event_a = side_a["store"].event_name if side_a.get("store") is not None else str(ref_a.identifier)
    event_b = side_b["store"].event_name if side_b.get("store") is not None else str(ref_b.identifier)

    len_a = float(side_a["trace"]["d"].iloc[-1])
    grid_a = core.resample_by_fraction(side_a["trace"], len_a)
    grid_b = core.resample_by_fraction(side_b["trace"], len_a)
    dist = grid_a["distance"]
    delta = core.compute_delta(grid_a["t"], grid_b["t"])

    corners = core.place_corners(grid_a, (circuit_info or {}).get("corners"))
    apexes = core.find_apexes(grid_a, grid_b)
    for apex in apexes:
        apex["braking_point_a_m"] = core.braking_point(dist, grid_a["brake"], apex["distance_m"])
        apex["braking_point_b_m"] = core.braking_point(dist, grid_b["brake"], apex["distance_m"])
        nearest = min(corners, key=lambda c: abs(c["distance_m"] - apex["distance_m"])) if corners else None
        close = nearest is not None and abs(nearest["distance_m"] - apex["distance_m"]) < 120
        apex["corner"] = nearest["number"] if close else None

    sections = core.section_gains(core.build_sections(corners, float(dist[-1])), dist, delta)
    for s in sections:
        s["label"] = core.section_label(s["corners"])
        s["gainer"] = "b" if s["delta_change_s"] < 0 else "a"

    color_a, color_b = pair_colors(side_a["color"], side_b["color"], side_b.get("team"))
    side_a = {**side_a, "color": color_a}
    side_b = {**side_b, "color": color_b}

    ia, ib = int(np.argmax(grid_a["speed"])), int(np.argmax(grid_b["speed"]))
    ranked = sorted((s for s in sections if abs(s["delta_change_s"]) >= 0.005),
                    key=lambda s: abs(s["delta_change_s"]), reverse=True)
    gap = round(side_b["lap_time"] - side_a["lap_time"], 3)
    highlights = {
        "gap_s": gap,
        "faster": ref_a.driver if gap > 0 else ref_b.driver,
        "biggest_swings": [
            {"where": s["label"], "start_m": s["start_m"], "end_m": s["end_m"],
             "gainer": ref_b.driver if s["gainer"] == "b" else ref_a.driver,
             "seconds": round(abs(s["delta_change_s"]), 3)}
            for s in ranked[:3]
        ],
        "top_speed_kmh": {ref_a.driver: round(float(grid_a["speed"][ia]), 1),
                          ref_b.driver: round(float(grid_b["speed"][ib]), 1)},
        "top_speed_at_m": {ref_a.driver: round(float(dist[ia]), 1), ref_b.driver: round(float(dist[ib]), 1)},
        "slowest_apex": (min(apexes, key=lambda a: a["min_speed_a"]) if apexes else None),
        "full_throttle_pct": {ref_a.driver: core.full_throttle_pct(grid_a["throttle"]),
                              ref_b.driver: core.full_throttle_pct(grid_b["throttle"])},
        "braking_deltas_m": [
            {"corner": a["corner"], "distance_m": a["distance_m"],
             "later_braker": (ref_a.driver if a["braking_point_a_m"] > a["braking_point_b_m"] else ref_b.driver),
             "metres": round(abs(a["braking_point_a_m"] - a["braking_point_b_m"]), 1)}
            for a in apexes
            if a["braking_point_a_m"] is not None and a["braking_point_b_m"] is not None
            and abs(a["braking_point_a_m"] - a["braking_point_b_m"]) >= 5.0
        ],
    }

    same_session = (ref_a.year, str(ref_a.identifier), ref_a.session) == \
        (ref_b.year, str(ref_b.identifier), ref_b.session)
    payload: Dict[str, Any] = {
        "a": _side_payload(side_a, grid_a, event_a),
        "b": _side_payload(side_b, grid_b, event_b),
        "distance": _round_list(dist, 1),
        "delta": _round_list(delta, 3),
        "corners": corners,
        "apexes": apexes,
        "sections": sections,
        "track": _track_payload(grid_a, sections, (circuit_info or {}).get("rotation", 0)),
        "highlights": highlights,
        "detail": detail,
        "same_session": same_session,
        "same_team": bool(side_a.get("team") and side_a.get("team") == side_b.get("team")),
        "session_info": {"year": ref_a.year, "event_name": event_a, "session_name": ref_a.session},
        "meta": {
            "delta_convention": "delta = t_b - t_a; positive means b is behind a",
            "distance_basis": "lap fraction, labelled in metres of lap a",
            "source": "F1 live timing (CarData/Position ~4 Hz)",
        },
    }
    if detail == "full":
        long_a, lat_a = core.compute_accelerations(grid_a)
        long_b, lat_b = core.compute_accelerations(grid_b)
        payload["accelerations"] = {
            "a": {"long_g": _round_list(long_a, 2), "lat_g": _round_list(lat_a, 2)},
            "b": {"long_g": _round_list(long_b, 2), "lat_g": _round_list(lat_b, 2)},
            "note": "derived from ~4 Hz telemetry, smoothed over ~40 m; indicative only",
        }
    return payload


def _generate(a: LapRef, b: LapRef, detail: str) -> Dict[str, Any]:
    # One scope so a same-session duel parses CarData/Position once.
    with shared_session_stores():
        side_a = _load_side(a)
        side_b = _load_side(b)
        circuit_info = get_circuit_info_for_session(side_a["store"])
        return build_payload(side_a, side_b, circuit_info, detail=detail)


def build_refs(
    y: int, identifier: Union[int, str], e: str, driver1: str, driver2: str,
    lap1: Optional[int] = None, lap2: Optional[int] = None,
    segment1: Optional[str] = None, segment2: Optional[str] = None,
    year2: Optional[int] = None, identifier2: Optional[Union[int, str]] = None,
    session2: Optional[str] = None,
) -> Tuple[LapRef, LapRef]:
    """Both sides of a duel from request arguments; side B inherits A's session by default."""
    session_a = str(e).strip().upper()
    a = LapRef(y, identifier, session_a, driver1.strip().upper(), lap1, _normalise_segment(segment1))
    b = LapRef(year2 or y, identifier2 if identifier2 not in (None, "") else identifier,
               str(session2).strip().upper() if session2 else session_a,
               driver2.strip().upper(), lap2, _normalise_segment(segment2))
    if a == b:
        raise ValueError("Both sides name the same lap")
    return a, b


class LapDuelData:
    """Callable: ``LapDuelData()(year, gp, session, driver1, driver2, ...) -> dict``."""

    def __call__(
        self, y: int, identifier: Union[int, str], e: str, driver1: str, driver2: str,
        lap1: Optional[int] = None, lap2: Optional[int] = None,
        segment1: Optional[str] = None, segment2: Optional[str] = None,
        year2: Optional[int] = None, identifier2: Optional[Union[int, str]] = None,
        session2: Optional[str] = None, detail: str = "standard",
    ) -> Dict[str, Any]:
        detail = (detail or "standard").strip().lower()
        if detail not in VALID_DETAIL:
            raise ValueError(f"detail must be one of {', '.join(VALID_DETAIL)} (got {detail!r})")
        a, b = build_refs(y, identifier, e, driver1, driver2, lap1, lap2, segment1, segment2,
                          year2, identifier2, session2)

        return cached_or_generate(
            year=y, identifier=identifier, session=a.session,
            data_type=_variant_key(a, b, detail), generator=lambda: _generate(a, b, detail),
            version="v2",
        )


class LapDuelPlot:
    """Callable: same arguments as :class:`LapDuelData` plus ``fmt`` and ``hero`` -> PNG path.

    ``fmt`` is one of ``canvas.FORMAT_NAMES`` (``None`` renders landscape; Lap
    Duel is new, so it has no legacy look to preserve). ``hero`` adds driver
    headshots to the plates.
    """

    def __call__(
        self, y: int, identifier: Union[int, str], e: str, driver1: str, driver2: str,
        lap1: Optional[int] = None, lap2: Optional[int] = None,
        segment1: Optional[str] = None, segment2: Optional[str] = None,
        year2: Optional[int] = None, identifier2: Optional[Union[int, str]] = None,
        session2: Optional[str] = None, detail: str = "standard",
        fmt: Optional[str] = None, hero: bool = False,
    ) -> str:
        from src.services.analysis.v2 import _lap_duel_render
        from src.services.plotting.canvas import get_format

        if fmt is not None:
            get_format(fmt)  # validate before the expensive part
        payload = LapDuelData()(
            y, identifier, e, driver1, driver2, lap1=lap1, lap2=lap2,
            segment1=segment1, segment2=segment2, year2=year2, identifier2=identifier2,
            session2=session2, detail=detail,
        )
        a, b = build_refs(y, identifier, e, driver1, driver2, lap1, lap2, segment1, segment2,
                          year2, identifier2, session2)
        variant = _variant_key(a, b, detail)[len(_data_type(a.driver, b.driver)):].strip("_") or "best"
        if hero:
            variant += "_hero"
        return _lap_duel_render.render(payload, fmt, hero=hero, variant=variant)
