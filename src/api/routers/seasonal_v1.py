from fastapi import APIRouter, Request, HTTPException, Depends, Response
from fastapi.concurrency import run_in_threadpool

from src.core.logging import get_logger
from src.core.security.api_keys import verify_api_key
from src.core.security.rate_limiting import apply_tiered_limit
from src.api.schemas.common import COMMON_ERROR_RESPONSES, ErrorEnvelope
from src.api.schemas.legacy_v1 import (
    LegacySeasonsListResponse,
    LegacySeasonSummaryResponse,
    LegacySeasonDriversResponse,
    LegacySeasonTeamsResponse,
    LegacyDriverInfoResponse,
    LegacyTeamInfoResponse,
)

logger = get_logger(__name__)


def _sunset_headers(response: Response) -> None:
    """RFC 8594 retirement notice, applied to every ``/api/v1`` response.

    Mirrors ``src/api/routers/analysis_v1.py::_sunset_headers`` — duplicated rather than
    shared because the two routers are independently owned and this file must not import
    from that one.
    """
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Sun, 01 Sep 2027 00:00:00 GMT"


router = APIRouter(prefix="/api/v1", dependencies=[Depends(_sunset_headers)])

# ============================================================================
# SEASONAL DATA ENDPOINTS
# ============================================================================


@router.get(
    '/seasons',
    tags=["API v1"],
    deprecated=True,
    summary="List seasons with stored data",
    operation_id="v1_seasons",
    description=(
        "Get list of all available seasons (years with a MongoDB season document). "
        "**Deprecated, no V2 equivalent** — V2 has no endpoint enumerating covered years; "
        "the closest modern reference data (`GET /api/static/drivers`, "
        "`GET /api/static/teams`) currently covers seasons 2025-2026 only."
    ),
    responses={200: {"model": LegacySeasonsListResponse}, **COMMON_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def get_available_seasons(
    request: Request,
    api_key: str = Depends(verify_api_key)
):
    try:
        logger.info("Fetching available seasons")
        from src.repositories.seasonal_data import get_available_seasons
        seasons = await run_in_threadpool(get_available_seasons)
        return {"seasons": seasons}
    except Exception as e:
        logger.error(f"Error fetching available seasons: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch available seasons")


@router.get(
    '/season/{year}',
    tags=["API v1"],
    deprecated=True,
    summary="Complete season summary",
    operation_id="v1_season_summary",
    description=(
        "Get complete season data including drivers, teams, and races. **Deprecated, no "
        "single V2 equivalent** — the closest coverage is composing "
        "`GET /api/v2/seasons/{year}/events` (schedule) with "
        "`GET /api/static/drivers?year=` and `GET /api/static/teams?year=` (roster); "
        "no V2 endpoint returns this aggregate in one call."
    ),
    responses={
        **COMMON_ERROR_RESPONSES,
        200: {"model": LegacySeasonSummaryResponse},
        404: {"model": ErrorEnvelope, "description": "No stored season document for this year."},
    },
)
@apply_tiered_limit("standard")
async def get_season_summary(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key)
):
    try:
        logger.info(f"Fetching complete season data for {year}")
        from src.repositories.seasonal_data import get_season_summary
        season_data = await run_in_threadpool(lambda: get_season_summary(year))

        if not season_data:
            raise HTTPException(status_code=404, detail=f"Season {year} not found")

        return season_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching season summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch season summary")


@router.get(
    '/season/{year}/drivers',
    tags=["API v1"],
    deprecated=True,
    summary="List drivers for a season",
    operation_id="v1_season_drivers",
    description=(
        "Get list of drivers for a specific season (from the MongoDB season document). "
        "**Deprecated** — closest replacement is `GET /api/static/drivers?year=` (curated "
        "reference data, covers seasons 2025-2026; different source, not a drop-in "
        "replacement for older years)."
    ),
    responses={
        **COMMON_ERROR_RESPONSES,
        200: {"model": LegacySeasonDriversResponse},
        404: {"model": ErrorEnvelope, "description": "No drivers found for this season."},
    },
)
@apply_tiered_limit("standard")
async def get_season_drivers(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key)
):
    try:
        logger.info(f"Fetching drivers for season {year}")
        from src.repositories.seasonal_data import get_drivers_for_season
        drivers = await run_in_threadpool(lambda: get_drivers_for_season(year))

        if not drivers:
            raise HTTPException(status_code=404, detail=f"No drivers found for season {year}")

        return {"year": year, "drivers": drivers}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching season drivers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch season drivers")


