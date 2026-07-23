from fastapi import APIRouter
from src.core.logging import get_logger
from src.core.security.rate_limiting import apply_tiered_limit
from fastapi import Request, Query, HTTPException, Path

from src.ingestion.circuits_loader import get_yearly_circuits, get_circuit_data_by_id, get_circuit_data_by_name, get_circuit_data_file

logger = get_logger(__name__)

router = APIRouter(prefix='/api/static')

@router.get('/circuits', tags=["Static"])
@apply_tiered_limit("data")
async def get_circuits_from_year(
    request: Request,
    year: int = Query(2026, ge=2024, le=2026, description="Season year (2024-2026)")
):
    """Get circuits for a specific season year"""
    try:
        circuits = get_yearly_circuits(year)
        return {"circuits": circuits}
    except Exception as e:
        logger.error(f"Error fetching circuits for year {year}: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get('/circuits/{circuit_id}/info', tags=["Static"])
@apply_tiered_limit("data")
async def get_circuit_info_by_id(
    request: Request,
    circuit_id: int = Path(..., description="Circuit ID"),
    year: int = Query(2026, ge=2024, le=2026, description="Season year (2024-2026)")
):
    """Get basic info for a specific circuit by ID"""
    try:
        circuit_details = get_circuit_data_by_id(circuit_id, year)
        if not circuit_details:
            raise ValueError(f"Circuit with ID {circuit_id} not found")
        return {"circuit": circuit_details}
    except ValueError as e:
        logger.error(f"Error fetching circuit info for ID {circuit_id}: {e}")
        raise HTTPException(status_code=404, detail=str(e))

@router.get('/circuits/{circuit_id}/data', tags=["Static"])
@apply_tiered_limit("data")
async def get_circuit_data_by_id_endpoint(
    request: Request,
    circuit_id: int = Path(..., description="Circuit ID"),
    year: int = Query(2026, ge=2024, le=2026, description="Season year (2024-2026)")
):
    """Get the full circuit layout (corners, rotation, marshal lights/sectors, track
    outline) for a specific circuit by ID, in our own schema"""
    try:
        circuit_data = get_circuit_data_file(circuit_id, year)
        return {"data": circuit_data}
    except ValueError as e:
        logger.error(f"Error fetching circuit data for ID {circuit_id}: {e}")
        raise HTTPException(status_code=404, detail=str(e))

