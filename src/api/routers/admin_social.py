"""Admin API for social packs: one session's charts, rendered in social formats.

The heavy lifting lives in ``src/workers/social_pack.py`` (planning, the job and
the on-disk layout); this router is a thin HTTP wrapper that maps the worker's
exceptions onto status codes. Mirrors ``admin_export.py``.

**No ``from __future__ import annotations`` in this module.** ``/generate``
combines a Pydantic body with ``@apply_tiered_limit``; PEP 563 stringifies the
annotation, slowapi's wrapper stops FastAPI resolving it, and the body
silently degrades into a required *query* parameter (422 ``Field required,
loc: ['query', 'body']``). See the trap documented in ``CLAUDE.md``.
"""
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from src.api.admin_security import admin_api_gate, apply_no_index
from src.api.routers.admin import ADMIN_ERROR_RESPONSES, _err, require_admin_key
from src.api.schemas.admin import (
    JobCancelResponse,
    SocialJobRecord,
    SocialJobsListResponse,
    SocialPackRequest,
)
from src.core.logging import get_logger
from src.core.security.rate_limiting import apply_tiered_limit
from src.workers import social_pack

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/social", tags=["Admin"],
                   dependencies=[Depends(admin_api_gate)])

_JOB_ID_RE = re.compile(r"^[a-f0-9]{12}$")


def _validate_job_id(job_id: str) -> None:
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail=f"no job with id {job_id!r}")


@router.post(
    "/generate",
    summary="Start a social-pack job",
    operation_id="admin_social_generate",
    responses={
        200: {"model": SocialJobRecord},
        400: _err("Unusable scope (unknown session, bad driver code, ...)."),
        409: _err("A social-pack job is already running."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_social_generate(
    request: Request,
    body: SocialPackRequest,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Render every relevant chart of one session in the chosen formats.

    Runs on a daemon thread; poll ``GET /jobs/{id}``. The finished pack is a
    folder under ``settings.social_dir`` plus ``social_pack.zip``, downloadable
    from ``GET /jobs/{id}/download``. Only one social-pack job runs at a time.
    """
    try:
        job = await run_in_threadpool(
            social_pack.start_social_job,
            year=body.year, gp=body.gp, session=body.session, formats=body.formats,
            pairs=body.pairs, include_season=body.include_season,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return job.as_dict()


@router.get(
    "/jobs",
    summary="Social-pack job history",
    operation_id="admin_social_jobs",
    responses={200: {"model": SocialJobsListResponse}, **ADMIN_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def admin_social_jobs(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    status: Optional[str] = Query(None, description="queued|running|completed|failed|cancelled"),
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Social-pack job history, durable across restarts."""
    jobs = await run_in_threadpool(social_pack.list_jobs, limit=limit, status=status)
    return {"count": len(jobs), "jobs": jobs}


@router.get(
    "/jobs/{job_id}",
    summary="Single social-pack job status",
    operation_id="admin_social_job",
    responses={
        200: {"model": SocialJobRecord},
        404: _err("No job with that id, in memory or in MongoDB."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_social_job(
    request: Request,
    job_id: str,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    _validate_job_id(job_id)
    doc = await run_in_threadpool(social_pack.get_job_dict, job_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"no job with id {job_id!r}")
    return doc


@router.post(
    "/jobs/{job_id}/cancel",
    summary="Cancel a running social-pack job",
    operation_id="admin_social_job_cancel",
    responses={
        200: {"model": JobCancelResponse},
        409: _err("The job is not cancellable (already terminal, or unknown)."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_social_job_cancel(
    request: Request,
    job_id: str,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    _validate_job_id(job_id)
    ok = await run_in_threadpool(social_pack.cancel_job, job_id)
    if not ok:
        raise HTTPException(status_code=409, detail=f"job {job_id!r} is not cancellable")
    return {"job_id": job_id, "cancel_requested": True}


@router.get(
    "/jobs/{job_id}/download",
    summary="Download a finished social pack (zip)",
    operation_id="admin_social_job_download",
    responses={
        200: {"content": {"application/zip": {}}},
        404: _err("No such job, or its zip does not exist (yet)."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_social_job_download(
    request: Request,
    job_id: str,
    api_key: str = Depends(require_admin_key),
) -> FileResponse:
    """Serve ``social_pack.zip``.

    The pack path comes from the stored job document, so it is re-resolved
    through ``resolve_within``: a tampered document that points outside the
    social root 404s rather than serving an arbitrary file.
    """
    path = await run_in_threadpool(social_pack.pack_download_path, job_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"no downloadable pack for job {job_id!r}")
    response = FileResponse(path, filename=f"social_pack_{job_id}.zip", media_type="application/zip")
    return apply_no_index(response)
