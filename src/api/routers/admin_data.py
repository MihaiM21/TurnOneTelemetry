"""Admin API for browsing and pruning stored V2 plot data.

The plot inventory answers "what is *missing*" against the singleton
expectation set. It cannot answer "what is actually *stored*", which is what you
need when a payload was generated from bad upstream data: the inventory reports
it as present, so the backfill skips it, and nothing regenerates it until the
document is removed. Deleting stored plot data was not possible anywhere in the
repositories layer before this.
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
from src.repositories.mongo import MongoDBManager
from src.services.admin_views import browse_stored_data

logger = get_logger(__name__)


def _admin_api_gate(request: Request, response: Response) -> None:
    enforce_ip_allowlist(request)
    enforce_ui_rate_limit(request)
    for k, v in NO_INDEX_HEADERS.items():
        response.headers[k] = v


router = APIRouter(prefix="/api/admin/data", tags=["General"],
                   dependencies=[Depends(_admin_api_gate)])


@router.get("/browse")
@apply_tiered_limit("standard")
async def admin_data_browse(
    request: Request,
    year: int = Query(..., ge=2018, le=2030),
    gp: Optional[str] = Query(None, description="Event name or gp_id"),
    session: Optional[str] = Query(None, description="Session abbrev (e.g. R, Q, FP1)"),
    api_key: str = Depends(require_admin_key),
):
    """Everything stored for a scope, including legacy/orphan keys."""
    return await run_in_threadpool(browse_stored_data, year, gp, session)


@router.delete("/{year}/{gp_id}/{session_type}/{data_type}")
@apply_tiered_limit("standard")
async def admin_data_delete(
    request: Request,
    year: int,
    gp_id: str,
    session_type: str,
    data_type: str,
    api_key: str = Depends(require_admin_key),
):
    """Delete one stored ``data_type`` so it can be regenerated from scratch."""
    manager = MongoDBManager(year=year, version="v2")
    removed = await run_in_threadpool(
        manager.delete_session_data, gp_id, session_type.upper(), data_type, year
    )
    if not removed:
        raise HTTPException(status_code=404, detail="No such stored data_type")
    logger.info("Admin deleted %s from %s %s (%s)", data_type, gp_id, session_type, year)
    return {
        "year": year, "gp_id": gp_id, "session_type": session_type,
        "data_type": data_type, "deleted": True,
    }
