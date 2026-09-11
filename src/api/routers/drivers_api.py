from fastapi import APIRouter
from src.api.schemas.common import COMMON_ERROR_RESPONSES, ErrorEnvelope
from src.api.schemas.seasonal import DriverDetailResponse, SeasonDriversResponse
from src.core.logging import get_logger
from src.core.security.rate_limiting import apply_tiered_limit
from fastapi import Request, Query, HTTPException, Path
from src.ingestion.reference import get_season_drivers, get_driver_details_by_name

logger = get_logger(__name__)

router = APIRouter(prefix='/api/static')

#: This router has no `verify_api_key` dependency (see module-level endpoint docs), so
#: 401/403 can never occur here; only the rate-limit and server-error cases apply.
_UNAUTH_COMMON_RESPONSES = {429: COMMON_ERROR_RESPONSES[429], 500: COMMON_ERROR_RESPONSES[500]}


@router.get(
    '/drivers',
    tags=["Static"],
    summary="List drivers for a season",
    operation_id="static_season_drivers",
    responses={
        **_UNAUTH_COMMON_RESPONSES,
        200: {"model": SeasonDriversResponse, "description": "All drivers for the season."},
        400: {"model": ErrorEnvelope, "description": "Season year not covered by the static reference data."},
    },
)
@apply_tiered_limit("data")
async def get_drivers_from_year(
    request: Request,
    year: int = Query(2026, ge=2025, le=2026, description="Season year (2025-2026)")
):
    """Get drivers for a specific season year.

    Unauthenticated: this endpoint has no API-key requirement and serves static reference
    data curated in the repo (``src/domain/data/drivers.json``), not live session data.
    """
    try:
        drivers = get_season_drivers(year)
        return {"drivers": drivers}
    except ValueError as e:
        logger.error(f"Error fetching drivers for year {year}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    '/drivers/{driver_name}',
    tags=["Static"],
    summary="Get driver details by name",
    operation_id="static_driver_details",
    responses={
        **_UNAUTH_COMMON_RESPONSES,
        200: {"model": DriverDetailResponse, "description": "The matched driver."},
        404: {"model": ErrorEnvelope, "description": "No driver matched `driver_name` for that season."},
    },
)
@apply_tiered_limit("data")
async def get_driver_details(
    request: Request,
    year: int = Query(2026, ge=2025, le=2026, description="Season year (2025-2026)"),
    driver_name: str = Path(..., description="Driver Name")
):
    """Get details for a specific driver by Name.

    Unauthenticated: this endpoint has no API-key requirement and serves static reference
    data curated in the repo (``src/domain/data/drivers.json``), not live session data.
    """
    try:
        team_details = get_driver_details_by_name(year, driver_name)
        if not team_details:
            raise ValueError(f"Driver {driver_name} not found")
        return {"team": team_details}
    except ValueError as e:
        logger.error(f"Error fetching driver details for {driver_name}: {e}")
        raise HTTPException(status_code=404, detail=str(e))
