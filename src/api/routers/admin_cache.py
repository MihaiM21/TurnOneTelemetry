"""Admin API for the durable V2 caches.

Two caches sit under the V2 pipeline and, until this router, neither had any
admin visibility at all: no size, no age, no way to purge a poisoned entry.

* **Raw streams** — GridFS bucket ``v2_raw_cache``, one gzipped blob per
  ``(session, stream)``. This is where the multi-megabyte ``CarData.z`` and
  ``Position.z`` payloads live.
* **Derived bundles** — collection ``v2_session_cache``, one small document per
  session holding the lap-indexed structures the race features consume.

Purging matters because both are keyed by a ``SCHEMA_VERSION``: after a bump,
old entries are silently unreadable but still occupy storage. The inventory
surfaces that as ``schema_drift``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from src.api.admin_security import (
    NO_INDEX_HEADERS,
    enforce_ip_allowlist,
    enforce_ui_rate_limit,
)
from src.api.routers.admin import require_admin_key
from src.core.logging import get_logger
from src.core.security.rate_limiting import apply_tiered_limit
from src.repositories import raw_stream_cache, session_cache
from src.services.admin_views import cache_inventory

logger = get_logger(__name__)


def _admin_api_gate(request: Request, response: Response) -> None:
    enforce_ip_allowlist(request)
    enforce_ui_rate_limit(request)
    for k, v in NO_INDEX_HEADERS.items():
        response.headers[k] = v


router = APIRouter(prefix="/api/admin/cache", tags=["General"],
                   dependencies=[Depends(_admin_api_gate)])


@router.get("/inventory")
@apply_tiered_limit("standard")
async def admin_cache_inventory(
    request: Request,
    year: Optional[int] = Query(None, ge=2018, le=2030),
    api_key: str = Depends(require_admin_key),
):
    """What the durable V2 caches currently hold: sizes, ages, schema drift."""
    return await run_in_threadpool(cache_inventory, year)


@router.post("/purge/raw/{session_key}")
@apply_tiered_limit("standard")
async def admin_cache_purge_raw(
    request: Request,
    session_key: str,
    api_key: str = Depends(require_admin_key),
):
    """Drop every raw stream blob for one session (``{year}_{round}_{session}``)."""
    removed = await run_in_threadpool(raw_stream_cache.delete_raw_streams, session_key)
    if not removed:
        raise HTTPException(status_code=404, detail="No cached streams for that session")
    return {"session_key": session_key, "removed": removed}


@router.post("/purge/bundle/{doc_id}")
@apply_tiered_limit("standard")
async def admin_cache_purge_bundle(
    request: Request,
    doc_id: str,
    api_key: str = Depends(require_admin_key),
):
    """Drop one derived bundle so it is recomputed from the raw streams."""
    if not await run_in_threadpool(session_cache.delete_bundle, doc_id):
        raise HTTPException(status_code=404, detail="No bundle with that id")
    return {"doc_id": doc_id, "removed": True}


@router.post("/prewarm")
@apply_tiered_limit("standard")
async def admin_cache_prewarm(
    request: Request,
    year: int = Query(..., ge=2018, le=2030),
    gp: str = Query(..., description="Round number, Event Key, or Event Name"),
    session: str = Query(..., description="Session name/abbrev (e.g. R, Q, FP1)"),
    api_key: str = Depends(require_admin_key),
):
    """Populate both cache tiers for a session up front.

    Reuses the background processor's prewarm so the admin path and the
    automatic post-session path warm exactly the same streams.
    """
    from src.workers.processor import get_processor

    try:
        await run_in_threadpool(
            get_processor().prewarm_session_streams, year, gp, session, gp
        )
    except Exception as exc:
        logger.error("Prewarm failed for %s %s %s: %s", year, gp, session, exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Prewarm failed")
    return {"year": year, "gp": gp, "session": session, "prewarmed": True}
