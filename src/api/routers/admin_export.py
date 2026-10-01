"""Admin API for the dataset-export subsystem: plan, run, browse and archive.

Mirrors ``admin_storage.py``'s shape. The heavy lifting lives in
``src/workers/dataset_export.py`` (plan/estimate/job orchestration) and
``src/services/dataset_export/exports.py`` (the export directory's read-model
and file operations); this router is a thin HTTP wrapper that maps the
service's exceptions onto status codes.

**No ``from __future__ import annotations`` in this module.** ``/jobs`` combines
a Pydantic body with ``@apply_tiered_limit``; PEP 563 stringifies the
annotation, slowapi's wrapper stops FastAPI resolving it, and the body
silently degrades into a required *query* parameter (422 ``Field required,
loc: ['query', 'body']``). See the trap documented in ``CLAUDE.md``.
"""
import re
import shutil
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from src.api.admin_security import admin_api_gate, apply_no_index
from src.api.routers.admin import ADMIN_ERROR_RESPONSES, _err, require_admin_key
from src.api.schemas.admin import (
    ExportDeleteResponse,
    ExportEstimateResponse,
    ExportFilesResponse,
    ExportJobStartResponse,
    ExportListResponse,
    ExportRequest,
    JobCancelResponse,
    JobRecord,
    JobsListResponse,
)
from src.core.config import settings
from src.core.logging import get_logger
from src.core.security.rate_limiting import apply_tiered_limit
from src.services.dataset_export import exports
from src.workers import dataset_export

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/export", tags=["Admin"],
                   dependencies=[Depends(admin_api_gate)])

_JOB_ID_RE = re.compile(r"^[a-f0-9]{12}$")


def _validate_job_id(job_id: str) -> None:
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail=f"no job with id {job_id!r}")


