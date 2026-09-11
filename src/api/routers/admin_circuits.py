"""Admin API for tracing and inspecting derived circuit layouts.

Circuit geometry is normally seeded from api.multiviewer.app. A brand-new
circuit -- Madring in 2026 was the first -- can run a whole weekend before
multiviewer publishes it, and until then every map-shaped feature degrades
(``/api/v2/telemetry/track-map`` 404s, the track-map plot renders unrotated,
corner-duel falls back to sequential numbers). :mod:`src.services.circuit_derivation`
fills that gap by tracing the outline from one lap of livetiming position data.

``GET /status``  reports, per scheduled event in a season, whether a layout is
                 stored and where it came from (schedule-driven, so a circuit
                 multiviewer hasn't listed yet shows up as missing rather than
                 silently absent).
``POST /derive`` runs the derivation. It defaults to ``dry_run=true`` so an
                 operator can inspect the traced outline and corner count
                 before anything is written; the written result is provisional
                 (``source="telemetry"``) and gets replaced once ``circuits_sync``
                 picks up multiviewer's real layout.

**No ``from __future__ import annotations`` in this module.** These endpoints
combine a Pydantic body with ``@apply_tiered_limit``; PEP 563 stringifies the
annotation, slowapi's wrapper stops FastAPI resolving it, and the body silently
degrades into a required *query* parameter (422 ``Field required, loc:
['query', 'body']``). See the trap documented in ``CLAUDE.md``.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from src.api.admin_security import admin_api_gate
from src.api.deps import validate_driver, validate_gp, validate_session
from src.api.routers.admin import ADMIN_ERROR_RESPONSES, _err, require_admin_key
from src.api.schemas.admin import (
    CircuitDeriveRequest,
    CircuitDeriveResponse,
    CircuitLayoutStatusResponse,
)
from src.core.logging import get_logger
from src.core.security.rate_limiting import apply_tiered_limit
from src.services.admin_views import circuit_layout_status
from src.services.circuit_derivation import (
    CircuitDerivationError,
    DeriveOptions,
    LayoutExistsError,
    derive_and_store,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/circuits", tags=["Admin"],
                   dependencies=[Depends(admin_api_gate)])


@router.get(
    "/status",
    summary="List which circuits in a season have a stored layout",
    operation_id="admin_circuits_status",
    responses={200: {"model": CircuitLayoutStatusResponse}, **ADMIN_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def admin_circuits_status(
    request: Request,
    year: int = Query(..., ge=2018, le=2030, description="Season year."),
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Per-event layout coverage for a season.

    Schedule-driven (enumerates every event on the calendar) rather than a
    directory scan, so a brand-new circuit with no file at all is reported as
    *missing* instead of not existing at all. Read-only.
    """
    return await run_in_threadpool(circuit_layout_status, year)


@router.post(
    "/derive",
    summary="Derive a circuit layout from a session's fastest lap",
    operation_id="admin_circuits_derive",
    responses={
        200: {"model": CircuitDeriveResponse},
        400: _err("Invalid gp/session/driver."),
        404: _err("Event or session not found upstream."),
        409: _err("A layout already exists for this circuit/year; pass overwrite=true."),
        422: _err("No candidate lap produced a closed outline."),
        503: _err("Telemetry not published upstream yet."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_circuits_derive(
    request: Request,
    body: CircuitDeriveRequest,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Trace a track outline and corners from one lap of ``Position.z``.

    Defaults to ``dry_run=true`` -- inspect the traced outline and corner
    count before writing anything. The stored result is provisional
    (``source="telemetry"``) and is replaced once ``circuits_sync`` picks up
    multiviewer's published layout for this circuit. ``Position.z`` and
    ``CarData.z`` are multi-megabyte livetiming streams, so a cold call
    (nothing cached yet) takes roughly 30 seconds.
    """
    year = body.year
    gp = validate_gp(body.gp)
    session = validate_session(body.session)
    driver = validate_driver(body.driver)

    options = DeriveOptions(driver=driver, rotation=body.rotation, min_turn_deg=body.min_turn_deg)
    try:
        result = await run_in_threadpool(
            derive_and_store, year, gp, session, options,
            overwrite=body.overwrite, dry_run=body.dry_run,
        )
    except LayoutExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CircuitDerivationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result.pop("layout", None)
    if result["written"]:
        logger.warning(
            "Admin circuit layout written: %s %s -> %s",
            result["year"], result["circuit_id"], result["path"],
        )
    return result
