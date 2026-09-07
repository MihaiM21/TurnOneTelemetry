"""Animation-ready telemetry endpoints (V2, livetiming-backed).

Thin HTTP wrapper around ``src/services/analysis/v2/lap_frames.py``. See that
module's docstring for why a *resampled* multi-driver, multi-lap frame stream
is a different shape from ``/api/v2/lap-all-data`` (one driver, one lap, raw
samples) and worth its own endpoint.
"""
from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.concurrency import run_in_threadpool
from typing import Optional, Union

from src.api.deps import guard_session_params
from src.core.logging import get_logger
from src.core.security.api_keys import verify_api_key
from src.core.security.rate_limiting import apply_tiered_limit
from src.api.schemas.common import ANALYSIS_ERROR_RESPONSES
from src.api.schemas.telemetry import LapsDataResponse, TrackMapResponse
from src.services.analysis.v2.lap_frames import LapsData, TrackMapData
from src.core.exceptions import T1APIError

logger = get_logger(__name__)


def _track(event_name, *args):
    try:
        from src.core.observability.analytics import SessionTracker
        SessionTracker().track_session(event_name, *args)
    except Exception:
        pass


router = APIRouter(
    prefix="/api/v2/telemetry",
    dependencies=[Depends(guard_session_params)],
)


@router.get(
    '/laps-data',
    tags=["API v2", "Telemetry"],
    summary="Resampled telemetry frames over a lap range",
    operation_id="v2_telemetry_laps_data",
    responses={200: {"model": LapsDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("data")
async def telemetry_laps_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    drivers: str = Query(..., description="Comma-separated driver TLAs, e.g. VER,HAM"),
    lap_from: int = Query(..., ge=1),
    lap_to: Optional[int] = Query(None, ge=1, description="Defaults to lap_from"),
    hz: int = Query(10, ge=1, le=20, description="Output frame rate"),
    format: str = Query('frames', description="frames | columnar"),
    precision: int = Query(2, ge=0, le=6),
    include_track: bool = Query(False),
    api_key: str = Depends(verify_api_key)
):
    """A single uniform time grid across a lap range and a set of drivers, ready
    for animation playback: one row per tick with position, throttle, brake and
    the rest resampled onto it, instead of each driver's raw samples landing on
    different instants.

    ``hz`` picks the output frame rate (1-20), ``format`` picks ``frames``
    (array of samples) or ``columnar`` (one array per field), and
    ``precision`` rounds every float in the response. The response's
    ``range.driver_laps`` and ``range.frame_count`` reflect the actual cost of
    the request against the service's driver-lap and frame budget — a request
    over budget is rejected with 400 before any stream is touched, and the
    error message suggests an ``hz`` that would fit. Any session.
    """
    lap_to = lap_to if lap_to is not None else lap_from
    if lap_to < lap_from:
        raise HTTPException(status_code=400, detail=f"lap_to ({lap_to}) must be >= lap_from ({lap_from}).")
    if format not in ("frames", "columnar"):
        raise HTTPException(status_code=400, detail="format must be one of 'frames', 'columnar'.")
    driver_list = [d.strip().upper() for d in drivers.split(',') if d.strip()]
    if not driver_list:
        raise HTTPException(status_code=400, detail="At least one driver is required.")

    logger.info(
        f"Fetching telemetry laps-data (V2): Y{year} GP{gp} {session} drivers={driver_list} "
        f"laps={lap_from}-{lap_to} hz={hz} format={format}"
    )
    try:
        result = await run_in_threadpool(
            LapsData(), year, gp, session, driver_list, lap_from, lap_to,
            hz, format, precision, include_track,
        )
        _track('telemetry-laps-data', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get(
    '/track-map',
    tags=["API v2", "Telemetry"],
    summary="Circuit geometry for a session",
    operation_id="v2_telemetry_track_map",
    responses={200: {"model": TrackMapResponse}, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("data")
async def telemetry_track_map_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """Session-level circuit geometry — rotation, corners, and track outline —
    with no driver attached. Deliberately distinct from the existing per-driver
    ``/api/v2/track-map-data``, which colours one driver's fastest lap by speed
    or gear; this endpoint returns only the circuit itself, for drawing the
    track once and animating cars over it (e.g. with ``/laps-data``). Any
    session.
    """
    logger.info(f"Fetching telemetry track-map (V2): Y{year} GP{gp} {session}")
    try:
        result = await run_in_threadpool(TrackMapData(), year, gp, session)
        _track('telemetry-track-map', year, gp, session)
        return result
    except T1APIError:
        raise
