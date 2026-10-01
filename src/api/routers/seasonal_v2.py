from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import FileResponse
from fastapi.concurrency import run_in_threadpool
import requests

from src.api.schemas.common import ANALYSIS_ERROR_RESPONSES, COMMON_ERROR_RESPONSES, ErrorEnvelope
from src.api.schemas.seasonal import (
    EventSessionsResponse,
    SeasonDriverRadarResponse,
    SeasonEventsResponse,
    SeasonFormResponse,
    TeammateBattleResponse,
)
from src.core.exceptions import T1APIError
from src.core.logging import get_logger
from src.core.security.api_keys import verify_api_key
from src.core.security.rate_limiting import apply_tiered_limit
from src.services.plotting.canvas import get_format

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v2")

from src.ingestion.static_client import F1StaticClient
from src.services.analysis.v2.teammate_battle import TeammateBattlePlot, TeammateBattleData
from src.services.analysis.v2.season_form import SeasonFormPlot, SeasonFormData
from src.services.analysis.v2.driver_radar import (
    SeasonRadarPlot, SeasonRadarData, CareerRadarPlot, CareerRadarData,
)

#: PNG plot endpoints have no JSON body.
_PNG_RESPONSE = {"content": {"image/png": {}}, "description": "PNG plot."}

#: A plot's 404 can be either a schedule miss (SessionNotFoundEnvelope, from
#: ANALYSIS_ERROR_RESPONSES) or a generic "Plot not found" (ErrorEnvelope) when the file
#: itself is missing after a successful generation. Both are documented via description.
_PLOT_NOT_FOUND_NOTE = " A missing rendered file (rare) 404s as a plain error envelope instead."


def _track(event_name, *args):
    try:
        from src.core.observability.analytics import SessionTracker
        SessionTracker().track_session(event_name, *args)
    except Exception:
        pass


# ============================================================================
# SEASONAL DATA ENDPOINTS (V2)
# ============================================================================

@router.get(
    '/seasons/{year}/events',
    tags=["API v2", "Seasonal Data"],
    summary="List season events (meetings)",
    operation_id="v2_season_events",
    responses={
        **COMMON_ERROR_RESPONSES,
        200: {"model": SeasonEventsResponse, "description": "All meetings for the season."},
        404: {"model": ErrorEnvelope, "description": "Season data not found on official F1 servers."},
        502: {"model": ErrorEnvelope, "description": "Error communicating with F1 servers."},
    },
)
@apply_tiered_limit("standard")
async def get_season_events(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key)
):
    """
    Get a list of all available events (meetings) for a specific season.
    This fetches data directly from the formula1.com static API.
    """
    try:
        logger.info(f"Fetching events for season {year} via F1StaticClient")
        client = F1StaticClient()

        # Run synchronous network request in a threadpool to prevent blocking the async event loop
        season_index = await run_in_threadpool(client.fetch_season_index, year)

        meetings = season_index.get('Meetings', [])

        # Format the response nicely
        events = []
        for meeting in meetings:
            events.append({
                "name": meeting.get('Name'),
                "official_name": meeting.get('OfficialName'),
                "location": meeting.get('Location'),
                "country": meeting.get('Country', {}).get('Name'),
                "key": meeting.get('Key'),
                "code": meeting.get('Code')
            })

        return {"year": year, "events": events}

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.error(f"Season index not found for year {year}")
            raise HTTPException(status_code=404, detail=f"Season data for {year} not found on official F1 servers.")
        logger.error(f"HTTP Error fetching events for {year}: {e}")
        raise HTTPException(status_code=502, detail="Error communicating with F1 servers.")
    except Exception as e:
        logger.error(f"Error fetching season events: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch available events for the season.")


