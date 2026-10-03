"""
Sector gap to pole (V2, livetiming-backed, qualifying / sprint qualifying only).

For P2..P10 of the classification: the gap to pole split into S1 / S2 / S3, all
measured on each driver's own fastest lap so the three segments add up to the
lap-time gap.

Where the sector splits come from
---------------------------------
``store.best_sectors()`` is a running per-driver minimum per sector, i.e. three
laps' worth of bests, not a lap, so it cannot be used here. Instead
:func:`collect_sector_candidates` replays ``TimingData`` and records every
*complete* (S1, S2, S3) triple a driver's timing line ever showed. The fastest
lap is then identified by value: the triple whose sum equals the lap time
(:func:`match_lap_sectors`, +-3 ms for the feed's rounding). Sector updates and
``LastLapTime`` arrive in a different order lap to lap, so matching on the sum
is robust where "snapshot the sectors when the lap time arrives" is not. Nothing
is estimated from telemetry. A driver whose lap cannot be matched is left out
and listed in ``unmatched``; if the pole lap cannot be matched there is no
reference and the session is reported as unavailable.

A segment is negative when the driver beat pole in that sector.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

from src.core.exceptions import DataNotAvailableError
from src.core.logging import get_logger
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._field_laps import driver_colour
from src.services.analysis.v2._helpers import (
    build_session_store,
    get_qualifying_classification,
    parse_f1_time,
    shared_session_stores,
)
from src.services.analysis.v2._race_helpers import _normalize_sectors

logger = get_logger(__name__)

DATA_TYPE = "sector_gap"
SESSIONS = ("Q", "SQ")
TOP_POSITIONS = 10
MATCH_TOL_S = 0.003

Triple = Tuple[float, float, float]

METHOD = {
    "sectors": "S1/S2/S3 as timed on each driver's fastest lap (livetiming sector splits)",
    "gap": "driver sector time minus pole's; negative = faster than pole in that sector",
    "match_tolerance_s": MATCH_TOL_S,
}


def _unavailable(year: int, identifier: Any, session: str, reason: str) -> DataNotAvailableError:
    return DataNotAvailableError(year=year, gp=identifier, session=session, source="livetiming", reason=reason)


# ----------------------------------------------------------------------
# Sector splits of a specific lap
# ----------------------------------------------------------------------
def collect_sector_candidates(entries: List[Dict[str, Any]]) -> Dict[str, List[Triple]]:
    """``{car: [(s1, s2, s3), ...]}`` -- every complete sector triple each timing line showed.

    An empty ``Value`` (the reset at the start of a lap) clears that sector, so
    a triple is only recorded when all three belong to the same lap.
    """
    current: Dict[str, List[Optional[float]]] = {}
    out: Dict[str, List[Triple]] = {}
    for entry in entries:
        lines = entry.get("Lines")
        if not isinstance(lines, dict):
            continue
        for num, line in lines.items():
            if not isinstance(line, dict) or line.get("Sectors") is None:
                continue
            cur = current.setdefault(str(num), [None, None, None])
            changed = False
            for idx, sector in _normalize_sectors(line["Sectors"]).items():
                if idx not in ("0", "1", "2") or "Value" not in sector:
                    continue
                value = parse_f1_time(sector["Value"]) if sector["Value"] else 0.0
                cur[int(idx)] = value if value > 0 else None
                changed = True
            if changed and all(v is not None for v in cur):
                triple = (float(cur[0]), float(cur[1]), float(cur[2]))
                seen = out.setdefault(str(num), [])
                if not seen or seen[-1] != triple:
                    seen.append(triple)
    return out


def match_lap_sectors(candidates: List[Triple], lap_time: float, tol: float = MATCH_TOL_S) -> Optional[Triple]:
    """The candidate triple whose sum is closest to ``lap_time``, within ``tol`` seconds."""
    best: Optional[Triple] = None
    best_err = tol + 1e-9
    for triple in candidates:
        err = abs(sum(triple) - lap_time)
        if err < best_err:
            best, best_err = triple, err
    return best


# ----------------------------------------------------------------------
# Payload
# ----------------------------------------------------------------------
def build_payload(rows: List[Dict[str, Any]], year: int, event_name: str, session: str,
                  unmatched: Optional[List[str]] = None) -> Dict[str, Any]:
    """Assemble the payload from classified rows, pole first (test seam).

    Each row: ``{position, driver, color, lap_time_s, sectors: [s1, s2, s3]}``.
    """
    if len(rows) < 2:
        raise ValueError("need pole and at least one other driver")
    pole = rows[0]
    drivers = []
    for r in rows[1:]:
        sector_gaps = [round(a - b, 3) for a, b in zip(r["sectors"], pole["sectors"])]
        drivers.append({
            "position": int(r["position"]),
            "driver": r["driver"],
            "color": r["color"],
            "lap_time_s": round(float(r["lap_time_s"]), 3),
            "gap_s": round(float(r["lap_time_s"]) - float(pole["lap_time_s"]), 3),
            "sectors": [round(float(s), 3) for s in r["sectors"]],
            "sector_gaps_s": sector_gaps,
        })

    leaders = []
    for k in range(3):
        best = min(rows, key=lambda r: r["sectors"][k])
        leaders.append({"sector": k + 1, "driver": best["driver"], "time_s": round(float(best["sectors"][k]), 3)})
    faster = [{"driver": d["driver"], "sector": k + 1, "gap_s": g}
              for d in drivers for k, g in enumerate(d["sector_gaps_s"]) if g < 0]
    highlights = {
        "sector_leaders": leaders,
        "faster_than_pole": faster,
        "largest_sector_gap": max(
            ({"driver": d["driver"], "sector": k + 1, "gap_s": g}
             for d in drivers for k, g in enumerate(d["sector_gaps_s"])),
            key=lambda x: x["gap_s"]),
    }
    return {
        "pole": {"driver": pole["driver"], "color": pole["color"], "lap_time_s": round(float(pole["lap_time_s"]), 3),
                 "sectors": [round(float(s), 3) for s in pole["sectors"]]},
        "drivers": drivers,
        "unmatched": list(unmatched or []),
        "highlights": highlights,
        "method": METHOD,
        "session_info": {"year": year, "event_name": event_name, "session_name": session},
    }


# ----------------------------------------------------------------------
# Fetch + public callables
# ----------------------------------------------------------------------
def _generate(year: int, identifier: Any, session: str) -> Dict[str, Any]:
    with shared_session_stores():
        store = build_session_store(year, identifier, session)
        if store is None:
            raise _unavailable(year, identifier, session, "Session could not be resolved")
        classification = get_qualifying_classification(store.base_url, store.client, store=store)
        if classification.empty:
            raise _unavailable(year, identifier, session, "No classification available")
        top = classification.head(TOP_POSITIONS)
        candidates = collect_sector_candidates(store.timing_data())
        drivers = store.driver_list()

        rows: List[Dict[str, Any]] = []
        unmatched: List[str] = []
        for i, r in enumerate(top.itertuples(index=False)):
            num = str(r.DriverNum)
            info = drivers.get(num, {})
            code = info.get("tla") or num
            triple = match_lap_sectors(candidates.get(num, []), float(r.LapTime))
            if triple is None:
                logger.warning("No sector triple matches %s's %.3f s lap", code, r.LapTime)
                unmatched.append(code)
                continue
            position = int(r.Position) if r.Position == r.Position else i + 1
            rows.append({"position": position, "driver": code, "color": driver_colour(info, year),
                         "lap_time_s": float(r.LapTime), "sectors": list(triple)})
        pole_code = drivers.get(str(top.iloc[0]["DriverNum"]), {}).get("tla") or str(top.iloc[0]["DriverNum"])
        if not rows or rows[0]["driver"] != pole_code:
            raise _unavailable(year, identifier, session, "Pole lap sector times not found")
        if len(rows) < 2:
            raise _unavailable(year, identifier, session, "Sector times missing for the rest of the field")
        return build_payload(rows, year, store.event_name, session, unmatched)


class SectorGapData:
    """Callable: ``SectorGapData()(year, identifier, session) -> dict`` (Q / SQ only)."""

    def __call__(self, y: int, identifier: Union[int, str], e: str) -> Dict[str, Any]:
        if e not in SESSIONS:
            raise _unavailable(y, identifier, e, f"{DATA_TYPE} exists for Q and SQ only")
        return cached_or_generate(
            year=y, identifier=identifier, session=e, data_type=DATA_TYPE,
            generator=lambda: _generate(y, identifier, e), version="v2",
        )


class SectorGapPlot:
    """Callable: same arguments as :class:`SectorGapData` plus ``fmt`` -> PNG path."""

    def __call__(self, y: int, identifier: Union[int, str], e: str, fmt: Optional[str] = None) -> str:
        from src.services.analysis.v2 import _field_render
        from src.services.plotting.canvas import get_format

        if fmt is not None:
            get_format(fmt)  # validate before the expensive part
        payload = SectorGapData()(y, identifier, e)
        return _field_render.render_sector_gap(payload, fmt)
