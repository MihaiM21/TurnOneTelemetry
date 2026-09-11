"""Admin API for scoped, preview-first cleanup of stored data.

``admin_data.py`` can delete exactly one ``data_type`` from one session, and
``admin_cache.py`` can drop one raw-stream session or one bundle. Neither can
answer the operator's actual question -- "reclaim everything for the 2023
season", or "drop every stored ``speed_distribution`` so it regenerates" -- and
neither touches the rendered PNGs under ``outputs/plots``, which had no delete
path at all and therefore grew without bound.

This router exposes :mod:`src.services.storage_cleanup` over HTTP in two steps:

``POST /preview``  enumerates what would be deleted and returns a
                   ``confirm_token`` fingerprinting that exact item set.
``POST /purge``    re-plans, and refuses unless the token still matches.

The token is what makes the destructive endpoint safe to expose: a purge can
only ever delete something the caller has already seen listed. It is the same
double-confirmation shape the backup restore uses.

**No ``from __future__ import annotations`` in this module.** These endpoints
combine a Pydantic body with ``@apply_tiered_limit``; PEP 563 stringifies the
annotation, slowapi's wrapper stops FastAPI resolving it, and the body silently
degrades into a required *query* parameter (422 ``Field required, loc:
['query', 'body']``). See the trap documented in ``CLAUDE.md``.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from src.api.admin_security import admin_api_gate
from src.api.routers.admin import ADMIN_ERROR_RESPONSES, _err, require_admin_key
from src.api.schemas.admin import (
    StorageOrphansResponse,
    StoragePreviewResponse,
    StoragePurgeRequest,
    StoragePurgeResponse,
    StorageTotalsResponse,
)
from src.core.logging import get_logger
from src.core.security.rate_limiting import apply_tiered_limit
from src.services.storage_cleanup import (
    ALL_LAYERS,
    CleanupError,
    CleanupScope,
    execute_cleanup,
    find_orphan_plot_dirs,
    plan_cleanup,
    storage_totals,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/storage", tags=["Admin"],
                   dependencies=[Depends(admin_api_gate)])


def _build_scope(
    year: Optional[int],
    gp: Optional[str],
    session: Optional[str],
    data_type: Optional[str],
    layers: Optional[List[str]],
) -> CleanupScope:
    """Validate a caller-supplied scope into a :class:`CleanupScope`.

    An unknown layer name is a 400 rather than a silent no-op, because silently
    ignoring it would make a purge look narrower than the caller intended.
    """
    try:
        return CleanupScope(
            year=year,
            gp=gp,
            session=session,
            data_type=data_type,
            layers=tuple(layers) if layers else ALL_LAYERS,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/totals",
    summary="Summarise what stored data is costing",
    operation_id="admin_storage_totals",
    responses={200: {"model": StorageTotalsResponse}, **ADMIN_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def admin_storage_totals(
    request: Request,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Headline figures across every storage layer, plus how much is
    recoverable from duplicate plot directories alone. Read-only."""
    return await run_in_threadpool(storage_totals)


@router.get(
    "/orphans",
    summary="List duplicate Grand Prix plot directories",
    operation_id="admin_storage_orphans",
    responses={200: {"model": StorageOrphansResponse}, **ADMIN_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def admin_storage_orphans(
    request: Request,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Find Grand Prix directories stored twice under different spellings.

    ``outputs/plots`` accumulated both ``2025/Abu Dhabi Grand Prix`` and
    ``2025/AbuDhabiGrandPrix`` because the writer's naming convention changed
    without a migration. Only one spelling is read, so the other is dead
    weight. Read-only -- deciding which spelling is canonical is an operator
    call, so this reports and never deletes. Feed the loser's directory name
    back as a ``gp`` scope to remove it.
    """
    groups = await run_in_threadpool(find_orphan_plot_dirs)
    return {
        "groups": groups,
        "total_groups": len(groups),
        "total_reclaimable_bytes": sum(g["reclaimable_bytes"] for g in groups),
    }


@router.post(
    "/preview",
    summary="Preview a cleanup without deleting anything",
    operation_id="admin_storage_preview",
    responses={
        200: {"model": StoragePreviewResponse},
        400: _err("Unknown storage layer name."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_storage_preview(
    request: Request,
    year: Optional[int] = Query(None, description="Season year, e.g. 2025."),
    gp: Optional[str] = Query(None, description="Grand Prix: gp_id, round number or event name."),
    session: Optional[str] = Query(None, description="Session type; `R` also matches legacy `Race`."),
    data_type: Optional[str] = Query(None, description="A single stored feature key."),
    layers: Optional[List[str]] = Query(
        None,
        description="Layers to include. Defaults to all: mongo, raw_streams, bundles, plots, redis.",
    ),
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Enumerate every item the equivalent purge would delete.

    Strictly read-only. The returned ``confirm_token`` fingerprints this exact
    item set and is required by ``/purge``; if storage changes in between, the
    purge is refused rather than deleting something never previewed.

    Scoping by ``data_type`` deliberately returns no raw-stream or bundle
    items: those layers are per-session, so removing them for one feature would
    force every other feature of that session to re-download.
    """
    scope = _build_scope(year, gp, session, data_type, layers)
    return await run_in_threadpool(plan_cleanup, scope)


@router.post(
    "/purge",
    summary="Delete stored data for a scope (destructive)",
    operation_id="admin_storage_purge",
    responses={
        200: {"model": StoragePurgeResponse},
        400: _err("Unknown layer, or an unscoped purge without `allow_full_purge`."),
        409: _err("Storage changed since the preview; re-run `/preview` and confirm the new plan."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_storage_purge(
    request: Request,
    body: StoragePurgeRequest,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Permanently delete everything in scope. Not reversible.

    Requires the ``confirm_token`` from a matching ``/preview``. A scope with
    no filters at all means "delete every stored year", so it additionally
    requires ``allow_full_purge`` -- an accidental empty body cannot wipe the
    database.

    Layers are processed durable-first and Redis last, so an interrupted run
    degrades toward "the cache is cold" rather than "the payload is gone but
    the cache still serves it". Per-item failures are collected and returned
    rather than aborting the run.
    """
    scope = _build_scope(
        body.scope.year, body.scope.gp, body.scope.session,
        body.scope.data_type, body.scope.layers,
    )
    try:
        result = await run_in_threadpool(
            execute_cleanup, scope, body.confirm_token,
            allow_full_purge=body.allow_full_purge,
        )
    except CleanupError as exc:
        message = str(exc)
        status = 409 if "changed since the preview" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc
    logger.warning(
        "Admin storage purge: scope=%s deleted=%s bytes=%s",
        scope.to_dict(), result["total_deleted"], result["bytes_reclaimed"],
    )
    return result
