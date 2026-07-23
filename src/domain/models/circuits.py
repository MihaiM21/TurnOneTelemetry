"""Our own circuit-data schema, decoupled from any external provider's raw shape."""

from typing import List, Optional
from pydantic import BaseModel


class Point(BaseModel):
    x: float
    y: float


class TrackMarker(BaseModel):
    """A numbered point on the circuit outline (corner, marshal light, marshal sector)"""
    number: int
    angle: float
    length: float
    position: Point


class TrackOutline(BaseModel):
    x: List[float]
    y: List[float]


class CircuitLayout(BaseModel):
    """Full track-map data for one circuit in one season."""
    circuit_id: str
    year: int
    name: str
    country: str
    country_code: str
    location: str
    rotation: float
    round: Optional[int] = None
    race_date: Optional[str] = None
    meeting_name: Optional[str] = None
    track_outline: TrackOutline
    corners: List[TrackMarker]
    marshal_lights: List[TrackMarker]
    marshal_sectors: List[TrackMarker]
    schema_version: int = 1
    source: str = "multiviewer"
    source_fetched_at: Optional[str] = None


class CircuitSummary(BaseModel):
    """Lightweight circuit listing entry, no track-map data."""
    circuit_id: str
    name: str
    country: str
    country_code: str
    circuit_key: Optional[int] = None
    years_available: List[int] = []
