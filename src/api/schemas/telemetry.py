"""Documentation-only response schemas for the V2 telemetry router.

These models exist purely so Swagger can show a real shape for every
``/api/v2/telemetry/...`` JSON endpoint. Per ``src/api/routers/telemetry_v2.py``
they are attached via ``responses={200: {"model": ...}}`` — **never** via
``response_model=`` — so they cannot filter or otherwise alter the live
payload. Each model mirrors the actual payload produced by
``src/services/analysis/v2/lap_frames.py`` (``LapsData`` / ``TrackMapData``).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TelemetrySessionInfo(BaseModel):
    """Session identity echoed back inside both telemetry payloads."""

    year: int
    event_name: str
    session_name: str
    round: int


class LapsDataRange(BaseModel):
    """Echo of the request's lap/format/rate knobs, plus the resulting size."""

    lap_from: int
    lap_to: int
    hz: int
    format: str = Field(
        description="'frames' or 'columnar' — selects which field is populated on each driver block below."
    )
    precision: Optional[int] = None
    frame_count: int
    driver_laps: int


class LapsDataDriverInfo(BaseModel):
    tla: str
    car_number: str
    name: str
    team: str
    color: str


class LapsDataLap(BaseModel):
    number: int
    lap_time_s: float
    start_s: float
    end_s: float
    pit_in: bool
    pit_out: bool
    position: Optional[int] = None
    compound: Optional[str] = None
    tyre_life: Optional[int] = None
    frame_from: Optional[int] = None
    frame_to: Optional[int] = None


class LapsDataGap(BaseModel):
    """One window inside the resampled range where position data was unavailable."""

    from_: float = Field(alias="from")
    to: float
    reason: str

    model_config = {"populate_by_name": True}


class LapFrame(BaseModel):
    """One sample on the uniform output grid built by ``build_frames``."""

    t: float
    lap: Optional[int] = None
    session_time: float
    d: float
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    speed: Optional[float] = None
    throttle: Optional[float] = None
    brake: Optional[float] = None
    gear: Optional[int] = None
    rpm: Optional[float] = None
    drs: Optional[int] = None
    status: str


class LapsDataDriverBlock(BaseModel):
    """One driver's laps, frames/columns, and gap report.

    ``frames`` and ``columns`` are mutually exclusive — the ``format`` query
    parameter on ``GET /api/v2/telemetry/laps-data`` selects which one is
    populated: ``frames`` (array of :class:`LapFrame`) or ``columnar`` (one
    array per field, same values as ``frames`` transposed).
    """

    driver: LapsDataDriverInfo
    laps: List[LapsDataLap]
    gaps: List[LapsDataGap]
    frames: Optional[List[LapFrame]] = None
    columns: Optional[Dict[str, List[Any]]] = None


class TelemetryTrack(BaseModel):
    """Session-level circuit geometry, driver-free."""

    rotation: int
    corners: List[Any]
    outline: List[List[float]]


class LapsDataResponse(BaseModel):
    """Uniform-grid, multi-driver, multi-lap telemetry — from ``lap_frames.py:LapsData``.

    Any session. ``track`` is ``null`` unless ``include_track=true`` was
    requested.
    """

    session_info: TelemetrySessionInfo
    range: LapsDataRange
    drivers: List[LapsDataDriverBlock]
    track: Optional[TelemetryTrack] = None


class TrackMapResponse(BaseModel):
    """Session-level circuit geometry — from ``lap_frames.py:TrackMapData``.

    Deliberately distinct from the existing per-driver ``/api/v2/track-map-data``,
    which colours one driver's fastest lap by speed or gear; this endpoint
    carries no driver and no lap timing, only the circuit outline itself.
    """

    session_info: TelemetrySessionInfo
    track: TelemetryTrack


__all__ = [
    "TelemetrySessionInfo",
    "LapsDataRange",
    "LapsDataDriverInfo",
    "LapsDataLap",
    "LapsDataGap",
    "LapFrame",
    "LapsDataDriverBlock",
    "TelemetryTrack",
    "LapsDataResponse",
    "TrackMapResponse",
]
