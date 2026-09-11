"""V2 championship standings endpoints (drivers + constructors)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool

from src.api.schemas.common import ANALYSIS_ERROR_RESPONSES
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
    get_constructors_standings_live,
    get_drivers_standings,
    get_drivers_standings_live,
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


def _resolve_live_drivers() -> dict:
    year = datetime.utcnow().year
    try:
        payload = get_drivers_standings_live(year)
        if payload.get("standings"):
            return payload
    except SessionNotFoundError:
        pass
    return get_drivers_standings_live(year - 1)


def _resolve_live_constructors() -> dict:
    year = datetime.utcnow().year
    try:
        payload = get_constructors_standings_live(year)
        if payload.get("standings"):
            return payload
    except SessionNotFoundError:
        pass
    return get_constructors_standings_live(year - 1)


@router.get(
    "/seasons/{year}/drivers-standings",
    tags=["API v2", "Seasonal Data"],
    response_model=DriversStandingsResponse,
    summary="Drivers' standings for a season",
    operation_id="v2_season_drivers_standings",
    description=(
        "Full-season drivers' championship standings. This is the **canonical** form of this "
        "data; `/api/v2/standings/drivers` returns the same shape for the current season only."
    ),
    responses={**ANALYSIS_ERROR_RESPONSES},
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
    summary="Constructors' standings for a season",
    operation_id="v2_season_constructors_standings",
    description=(
        "Full-season constructors' championship standings. This is the **canonical** form of "
        "this data; `/api/v2/standings/constructors` returns the same shape for the current "
        "season only."
    ),
    responses={**ANALYSIS_ERROR_RESPONSES},
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
    summary="Drivers' standings after a round",
    operation_id="v2_round_drivers_standings",
    description="Drivers' championship standings as they stood immediately after `round_nr`.",
    responses={**ANALYSIS_ERROR_RESPONSES},
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
    summary="Constructors' standings after a round",
    operation_id="v2_round_constructors_standings",
    description="Constructors' championship standings as they stood immediately after `round_nr`.",
    responses={**ANALYSIS_ERROR_RESPONSES},
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
    summary="Current drivers' standings",
    operation_id="v2_current_drivers_standings",
    description=(
        "Drivers' championship standings for the current season, falling back to the prior "
        "season if the current one has no results yet. Short form of the canonical "
        "`/api/v2/seasons/{year}/drivers-standings`; defaults to the current season."
    ),
    responses={**ANALYSIS_ERROR_RESPONSES},
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
    summary="Current constructors' standings",
    operation_id="v2_current_constructors_standings",
    description=(
        "Constructors' championship standings for the current season, falling back to the "
        "prior season if the current one has no results yet. Short form of the canonical "
        "`/api/v2/seasons/{year}/constructors-standings`; defaults to the current season."
    ),
    responses={**ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def current_constructors_standings(
    request: Request,
    api_key: str = Depends(verify_api_key),
):
    return await run_in_threadpool(_resolve_current_year_constructors)


@router.get(
    "/standings/drivers/live",
    tags=["API v2", "Seasonal Data"],
    response_model=DriversStandingsResponse,
    summary="Live drivers' standings",
    operation_id="v2_live_drivers_standings",
    description=(
        "Drivers' championship standings for the current season, fetched straight from the "
        "upstream feed on every call — **no cache layer**. Use this while a race weekend is "
        "in progress; use `/api/v2/standings/drivers` when a cached snapshot is acceptable."
    ),
    responses={**ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def live_drivers_standings(
    request: Request,
    api_key: str = Depends(verify_api_key),
):
    return await run_in_threadpool(_resolve_live_drivers)


@router.get(
    "/standings/constructors/live",
    tags=["API v2", "Seasonal Data"],
    response_model=ConstructorsStandingsResponse,
    summary="Live constructors' standings",
    operation_id="v2_live_constructors_standings",
    description=(
        "Constructors' championship standings for the current season, fetched straight from "
        "the upstream feed on every call — **no cache layer**. Use this while a race weekend "
        "is in progress; use `/api/v2/standings/constructors` when a cached snapshot is "
        "acceptable."
    ),
    responses={**ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def live_constructors_standings(
    request: Request,
    api_key: str = Depends(verify_api_key),
):
    return await run_in_threadpool(_resolve_live_constructors)
