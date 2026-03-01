from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.concurrency import run_in_threadpool

from src.utils.logger import get_logger
from src.utils.auth import verify_api_key
from src.utils.rate_limiting import apply_tiered_limit

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1")

# ============================================================================
# SEASONAL DATA ENDPOINTS
# ============================================================================

@router.get('/seasons', tags=["API v1", "Seasonal Data"])
@apply_tiered_limit("standard")
async def get_available_seasons(
    request: Request,
    api_key: str = Depends(verify_api_key)
):
    """Get list of all available seasons"""
    try:
        logger.info("Fetching available seasons")
        from src.utils.database.seasonal_data import get_available_seasons
        seasons = await run_in_threadpool(get_available_seasons)
        return {"seasons": seasons}
    except Exception as e:
        logger.error(f"Error fetching available seasons: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch available seasons")

@router.get('/season/{year}', tags=["API v1", "Seasonal Data"])
@apply_tiered_limit("standard")
async def get_season_summary(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key)
):
    """Get complete season data including drivers, teams, and races"""
    try:
        logger.info(f"Fetching complete season data for {year}")
        from src.utils.database.seasonal_data import get_season_summary
        season_data = await run_in_threadpool(lambda: get_season_summary(year))
        
        if not season_data:
            raise HTTPException(status_code=404, detail=f"Season {year} not found")
        
        return season_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching season summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch season summary")

@router.get('/season/{year}/drivers', tags=["API v1", "Seasonal Data"])
@apply_tiered_limit("standard")
async def get_season_drivers(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key)
):
    """Get list of drivers for a specific season"""
    try:
        logger.info(f"Fetching drivers for season {year}")
        from src.utils.database.seasonal_data import get_drivers_for_season
        drivers = await run_in_threadpool(lambda: get_drivers_for_season(year))
        
        if not drivers:
            raise HTTPException(status_code=404, detail=f"No drivers found for season {year}")
        
        return {"year": year, "drivers": drivers}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching season drivers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch season drivers")

@router.get('/season/{year}/teams', tags=["API v1", "Seasonal Data"])
@apply_tiered_limit("standard")
async def get_season_teams(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key)
):
    """Get list of teams for a specific season"""
    try:
        logger.info(f"Fetching teams for season {year}")
        from src.utils.database.seasonal_data import get_teams_for_season
        teams = await run_in_threadpool(lambda: get_teams_for_season(year))
        
        if not teams:
            raise HTTPException(status_code=404, detail=f"No teams found for season {year}")
        
        return {"year": year, "teams": teams}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching season teams: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch season teams")

@router.get('/season/{year}/driver/{driver_code}', tags=["API v1", "Seasonal Data"])
@apply_tiered_limit("standard")
async def get_driver_info(
    request: Request,
    year: int,
    driver_code: str,
    api_key: str = Depends(verify_api_key)
):
    """Get specific driver information for a season"""
    try:
        logger.info(f"Fetching driver {driver_code} for season {year}")
        from src.utils.database.seasonal_data import get_driver_by_code
        driver = await run_in_threadpool(lambda: get_driver_by_code(year, driver_code.upper()))
        
        if not driver:
            raise HTTPException(status_code=404, detail=f"Driver {driver_code} not found for season {year}")
        
        return driver
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching driver info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch driver info")

@router.get('/season/{year}/team/{team_name}', tags=["API v1", "Seasonal Data"])
@apply_tiered_limit("standard")
async def get_team_info(
    request: Request,
    year: int,
    team_name: str,
    api_key: str = Depends(verify_api_key)
):
    """Get specific team information for a season"""
    try:
        logger.info(f"Fetching team {team_name} for season {year}")
        from src.utils.database.seasonal_data import get_team_by_name
        team = await run_in_threadpool(lambda: get_team_by_name(year, team_name))
        
        if not team:
            raise HTTPException(status_code=404, detail=f"Team {team_name} not found for season {year}")
        
        return team
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching team info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch team info")
