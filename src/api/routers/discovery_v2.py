"""Feature discovery for the V2 analysis API.

Clients previously had to hardcode endpoint URLs, with no way to ask what
analysis exists at all, or whether a given session actually has data yet.
``src/services/analysis/v2/registry.py`` already holds the canonical feature
catalog and ``src/workers/plot_inventory.py`` already knows what is stored,
but both were admin-only. These two endpoints expose them publicly:

* ``GET /api/v2/features`` -- the static catalog of every generatable feature.
* ``GET /api/v2/sessions/{year}/{gp}/{session}/availability`` -- what is
  actually stored for one specific session right now.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.concurrency import run_in_threadpool

from src.api.deps import validate_gp, validate_session
from src.api.schemas.common import ANALYSIS_ERROR_RESPONSES, COMMON_ERROR_RESPONSES
from src.api.schemas.discovery import FeatureCatalogResponse, SessionAvailabilityResponse
from src.core.config import settings
from src.core.logging import get_logger
from src.core.security.api_keys import verify_api_key
from src.core.security.rate_limiting import apply_tiered_limit
from src.services.analysis.v2.registry import (
    FEATURE_CATALOG,
    KIND_CAREER,
    KIND_PER_DRIVER,
    KIND_PER_DRIVER_LAP,
    KIND_PER_PAIR,
    KIND_SEASON,
    KIND_SINGLETON,
    session_drivers,
)
from src.services.orchestrator_helpers import simplify_session_name
from src.workers.plot_inventory import compute_inventory

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v2")

#: Every ``kind`` a caller may filter ``/features`` by. Kept as a set literal
#: (rather than reading ``FeatureEntry.kind`` values off the catalog) so an
#: invalid filter is rejected even if the catalog is momentarily empty.
_VALID_KINDS = frozenset(
    {KIND_SINGLETON, KIND_PER_DRIVER, KIND_PER_PAIR, KIND_PER_DRIVER_LAP, KIND_SEASON, KIND_CAREER}
)


def _normalize_session_filter(session: str) -> str:
    """Map ``Q``/``FP1``/``Qualifying`` etc. onto the registry's abbreviations."""
    return simplify_session_name(session).strip().upper()


@router.get(
    "/features",
    tags=["API v2", "Seasonal Data"],
    summary="Catalog of every generatable V2 feature",
    operation_id="v2_feature_catalog",
    description=(
        "Static catalog of every V2 analysis feature the backend can generate -- "
        "singletons through season/career scope -- read from the same registry the "
        "admin backfill uses, so it can never drift from what actually exists. "
        "Optionally filter by `session` (e.g. `Q`, `FP1`, or a long form like "
        "`Qualifying`) to only see features applicable to that session type, and/or "
        "by `kind` (singleton, per_driver, per_pair, per_driver_lap, season, career). "
        "The catalog is static for a given build, so the response is cacheable."
    ),
    responses={200: {"model": FeatureCatalogResponse}, **COMMON_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def v2_feature_catalog(
    request: Request,
    session: Optional[str] = Query(
        None, description="Optional session filter, e.g. Q, FP1, or a long form like Qualifying."
    ),
    kind: Optional[str] = Query(
        None,
        description=(
            "Optional kind filter: singleton, per_driver, per_pair, per_driver_lap, "
            "season, career."
        ),
    ),
    api_key: str = Depends(verify_api_key),
):
    entries = FEATURE_CATALOG

    if kind is not None:
        if kind not in _VALID_KINDS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid kind '{kind}'. Valid values: {', '.join(sorted(_VALID_KINDS))}",
            )
        entries = [e for e in entries if e.kind == kind]

    if session:
        normalized = _normalize_session_filter(session)
        entries = [e for e in entries if e.session_scoped and e.applies(normalized)]

    by_group: "OrderedDict[str, int]" = OrderedDict()
    for entry in entries:
        by_group[entry.group] = by_group.get(entry.group, 0) + 1

    return {
        "features": [entry.as_dict() for entry in entries],
        "count": len(entries),
        "groups": [{"group": g, "count": c} for g, c in sorted(by_group.items())],
    }


@router.get(
    "/sessions/{year}/{gp}/{session}/availability",
    tags=["API v2", "Seasonal Data"],
    summary="Which V2 features are already generated for a session",
    operation_id="v2_session_feature_availability",
    description=(
        "Reports which singleton V2 features are already stored for this session "
        "versus still missing, so a frontend can render only populated tiles instead "
        "of firing every analysis endpoint and handling the failures. `gp` accepts a "
        "round number, event key, or official event name, same as the rest of v2. "
        "Set `include_drivers=true` to also resolve the participating driver TLAs -- "
        "this costs a livetiming fetch, so it defaults to false and is skipped unless "
        "asked for."
    ),
    responses={
        200: {"model": SessionAvailabilityResponse},
        404: {"description": "The event or session is not in the season schedule."},
        **ANALYSIS_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("data")
async def v2_session_feature_availability(
    request: Request,
    year: int = Path(..., ge=settings.min_year, le=settings.max_year, description="Season year."),
    gp: Union[int, str] = Path(..., description="Round number, event key, or official event name."),
    session: str = Path(..., description="Session identifier, e.g. FP1, Q, SQ, S, R."),
    include_drivers: bool = Query(
        False,
        description="Also resolve participating driver TLAs. Costs a livetiming fetch; off by default.",
    ),
    api_key: str = Depends(verify_api_key),
):
    validated_gp = validate_gp(gp)
    validated_session = validate_session(session)

    logger.info(
        "Fetching V2 feature availability: Y%s GP%s %s", year, validated_gp, validated_session
    )
    inventory = await run_in_threadpool(
        compute_inventory, year=year, identifier=validated_gp, session=validated_session
    )

    grand_prix = inventory.get("grand_prix") or []
    if not grand_prix:
        raise HTTPException(status_code=404, detail="No such event in the season schedule.")

    gp_entry = grand_prix[0]
    sessions = gp_entry.get("sessions") or []
    if not sessions:
        raise HTTPException(
            status_code=404,
            detail=f"Session '{validated_session}' is not scheduled for this event.",
        )
    session_row = sessions[0]

    labels = session_row.get("labels") or {}
    present = set(session_row.get("present") or [])
    expected: List[str] = session_row.get("expected") or []
    features = [
        {"data_type": dt, "label": labels.get(dt, dt), "available": dt in present}
        for dt in expected
    ]

    drivers: Optional[List[str]] = None
    if include_drivers:
        try:
            drivers = await run_in_threadpool(
                session_drivers,
                year,
                gp_entry.get("event_name") or validated_gp,
                validated_session,
            )
        except Exception as exc:
            logger.warning(
                "Could not resolve drivers for Y%s GP%s %s: %s",
                year, validated_gp, validated_session, exc,
            )
            drivers = []

    return {
        "year": year,
        "gp": gp_entry.get("event_name"),
        "round_nr": gp_entry.get("round_nr"),
        "session": validated_session,
        "available": sorted(present),
        "missing": sorted(session_row.get("missing") or []),
        "features": features,
        "drivers": drivers,
    }