@router.get(
    '/season/{year}/teams',
    tags=["API v1"],
    deprecated=True,
    summary="List teams for a season",
    operation_id="v1_season_teams",
    description=(
        "Get list of teams for a specific season (from the MongoDB season document). "
        "**Deprecated** — closest replacement is `GET /api/static/teams?year=` (curated "
        "reference data, covers seasons 2025-2026; different source, not a drop-in "
        "replacement for older years)."
    ),
    responses={
        **COMMON_ERROR_RESPONSES,
        200: {"model": LegacySeasonTeamsResponse},
        404: {"model": ErrorEnvelope, "description": "No teams found for this season."},
    },
)
@apply_tiered_limit("standard")
async def get_season_teams(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key)
):
    try:
        logger.info(f"Fetching teams for season {year}")
        from src.repositories.seasonal_data import get_teams_for_season
        teams = await run_in_threadpool(lambda: get_teams_for_season(year))

        if not teams:
            raise HTTPException(status_code=404, detail=f"No teams found for season {year}")

        return {"year": year, "teams": teams}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching season teams: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch season teams")


@router.get(
    '/season/{year}/driver/{driver_code}',
    tags=["API v1"],
    deprecated=True,
    summary="Get one driver for a season",
    operation_id="v1_season_driver",
    description=(
        "Get specific driver information for a season (from the MongoDB season "
        "document). **Deprecated** — closest replacement is "
        "`GET /api/static/drivers/{driver_name}?year=` (curated reference data, covers "
        "seasons 2025-2026; different source, not a drop-in replacement for older years)."
    ),
    responses={
        **COMMON_ERROR_RESPONSES,
        200: {"model": LegacyDriverInfoResponse},
        404: {"model": ErrorEnvelope, "description": "No such driver for this season."},
    },
)
@apply_tiered_limit("standard")
async def get_driver_info(
    request: Request,
    year: int,
    driver_code: str,
    api_key: str = Depends(verify_api_key)
):
    try:
        logger.info(f"Fetching driver {driver_code} for season {year}")
        from src.repositories.seasonal_data import get_driver_by_code
        driver = await run_in_threadpool(lambda: get_driver_by_code(year, driver_code.upper()))

        if not driver:
            raise HTTPException(status_code=404, detail=f"Driver {driver_code} not found for season {year}")

        return driver
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching driver info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch driver info")


@router.get(
    '/season/{year}/team/{team_name}',
    tags=["API v1"],
    deprecated=True,
    summary="Get one team for a season",
    operation_id="v1_season_team",
    description=(
        "Get specific team information for a season (from the MongoDB season document). "
        "**Deprecated** — closest replacement is `GET /api/static/teams/{team_name}?year=` "
        "(curated reference data, covers seasons 2025-2026; different source, not a "
        "drop-in replacement for older years)."
    ),
    responses={
        **COMMON_ERROR_RESPONSES,
        200: {"model": LegacyTeamInfoResponse},
        404: {"model": ErrorEnvelope, "description": "No such team for this season."},
    },
)
@apply_tiered_limit("standard")
async def get_team_info(
    request: Request,
    year: int,
    team_name: str,
    api_key: str = Depends(verify_api_key)
):
    try:
        logger.info(f"Fetching team {team_name} for season {year}")
        from src.repositories.seasonal_data import get_team_by_name
        team = await run_in_threadpool(lambda: get_team_by_name(year, team_name))

        if not team:
            raise HTTPException(status_code=404, detail=f"Team {team_name} not found for season {year}")

        return team
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching team info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch team info")
