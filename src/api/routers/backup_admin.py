"""Admin endpoints for the backup subsystem.

All routes are gated by the same admin gate / IP allowlist / rate limit as
the rest of ``/api/admin``. Mounted under ``/api/admin/backup``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from src.api.admin_security import enforce_ip_allowlist, enforce_ui_rate_limit
from src.api.routers.admin import require_admin_key
from src.core.config import settings
from src.core.logging import get_logger
from src.core.security.rate_limiting import apply_tiered_limit
from src.services.backup.restore import RestoreRunner
from src.services.backup.scheduler import get_scheduler
from src.services.backup.storage import S3Storage

logger = get_logger(__name__)


def _gate(request: Request) -> None:
    enforce_ip_allowlist(request)
    enforce_ui_rate_limit(request)


router = APIRouter(prefix="/api/admin/backup", tags=["General"], dependencies=[Depends(_gate)])


def _require_enabled() -> None:
    if not settings.backup_enabled:
        raise HTTPException(status_code=503, detail="Backup subsystem is disabled (BACKUP_ENABLED=false)")


class RestoreRequest(BaseModel):
    i_understand: bool = False
    components: Optional[list[str]] = None
    mongo_target_db: Optional[str] = None
    mongo_drop: bool = False


# ── status / discovery ────────────────────────────────────────────────────
@router.get("/status")
@apply_tiered_limit("standard")
async def backup_status(request: Request, api_key: str = Depends(require_admin_key)):
    sched = get_scheduler()
    s3_ok: Optional[bool] = None
    if settings.backup_enabled:
        try:
            s3_ok = await run_in_threadpool(lambda: S3Storage().ping())
        except Exception as exc:
            s3_ok = False
            logger.warning(f"S3 ping failed: {exc}")
    last = sched.last_result.manifest if sched.last_result else None
    return {
        "enabled": settings.backup_enabled,
        "scheduler_running": sched.running,
        "manual_in_progress": sched._manual_in_progress,
        "last_run_at": sched.last_run_at.isoformat() if sched.last_run_at else None,
        "next_run_at": sched.next_run_at.isoformat() if sched.next_run_at else None,
        "last_error": sched.last_error,
        "last_backup_id": last.backup_id if last else None,
        "last_backup_tier": last.tier if last else None,
        "last_backup_success": last.success if last else None,
        "s3_reachable": s3_ok,
        "config": {
            "schedule_utc": f"{settings.backup_schedule_hour_utc:02d}:{settings.backup_schedule_minute_utc:02d}",
            "retention_daily": settings.backup_retention_daily,
            "retention_weekly": settings.backup_retention_weekly,
            "retention_monthly": settings.backup_retention_monthly,
            "include_volumes": settings.backup_include_volumes,
            "bucket": settings.backup_s3_bucket,
            "prefix": settings.backup_s3_prefix,
        },
    }


@router.get("/list")
@apply_tiered_limit("standard")
async def backup_list(
    request: Request,
    tier: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    api_key: str = Depends(require_admin_key),
):
    _require_enabled()
    storage = await run_in_threadpool(S3Storage)
    ids = await run_in_threadpool(storage.list_backups, tier)
    return {"tier": tier, "count": len(ids), "backup_ids": ids}


@router.get("/manifest/{backup_id}")
@apply_tiered_limit("standard")
async def backup_manifest(
    request: Request,
    backup_id: str,
    api_key: str = Depends(require_admin_key),
):
    _require_enabled()
    runner = await run_in_threadpool(RestoreRunner)
    manifest = await run_in_threadpool(runner.load_manifest, backup_id)
    # asdict via to_json() round-trip for clean JSON serialization
    import json as _json
    return _json.loads(manifest.to_json())


# ── actions ───────────────────────────────────────────────────────────────
@router.post("/run")
@apply_tiered_limit("standard")
async def backup_run(
    request: Request,
    api_key: str = Depends(require_admin_key),
):
    _require_enabled()
    sched = get_scheduler()
    if sched._manual_in_progress:
        raise HTTPException(status_code=409, detail="A backup is already in progress")
    sched.trigger_now()
    return {"status": "triggered", "message": "Backup scheduled to start immediately"}


@router.post("/verify/{backup_id}")
@apply_tiered_limit("standard")
async def backup_verify(
    request: Request,
    backup_id: str,
    api_key: str = Depends(require_admin_key),
):
    _require_enabled()
    runner = await run_in_threadpool(RestoreRunner)
    report = await run_in_threadpool(runner.verify, backup_id)
    return {
        "backup_id": report.backup_id,
        "tier": report.tier,
        "verified": report.verified,
    }


@router.post("/restore/{backup_id}")
@apply_tiered_limit("standard")
async def backup_restore(
    request: Request,
    backup_id: str,
    payload: RestoreRequest,
    confirm: bool = Query(False),
    api_key: str = Depends(require_admin_key),
):
    _require_enabled()
    is_drill = bool(payload.mongo_target_db)
    if not is_drill and not (confirm and payload.i_understand):
        raise HTTPException(
            status_code=400,
            detail=(
                "Destructive restore requires ?confirm=true AND body field i_understand=true. "
                "For a safe drill, pass mongo_target_db=<throwaway_db>."
            ),
        )

    runner = await run_in_threadpool(RestoreRunner)

    def _do_restore():
        return runner.restore(
            backup_id,
            components=payload.components,
            mongo_target_db=payload.mongo_target_db,
            mongo_drop=payload.mongo_drop,
        )

    report = await run_in_threadpool(_do_restore)
    return {
        "backup_id": report.backup_id,
        "tier": report.tier,
        "restored": report.restored,
        "skipped": report.skipped,
        "mongo_target_db": payload.mongo_target_db,
    }