@router.get(
    '/seasons/{year}/events/{event_name}/sessions',
    tags=["API v2", "Seasonal Data"],
    summary="List sessions for a season event",
    operation_id="v2_season_event_sessions",
    responses={
        **COMMON_ERROR_RESPONSES,
        200: {"model": EventSessionsResponse, "description": "All sessions for the matched meeting."},
        404: {
            "model": ErrorEnvelope,
            "description": "Season data not found on official F1 servers, or no event matched `event_name`.",
        },
        502: {"model": ErrorEnvelope, "description": "Error communicating with F1 servers."},
    },
)
@apply_tiered_limit("standard")
async def get_event_sessions(
    request: Request,
    year: int,
    event_name: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Get a list of all available sessions for a specific event in a season.
    event_name can be a broad match (e.g., "Italian Grand Prix").
    """
    try:
        logger.info(f"Fetching sessions for {event_name} ({year}) via F1StaticClient")
        client = F1StaticClient()

        # We need the full season index to find the event and its sessions
        season_index = await run_in_threadpool(client.fetch_season_index, year)

        # Find the meeting
        meetings = season_index.get('Meetings', [])
        event_data = None
        for meeting in meetings:
            if event_name.lower() in meeting.get('Name', '').lower() or event_name.lower() == str(meeting.get('Key')):
                event_data = meeting
                break

        if not event_data:
            raise HTTPException(
                status_code=404,
                detail=f"Event matching '{event_name}' not found in the {year} season.",
            )

        sessions_data = event_data.get('Sessions', [])

        sessions = []
        for session in sessions_data:
            sessions.append({
                "name": session.get('Name'),
                "type": session.get('Type'),
                "number": session.get('Number'),
                "start_date": session.get('StartDate'),
                "end_date": session.get('EndDate'),
                "path": session.get('Path'),
                "key": session.get('Key')
            })

        return {
            "year": year,
            "event_name": event_data.get('Name'),
            "event_key": event_data.get('Key'),
            "sessions": sessions
        }

    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.error(f"Season index not found for year {year}")
            raise HTTPException(status_code=404, detail=f"Season data for {year} not found on official F1 servers.")
        logger.error(f"HTTP Error fetching sessions for {event_name}: {e}")
        raise HTTPException(status_code=502, detail="Error communicating with F1 servers.")
    except Exception as e:
        logger.error(f"Error fetching event sessions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch available sessions for the event.")


# ============================================================================
# TEAMMATE BATTLE TRACKER (V2, seasonal)
# ============================================================================

@router.get(
    '/seasons/{year}/teammate-battle-plot',
    tags=["API v2", "Seasonal Data"],
    summary="Teammate battle scorecard plot (PNG)",
    operation_id="v2_teammate_battle_plot",
    responses={
        **ANALYSIS_ERROR_RESPONSES,
        200: _PNG_RESPONSE,
        404: {
            **ANALYSIS_ERROR_RESPONSES[404],
            "description": ANALYSIS_ERROR_RESPONSES[404]["description"] + _PLOT_NOT_FOUND_NOTE,
        },
    },
)
@apply_tiered_limit("standard")
async def teammate_battle_plot_v2(
    request: Request,
    year: int,
    format: Optional[str] = Query(
        None, description="landscape|square|portrait|story — social-media canvas; omit for the classic image"
    ),
    api_key: str = Depends(verify_api_key)
):
    """Season-long teammate H2H scorecard: quali/race wins and average quali gap per team.

    For the current (in-progress) season this is regenerated on request rather than served
    from a durable stored row, since the live season's payloads are cached in Redis only.
    """
    logger.info(f"Generating teammate battle plot (V2): {year}")
    try:
        # Omitted format -> exactly the pre-existing call; an unknown name is a ValueError -> 400.
        fmt_kwargs = {}
        if format is not None:
            get_format(format)
            fmt_kwargs = {"fmt": format}
        output_path = await run_in_threadpool(TeammateBattlePlot(), year, **fmt_kwargs)
        _track('teammate-battle', year)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/seasons/{year}/teammate-battle-data',
    tags=["API v2", "Seasonal Data"],
    summary="Teammate battle scorecard data",
    operation_id="v2_teammate_battle_data",
    responses={
        **ANALYSIS_ERROR_RESPONSES,
        200: {"model": TeammateBattleResponse, "description": "Per-team quali/race H2H scorecard."},
    },
)
@apply_tiered_limit("data")
async def teammate_battle_data_v2(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key)
):
    """JSON teammate H2H payload: per-team quali/race head-to-head + average quali gap.

    For the current (in-progress) season this is regenerated on request rather than served
    from a durable stored row, since the live season's payloads are cached in Redis only.
    """
    logger.info(f"Fetching teammate battle data (V2): {year}")
    try:
        result = await run_in_threadpool(TeammateBattleData(), year)
        _track('teammate-battle', year)
        return result
    except T1APIError:
        raise


# ============================================================================
# SEASON FORM GUIDE (V2, seasonal)
# ============================================================================

@router.get(
    '/seasons/{year}/form-guide-plot',
    tags=["API v2", "Seasonal Data"],
    summary="Season form guide plot (PNG)",
    operation_id="v2_season_form_guide_plot",
    responses={
        **ANALYSIS_ERROR_RESPONSES,
        200: _PNG_RESPONSE,
        404: {
            **ANALYSIS_ERROR_RESPONSES[404],
            "description": ANALYSIS_ERROR_RESPONSES[404]["description"] + _PLOT_NOT_FOUND_NOTE,
        },
    },
)
@apply_tiered_limit("standard")
async def form_guide_plot_v2(
    request: Request,
    year: int,
    window: int = Query(3, ge=2, le=10, description="Rolling average window, in races"),
    drivers: Optional[str] = Query(None, description="Comma-separated TLAs; default top 10 by mean finish"),
    api_key: str = Depends(verify_api_key)
):
    """Rolling-average race/quali form guide across the season, per driver.

    For the current (in-progress) season this is regenerated on request rather than served
    from a durable stored row, since the live season's payloads are cached in Redis only.
    """
    logger.info(f"Generating season form guide plot (V2): {year} window={window} drivers={drivers}")
    try:
        driver_list = [d.strip().upper() for d in drivers.split(",") if d.strip()] if drivers else None
        output_path = await run_in_threadpool(SeasonFormPlot(), year, window, driver_list)
        _track('form-guide', year)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/seasons/{year}/form-guide-data',
    tags=["API v2", "Seasonal Data"],
    summary="Season form guide data",
    operation_id="v2_season_form_guide_data",
    responses={
        **ANALYSIS_ERROR_RESPONSES,
        200: {"model": SeasonFormResponse, "description": "Rolling-average race/quali position series."},
    },
)
@apply_tiered_limit("data")
async def form_guide_data_v2(
    request: Request,
    year: int,
    window: int = Query(3, ge=2, le=10, description="Rolling average window, in races"),
    drivers: Optional[str] = Query(None, description="Comma-separated TLAs; default top 10 by mean finish"),
    api_key: str = Depends(verify_api_key)
):
    """JSON rolling-average race/quali position series per driver.

    For the current (in-progress) season this is regenerated on request rather than served
    from a durable stored row, since the live season's payloads are cached in Redis only.
    """
    logger.info(f"Fetching season form guide data (V2): {year} window={window} drivers={drivers}")
    try:
        driver_list = [d.strip().upper() for d in drivers.split(",") if d.strip()] if drivers else None
        result = await run_in_threadpool(SeasonFormData(), year, window, driver_list)
        _track('form-guide', year)
        return result
    except T1APIError:
        raise


@router.get(
    '/seasons/{year}/driver-radar-plot',
    tags=["API v2", "Seasonal Data"],
    summary="Season driver radar plot (PNG)",
    operation_id="v2_season_driver_radar_plot",
    responses={
        **ANALYSIS_ERROR_RESPONSES,
        200: _PNG_RESPONSE,
        404: {
            **ANALYSIS_ERROR_RESPONSES[404],
            "description": ANALYSIS_ERROR_RESPONSES[404]["description"] + _PLOT_NOT_FOUND_NOTE,
        },
    },
)
@apply_tiered_limit("standard")
async def season_radar_plot_v2(
    request: Request,
    year: int,
    drivers: Optional[str] = Query(None, description="Comma-separated TLAs (max 3); default best 3 by race pace"),
    portrait: bool = Query(False, description="Portrait 4:5 crop for social media"),
    api_key: str = Depends(verify_api_key)
):
    """Season driver performance radar (race pace, qualifying, consistency, racecraft, reliability, peak).

    For the current (in-progress) season this is regenerated on request rather than served
    from a durable stored row, since the live season's payloads are cached in Redis only.
    """
    logger.info(f"Generating season radar plot (V2): {year} drivers={drivers}")
    try:
        output_path = await run_in_threadpool(SeasonRadarPlot(), year, drivers, portrait)
        _track('season-radar', year)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/seasons/{year}/driver-radar-data',
    tags=["API v2", "Seasonal Data"],
    summary="Season driver radar data",
    operation_id="v2_season_driver_radar_data",
    responses={
        **ANALYSIS_ERROR_RESPONSES,
        200: {"model": SeasonDriverRadarResponse, "description": "Season driver radar (scope='season')."},
    },
)
@apply_tiered_limit("data")
async def season_radar_data_v2(
    request: Request,
    year: int,
    drivers: Optional[str] = Query(None, description="Comma-separated TLAs (max 3); default best 3 by race pace"),
    api_key: str = Depends(verify_api_key)
):
    """JSON season driver radar: 0-100 axis values + raw metrics per driver.

    For the current (in-progress) season this is regenerated on request rather than served
    from a durable stored row, since the live season's payloads are cached in Redis only.
    """
    logger.info(f"Fetching season radar data (V2): {year} drivers={drivers}")
    try:
        result = await run_in_threadpool(SeasonRadarData(), year, drivers)
        _track('season-radar', year)
        return result
    except T1APIError:
        raise


@router.get(
    '/career/driver-radar-plot',
    tags=["API v2", "Seasonal Data"],
    summary="Career driver radar plot (PNG)",
    operation_id="v2_career_driver_radar_plot",
    responses={
        **ANALYSIS_ERROR_RESPONSES,
        200: _PNG_RESPONSE,
        404: {
            **ANALYSIS_ERROR_RESPONSES[404],
            "description": ANALYSIS_ERROR_RESPONSES[404]["description"] + _PLOT_NOT_FOUND_NOTE,
        },
    },
)
@apply_tiered_limit("standard")
async def career_radar_plot_v2(
    request: Request,
    years: str = Query(..., description="Year span 'YYYY-YYYY' or list 'YYYY,YYYY'"),
    drivers: Optional[str] = Query(None, description="Comma-separated TLAs (max 3); default best 3 by race pace"),
    portrait: bool = Query(False, description="Portrait 4:5 crop for social media"),
    api_key: str = Depends(verify_api_key)
):
    """Career driver performance radar aggregated over multiple seasons of results.

    If the span includes the current (in-progress) season, that season's contribution is
    regenerated on request rather than read from a durable row, since the live season's
    payloads are cached in Redis only.
    """
    logger.info(f"Generating career radar plot (V2): years={years} drivers={drivers}")
    try:
        output_path = await run_in_threadpool(CareerRadarPlot(), years, drivers, portrait)
        _track('career-radar', years)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/career/driver-radar-data',
    tags=["API v2", "Seasonal Data"],
    summary="Career driver radar data",
    operation_id="v2_career_driver_radar_data",
    responses={
        **ANALYSIS_ERROR_RESPONSES,
        200: {"model": SeasonDriverRadarResponse, "description": "Career driver radar (scope='career')."},
    },
)
@apply_tiered_limit("data")
async def career_radar_data_v2(
    request: Request,
    years: str = Query(..., description="Year span 'YYYY-YYYY' or list 'YYYY,YYYY'"),
    drivers: Optional[str] = Query(None, description="Comma-separated TLAs (max 3); default best 3 by race pace"),
    api_key: str = Depends(verify_api_key)
):
    """JSON career driver radar: 0-100 axis values + raw metrics per driver across seasons.

    If the span includes the current (in-progress) season, that season's contribution is
    regenerated on request rather than read from a durable row, since the live season's
    payloads are cached in Redis only.
    """
    logger.info(f"Fetching career radar data (V2): years={years} drivers={drivers}")
    try:
        result = await run_in_threadpool(CareerRadarData(), years, drivers)
        _track('career-radar', years)
        return result
    except T1APIError:
        raise
