from fastapi import APIRouter
from src.utils.logger import get_logger
from src.utils.rate_limiting import apply_tiered_limit
from fastapi import Request, Query, HTTPException
from src.data_loader.const_loader import get_season_drivers, get_season_teams



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
    

# TODO: Add endpoint for single team details (e.g., by team ID or name)