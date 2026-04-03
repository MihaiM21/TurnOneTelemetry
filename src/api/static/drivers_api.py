from fastapi import APIRouter
from src.utils.logger import get_logger
from src.utils.rate_limiting import apply_tiered_limit
from fastapi import Request, Query, HTTPException
from src.data_loader.const_loader import get_season_drivers, get_driver_details_by_name



logger = get_logger(__name__)

router = APIRouter(prefix='/api/static')


@router.get('/drivers', tags=["Static"])
@apply_tiered_limit("data")
async def get_drivers_from_year(
    request: Request,
    year: int = Query(2026, ge=2025, le=2026, description="Season year (2025-2026)")
):
    """Get drivers for a specific season year"""
    try:
        drivers = get_season_drivers(year)
        return {"drivers": drivers}
    except ValueError as e:
        logger.error(f"Error fetching drivers for year {year}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    
@router.get('/drivers/{driver_id}', tags=["Static"])
@apply_tiered_limit("data")
async def get_driver_details(
    request: Request,
    year: int = Query(2026, ge=2025, le=2026, description="Season year (2025-2026)"),
    driver_name: str = Query(..., description="Driver Name")
):
    """Get details for a specific driver by Name"""
    try:
        team_details = get_driver_details_by_name(year, driver_name)
        if not team_details:
            raise ValueError(f"Driver {driver_name} not found")
        return {"team": team_details}
    except ValueError as e:
        logger.error(f"Error fetching driver details for {driver_name}: {e}")
        raise HTTPException(status_code=404, detail=str(e))