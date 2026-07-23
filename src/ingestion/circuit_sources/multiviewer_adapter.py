"""
Adapts raw api.multiviewer.app circuit payloads into our own CircuitLayout /
CircuitSummary schema (src/domain/models/circuits.py).

This is the only module allowed to know multiviewer's raw field names
(trackPosition, circuitKey, countryIocCode, ...). Everything downstream
(storage, loader, API) only ever sees our own schema.
"""

from typing import Any, Dict, List, Optional

from src.domain.models.circuits import CircuitLayout, CircuitSummary, Point, TrackMarker, TrackOutline


def _adapt_markers(raw_markers: Optional[List[Dict[str, Any]]]) -> List[TrackMarker]:
    raw_markers = raw_markers or []
    return [
        TrackMarker(
            number=marker["number"],
            angle=marker["angle"],
            length=marker["length"],
            position=Point(x=marker["trackPosition"]["x"], y=marker["trackPosition"]["y"]),
        )
        for marker in raw_markers
    ]


def adapt_circuit_layout(
    raw_circuit_json: Dict[str, Any],
    circuit_id: str,
    year: int,
    source_fetched_at: Optional[str] = None,
) -> CircuitLayout:
    """Map a raw multiviewer /circuits/{id}/{year} response into a CircuitLayout."""
    raw_circuit_json = raw_circuit_json or {}
    return CircuitLayout(
        circuit_id=str(circuit_id),
        year=year,
        name=raw_circuit_json.get("circuitName") or "",
        country=raw_circuit_json.get("countryName") or "",
        country_code=raw_circuit_json.get("countryIocCode") or "",
        location=raw_circuit_json.get("location") or "",
        rotation=raw_circuit_json.get("rotation") or 0,
        round=raw_circuit_json.get("round"),
        race_date=raw_circuit_json.get("raceDate"),
        meeting_name=raw_circuit_json.get("meetingName"),
        track_outline=TrackOutline(
            x=raw_circuit_json.get("x") or [],
            y=raw_circuit_json.get("y") or [],
        ),
        corners=_adapt_markers(raw_circuit_json.get("corners")),
        marshal_lights=_adapt_markers(raw_circuit_json.get("marshalLights")),
        marshal_sectors=_adapt_markers(raw_circuit_json.get("marshalSectors")),
        source="multiviewer",
        source_fetched_at=source_fetched_at,
    )


def adapt_circuit_summary(
    raw_circuit_entry: Dict[str, Any],
    circuit_id: str,
    years_available: List[int],
) -> CircuitSummary:
    """Map a raw multiviewer circuits-list entry into a CircuitSummary."""
    raw_circuit_entry = raw_circuit_entry or {}
    return CircuitSummary(
        circuit_id=str(circuit_id),
        name=raw_circuit_entry.get("name") or "",
        country=raw_circuit_entry.get("country") or "",
        country_code=raw_circuit_entry.get("iocCountryCode") or "",
        circuit_key=raw_circuit_entry.get("circuitKey"),
        years_available=years_available,
    )
