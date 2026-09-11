from fastapi import APIRouter
from src.api.schemas.common import COMMON_ERROR_RESPONSES, ErrorEnvelope
from src.api.schemas.seasonal import SeasonTeamsResponse, TeamDetailResponse
from src.core.logging import get_logger
from src.core.security.rate_limiting import apply_tiered_limit
from fastapi import Request, Query, HTTPException, Path
from src.ingestion.reference import get_season_teams, get_team_details_by_name

logger = get_logger(__name__)

router = APIRouter(prefix='/api/static')

#: This router has no `verify_api_key` dependency (see module-level endpoint docs), so
#: 401/403 can never occur here; only the rate-limit and server-error cases apply.
_UNAUTH_COMMON_RESPONSES = {429: COMMON_ERROR_RESPONSES[429], 500: COMMON_ERROR_RESPONSES[500]}


@router.get(
    '/teams',
    tags=["Static"],
    summary="List teams for a season",
    operation_id="static_season_teams",
    responses={
        **_UNAUTH_COMMON_RESPONSES,
        200: {"model": SeasonTeamsResponse, "description": "All constructors for the season."},
        400: {"model": ErrorEnvelope, "description": "Season year not covered by the static reference data."},
    },
)
@apply_tiered_limit("data")
async def get_teams_from_year(
    request: Request,
    year: int = Query(2026, ge=2025, le=2026, description="Season year (2025-2026)")
):
    """Get teams for a specific season year.

    Unauthenticated: this endpoint has no API-key requirement and serves static reference
    data curated in the repo (``src/domain/data/teams.json``), not live session data.
    """
    try:
        teams = get_season_teams(year)
        return {"teams": teams}
    except ValueError as e:
        logger.error(f"Error fetching teams for year {year}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    '/teams/{team_name}',
    tags=["Static"],
    summary="Get team details by name",
    operation_id="static_team_details",
    responses={
        **_UNAUTH_COMMON_RESPONSES,
        200: {"model": TeamDetailResponse, "description": "The matched constructor."},
        404: {"model": ErrorEnvelope, "description": "No team matched `team_name` for that season."},
    },
)
@apply_tiered_limit("data")
async def get_team_details(
    request: Request,
    year: int = Query(2026, ge=2025, le=2026, description="Season year (2025-2026)"),
    team_name: str = Path(..., description="Team Name")
):
    """Get details for a specific team by Name.

    Unauthenticated: this endpoint has no API-key requirement and serves static reference
    data curated in the repo (``src/domain/data/teams.json``), not live session data.
    """
    try:
        team_details = get_team_details_by_name(year, team_name)
        if not team_details:
            raise ValueError(f"Team {team_name} not found")
        return {"team": team_details}
    except ValueError as e:
        logger.error(f"Error fetching team details for {team_name}: {e}")
        raise HTTPException(status_code=404, detail=str(e))
