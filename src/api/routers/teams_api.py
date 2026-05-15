from fastapi import APIRouter
from src.core.logging import get_logger
from src.core.security.rate_limiting import apply_tiered_limit
from fastapi import Request, Query, HTTPException, Path
from src.ingestion.reference import get_season_teams, get_team_details_by_name



logger = get_logger(__name__)

router = APIRouter(prefix='/api/static')


@router.get('/teams', tags=["Static"])
@apply_tiered_limit("data")
async def get_teams_from_year(
    request: Request,
    year: int = Query(2026, ge=2025, le=2026, description="Season year (2025-2026)")
):
    """Get teams for a specific season year"""
    try:
        teams = get_season_teams(year)
        return {"teams": teams}
    except ValueError as e:
        logger.error(f"Error fetching teams for year {year}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    
@router.get('/teams/{team_name}', tags=["Static"])
@apply_tiered_limit("data")
async def get_team_details(
    request: Request,
    year: int = Query(2026, ge=2025, le=2026, description="Season year (2025-2026)"),
    team_name: str = Path(..., description="Team Name")
):
    """Get details for a specific team by Name"""
    try:
        team_details = get_team_details_by_name(year, team_name)
        if not team_details:
            raise ValueError(f"Team {team_name} not found")
        return {"team": team_details}
    except ValueError as e:
        logger.error(f"Error fetching team details for {team_name}: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    
