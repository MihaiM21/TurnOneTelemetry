from fastapi import APIRouter
from src.api.schemas.common import COMMON_ERROR_RESPONSES, ErrorEnvelope
from src.api.schemas.seasonal import CircuitDataResponse, CircuitInfoResponse, YearlyCircuitsResponse
from src.core.logging import get_logger
from src.core.security.rate_limiting import apply_tiered_limit
from fastapi import Request, Query, HTTPException, Path

from src.ingestion.circuits_loader import get_yearly_circuits, get_circuit_data_by_id, get_circuit_data_file

logger = get_logger(__name__)

router = APIRouter(prefix='/api/static')

#: This router has no `verify_api_key` dependency (see module-level endpoint docs), so
#: 401/403 can never occur here; only the rate-limit and server-error cases apply.
_UNAUTH_COMMON_RESPONSES = {429: COMMON_ERROR_RESPONSES[429], 500: COMMON_ERROR_RESPONSES[500]}


@router.get(
    '/circuits',
    tags=["Static"],
    summary="List circuits for a season",
    operation_id="static_season_circuits",
    responses={
        **_UNAUTH_COMMON_RESPONSES,
        200: {"model": YearlyCircuitsResponse, "description": "All circuit summaries for the season."},
    },
)
@apply_tiered_limit("data")
async def get_circuits_from_year(
    request: Request,
    year: int = Query(2026, ge=2024, le=2026, description="Season year (2024-2026)")
):
    """Get circuits for a specific season year.

    Unauthenticated: this endpoint has no API-key requirement and serves static reference
    data curated in the repo (``src/domain/data/circuits/``), not live session data.
    """
    try:
        circuits = get_yearly_circuits(year)
        return {"circuits": circuits}
    except Exception as e:
        logger.error(f"Error fetching circuits for year {year}: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get(
    '/circuits/{circuit_id}/info',
    tags=["Static"],
    summary="Get circuit summary by ID",
    operation_id="static_circuit_info",
    responses={
        **_UNAUTH_COMMON_RESPONSES,
        200: {"model": CircuitInfoResponse, "description": "The matched circuit summary."},
        404: {"model": ErrorEnvelope, "description": "No circuit matched `circuit_id` for that season."},
    },
)
@apply_tiered_limit("data")
async def get_circuit_info_by_id(
    request: Request,
    circuit_id: int = Path(..., description="Circuit ID"),
    year: int = Query(2026, ge=2024, le=2026, description="Season year (2024-2026)")
):
    """Get basic info for a specific circuit by ID.

    Unauthenticated: this endpoint has no API-key requirement and serves static reference
    data curated in the repo (``src/domain/data/circuits/``), not live session data.
    """
    try:
        circuit_details = get_circuit_data_by_id(circuit_id, year)
        if not circuit_details:
            raise ValueError(f"Circuit with ID {circuit_id} not found")
        return {"circuit": circuit_details}
    except ValueError as e:
        logger.error(f"Error fetching circuit info for ID {circuit_id}: {e}")
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    '/circuits/{circuit_id}/data',
    tags=["Static"],
    summary="Get full circuit layout by ID",
    operation_id="static_circuit_data",
    responses={
        **_UNAUTH_COMMON_RESPONSES,
        200: {"model": CircuitDataResponse, "description": "Full circuit layout in our own schema."},
        404: {"model": ErrorEnvelope, "description": "No circuit layout file found for `circuit_id`/`year`."},
    },
)
@apply_tiered_limit("data")
async def get_circuit_data_by_id_endpoint(
    request: Request,
    circuit_id: int = Path(..., description="Circuit ID"),
    year: int = Query(2026, ge=2024, le=2026, description="Season year (2024-2026)")
):
    """Get the full circuit layout (corners, rotation, marshal lights/sectors, track
    outline) for a specific circuit by ID, in our own schema.

    Unauthenticated: this endpoint has no API-key requirement and serves static reference
    data curated in the repo (``src/domain/data/circuits/``), not live session data.
    """
    try:
        circuit_data = get_circuit_data_file(circuit_id, year)
        return {"data": circuit_data}
    except ValueError as e:
        logger.error(f"Error fetching circuit data for ID {circuit_id}: {e}")
        raise HTTPException(status_code=404, detail=str(e))