@router.post(
    "/estimate",
    summary="Cost a dataset-export scope (dry run)",
    operation_id="admin_export_estimate",
    responses={200: {"model": ExportEstimateResponse}, **ADMIN_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def admin_export_estimate(
    request: Request,
    body: ExportRequest,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Expand a scope into a plan and cost it: unit counts, bytes, seconds, free disk.

    Read-only. Uses a throwaway ``export_id`` when the caller doesn't supply one, since
    the estimate never touches the filesystem.
    """
    try:
        plan = await run_in_threadpool(
            dataset_export.build_export_plan,
            years=body.years, identifier=body.gp, session=body.session,
            session_types=body.session_types, tiers=body.tiers,
            export_id=body.export_id or "estimate_preview", resume=body.resume,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except exports.ExportNotFound as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await run_in_threadpool(dataset_export.estimate_export_plan, plan)


@router.post(
    "/jobs",
    summary="Start a dataset-export job",
    operation_id="admin_export_start",
    responses={
        200: {"model": ExportJobStartResponse},
        409: _err("An export/archive job is already running, or free disk is below the configured minimum."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_export_start(
    request: Request,
    body: ExportRequest,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Plan and launch a dataset export on a daemon thread.

    Refuses to start when free disk on the export volume is below
    ``settings.export_min_free_gb`` -- a large telemetry/raw export can otherwise
    fill the volume mid-run.
    """
    free = shutil.disk_usage(exports.export_root()).free
    if free < settings.export_min_free_gb * 1e9:
        raise HTTPException(
            status_code=409,
            detail=f"only {free / 1e9:.1f} GB free; need at least {settings.export_min_free_gb} GB",
        )
    try:
        job = await run_in_threadpool(
            dataset_export.start_export_job,
            years=body.years, identifier=body.gp, session=body.session,
            session_types=body.session_types, tiers=body.tiers,
            export_id=body.export_id, resume=body.resume, concurrency=body.concurrency,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return job.as_dict()


@router.get(
    "/jobs",
    summary="Dataset-export job history",
    operation_id="admin_export_jobs",
    responses={200: {"model": JobsListResponse}, **ADMIN_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def admin_export_jobs(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    status: Optional[str] = Query(None, description="queued|running|completed|failed|cancelled"),
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Export and archive job history, durable across restarts."""
    jobs = await run_in_threadpool(dataset_export.list_jobs, limit=limit, status=status)
    return {"count": len(jobs), "jobs": jobs}


@router.get(
    "/jobs/{job_id}",
    summary="Single dataset-export job status",
    operation_id="admin_export_job",
    responses={
        200: {"model": JobRecord},
        404: _err("No job with that id, in memory or in MongoDB."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_export_job(
    request: Request,
    job_id: str,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    _validate_job_id(job_id)
    doc = await run_in_threadpool(dataset_export.get_job_dict, job_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"no job with id {job_id!r}")
    return doc


@router.post(
    "/jobs/{job_id}/cancel",
    summary="Cancel a running dataset-export job",
    operation_id="admin_export_job_cancel",
    responses={
        200: {"model": JobCancelResponse},
        409: _err("The job is not cancellable (already terminal, or unknown)."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_export_job_cancel(
    request: Request,
    job_id: str,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    _validate_job_id(job_id)
    ok = await run_in_threadpool(dataset_export.cancel_job, job_id)
    if not ok:
        raise HTTPException(status_code=409, detail=f"job {job_id!r} is not cancellable")
    return {"job_id": job_id, "cancel_requested": True}


@router.get(
    "/exports",
    summary="List dataset exports",
    operation_id="admin_export_list",
    responses={200: {"model": ExportListResponse}, **ADMIN_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def admin_export_list(
    request: Request,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Every export directory holding a manifest, newest first."""
    summaries = await run_in_threadpool(exports.list_exports)
    return {"count": len(summaries), "exports": [s.to_dict() for s in summaries]}


@router.get(
    "/exports/{export_id}/manifest",
    summary="Raw manifest for one export",
    operation_id="admin_export_manifest",
    responses={
        200: {"description": "The export's manifest.json, as stored (not a fixed schema)."},
        404: _err("No export with that id."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_export_manifest(
    request: Request,
    export_id: str,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    try:
        manifest = await run_in_threadpool(exports.load_manifest, export_id)
    except exports.ExportNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return manifest.to_dict()


@router.get(
    "/exports/{export_id}/files",
    summary="List files recorded in an export's manifest",
    operation_id="admin_export_files",
    responses={
        200: {"model": ExportFilesResponse},
        404: _err("No export with that id."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_export_files(
    request: Request,
    export_id: str,
    prefix: str = Query("", description="Only files whose relative path starts with this."),
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    try:
        files = await run_in_threadpool(exports.list_export_files, export_id, prefix)
    except exports.ExportNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"export_id": export_id, "count": len(files), "files": [f.to_dict() for f in files]}


@router.get(
    "/exports/{export_id}/files/{path:path}",
    summary="Download one file from an export",
    operation_id="admin_export_file_download",
    responses={
        200: {"content": {"application/octet-stream": {}}},
        404: _err("No export with that id, or no such file within it."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_export_file_download(
    request: Request,
    export_id: str,
    path: str,
    api_key: str = Depends(require_admin_key),
) -> FileResponse:
    """Serve one exported file by its manifest-relative path.

    ``resolve_export_file`` re-resolves the path within the export root, so a
    traversal attempt (``../``) or an in-progress ``*.tmp`` file both 404
    rather than reaching outside the export or serving a truncated file.
    """
    try:
        real_path = await run_in_threadpool(exports.resolve_export_file, export_id, path)
    except exports.ExportNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    response = FileResponse(real_path, filename=real_path.name, media_type="application/octet-stream")
    return apply_no_index(response)


@router.post(
    "/exports/{export_id}/archive",
    summary="Build a zip archive of an export",
    operation_id="admin_export_archive_start",
    responses={
        200: {"model": ExportJobStartResponse},
        404: _err("No export with that id."),
        409: _err("The export has a running job, or an archive build is already in progress."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_export_archive_start(
    request: Request,
    export_id: str,
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    try:
        job = await run_in_threadpool(dataset_export.start_archive_job, export_id)
    except exports.ExportNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.as_dict()


@router.get(
    "/exports/{export_id}/archive",
    summary="Download an export's zip archive",
    operation_id="admin_export_archive_download",
    responses={
        200: {"content": {"application/zip": {}}},
        404: _err("No archive exists yet for this export (not built, or still building)."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_export_archive_download(
    request: Request,
    export_id: str,
    api_key: str = Depends(require_admin_key),
) -> FileResponse:
    status_info = await run_in_threadpool(exports.archive_status, export_id)
    if status_info["status"] != "ready":
        raise HTTPException(status_code=404, detail=f"no ready archive for export {export_id!r}")
    path = exports.archive_path(export_id)
    response = FileResponse(path, filename=path.name, media_type="application/zip")
    return apply_no_index(response)


@router.delete(
    "/exports/{export_id}",
    summary="Delete a dataset export (destructive)",
    operation_id="admin_export_delete",
    responses={
        200: {"model": ExportDeleteResponse},
        400: _err("`confirm` did not match the export id."),
        404: _err("No export with that id."),
        409: _err("The export has a running job."),
        **ADMIN_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("standard")
async def admin_export_delete(
    request: Request,
    export_id: str,
    confirm: str = Query(..., description="Must equal `export_id`, to guard against a stray call."),
    api_key: str = Depends(require_admin_key),
) -> Dict[str, Any]:
    if confirm != export_id:
        raise HTTPException(status_code=400, detail="confirm must equal export_id")
    try:
        freed = await run_in_threadpool(
            exports.delete_export, export_id, in_use=dataset_export.export_in_use,
        )
    except exports.ExportBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except exports.ExportNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.warning("Admin dataset-export delete: export_id=%s bytes_freed=%s", export_id, freed)
    return {"export_id": export_id, "bytes_freed": freed}
