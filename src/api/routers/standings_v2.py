"""V2 championship standings endpoints (drivers + constructors)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool

from src.api.schemas.standings import (
    ConstructorsStandingsResponse,
    DriversStandingsResponse,
)
from src.core.exceptions import SessionNotFoundError
from src.core.logging import get_logger
from src.core.security.api_keys import verify_api_key
from src.core.security.rate_limiting import apply_tiered_limit
from src.services.standings.v2.standings import (
    get_constructors_standings,
    get_drivers_standings,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v2")


def _resolve_current_year_drivers() -> dict:
    year = datetime.utcnow().year
    try:
        payload = get_drivers_standings(year)
        if payload.get("standings"):
            return payload
    except SessionNotFoundError:
        pass
    return get_drivers_standings(year - 1)


def _resolve_current_year_constructors() -> dict:
    year = datetime.utcnow().year
    try:
        payload = get_constructors_standings(year)
        if payload.get("standings"):
            return payload
    except SessionNotFoundError:
        pass
    return get_constructors_standings(year - 1)


@router.get(
    "/seasons/{year}/drivers-standings",
    tags=["API v2", "Seasonal Data"],
    response_model=DriversStandingsResponse,
)
@apply_tiered_limit("standard")
async def drivers_standings_for_season(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key),
):
    return await run_in_threadpool(get_drivers_standings, year, None)


@router.get(
    "/seasons/{year}/constructors-standings",
    tags=["API v2", "Seasonal Data"],
    response_model=ConstructorsStandingsResponse,
)
@apply_tiered_limit("standard")
async def constructors_standings_for_season(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key),
):
    return await run_in_threadpool(get_constructors_standings, year, None)


@router.get(
    "/seasons/{year}/round/{round_nr}/drivers-standings",
    tags=["API v2", "Seasonal Data"],
    response_model=DriversStandingsResponse,
)
@apply_tiered_limit("standard")
async def drivers_standings_after_round(
    request: Request,
    year: int,
    round_nr: int,
    api_key: str = Depends(verify_api_key),
):
    return await run_in_threadpool(get_drivers_standings, year, round_nr)


@router.get(
    "/seasons/{year}/round/{round_nr}/constructors-standings",
    tags=["API v2", "Seasonal Data"],
    response_model=ConstructorsStandingsResponse,
)
@apply_tiered_limit("standard")
async def constructors_standings_after_round(
    request: Request,
    year: int,
    round_nr: int,
    api_key: str = Depends(verify_api_key),
):
    return await run_in_threadpool(get_constructors_standings, year, round_nr)


@router.get(
    "/standings/drivers",
    tags=["API v2", "Seasonal Data"],
    response_model=DriversStandingsResponse,
)
@apply_tiered_limit("standard")
async def current_drivers_standings(
    request: Request,
    api_key: str = Depends(verify_api_key),
):
    return await run_in_threadpool(_resolve_current_year_drivers)


@router.get(
    "/standings/constructors",
    tags=["API v2", "Seasonal Data"],
    response_model=ConstructorsStandingsResponse,
)
@apply_tiered_limit("standard")
async def current_constructors_standings(
    request: Request,
    api_key: str = Depends(verify_api_key),
):
    return await run_in_threadpool(_resolve_current_year_constructors)
