from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import FileResponse
from fastapi.concurrency import run_in_threadpool
import requests

from src.api.schemas.common import ANALYSIS_ERROR_RESPONSES, COMMON_ERROR_RESPONSES, ErrorEnvelope
from src.api.schemas.seasonal import (
    EventSessionsResponse,
    SeasonDriverRadarResponse,
    SeasonEventItem,
    SeasonEventsResponse,
    SeasonFormResponse,
    TeammateBattleResponse,
)
from src.core.exceptions import SessionNotFoundError, T1APIError
from src.core.logging import get_logger
from src.core.security.api_keys import verify_api_key
from src.core.security.rate_limiting import apply_tiered_limit
from src.services.plotting.canvas import get_format

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v2")

from src.ingestion.calendar import public_event
from src.ingestion.reference import get_season_calendar
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
    The season calendar: pre-season testing, every race by its **official round**
    (the number V1/FastF1 use), and events that were cancelled or replaced
    (`status: "cancelled"`, no round). Built from the livetiming index, the
    curated schedule and the championship schedule; see `GET /seasons/{year}/events/{event}`
    to resolve one event.
    """
    try:
        logger.info(f"Fetching calendar for season {year}")
        events = await run_in_threadpool(get_season_calendar, year)
        return {"year": year, "events": [public_event(e) for e in events]}
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching season events: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch available events for the season.")


@router.get(
    '/seasons/{year}/events/{event_name}',
    tags=["API v2", "Seasonal Data"],
    summary="Resolve one season event",
    operation_id="v2_season_event",
    responses={
        **COMMON_ERROR_RESPONSES,
        200: {"model": SeasonEventItem, "description": "The calendar entry `event_name` resolves to."},
        404: {
            "model": ErrorEnvelope,
            "description": "Season not covered, or `event_name` matches no event (or several: see `suggestions`).",
        },
    },
)
@apply_tiered_limit("standard")
async def get_season_event(
    request: Request,
    year: int,
    event_name: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Resolve a round number, livetiming meeting key, or name (`Japan`, `USA`,
    `Sepang`, `Madrid` ...) to its calendar entry. Cancelled events resolve too and
    say so in `status` / `reason` / `replaced_by`.
    """
    from src.ingestion.event_resolver import EventResolver

    def _resolve():
        resolver = EventResolver(year)
        info = resolver.resolve(event_name, allow_cancelled=True)
        for event in resolver._events:
            if event.get("grandPrix") == info.name and event.get("key") == info.key and \
                    event.get("status", "scheduled") == info.status:
                return event
        raise SessionNotFoundError(year=year, gp=event_name, reason="Event vanished from the calendar.")

    event = await run_in_threadpool(_resolve)
    return public_event(event)


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
            "description": "Season data not found on official F1 servers, no event matched `event_name`, "
                           "or the event was cancelled / is not yet listed by livetiming.",
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
    `event_name` is resolved like every other `gp` input: official round, livetiming
    meeting key, or a name / alias (`Italian Grand Prix`, `Monza`, `Italy`).
    """
    from src.ingestion.event_resolver import resolve_event

    try:
        logger.info(f"Fetching sessions for {event_name} ({year}) via F1StaticClient")
        client = F1StaticClient()

        info = await run_in_threadpool(resolve_event, year, event_name)
        if info.key is None:
            raise HTTPException(
                status_code=404,
                detail=f"{info.name} ({year}) is scheduled but livetiming has not listed it yet.",
            )

        # We need the full season index to find the event and its sessions
        season_index = await run_in_threadpool(client.fetch_season_index, year)

        # The meeting is selected by key -- never by array position or name substring.
        event_data = next(
            (m for m in season_index.get('Meetings', []) if m.get('Key') == info.key), None
        )

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

    except (HTTPException, T1APIError):
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
