"""Durable storage for admin background jobs (plot backfill and friends).

Job state used to live in a process-local dict inside
``src/workers/plot_inventory.py``. That has two failure modes an operator hits
in practice:

* a server restart mid-backfill loses every trace of what ran, and
* under a multi-worker deploy the status poll round-robins to a process that
  never saw the job, so the progress bar 404s at random.

Persisting jobs here fixes both, and gives the admin UI a real job history plus
a cancel channel (the worker polls ``cancel_requested``).

**Write volume matters.** A full-catalog backfill is thousands of units; writing
once per unit would put more load on Mongo than the generation itself. Callers
keep authoritative state in memory and flush here on a throttle — see
``plot_inventory._JobWriter``. Every function fails open: losing a progress
write must never abort a running job.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pymongo import DESCENDING
from pymongo.errors import PyMongoError

from src.core.logging import get_logger
from src.repositories.mongo import get_mongo_client

logger = get_logger(__name__)

COLLECTION = "admin_jobs"

# Bound the stored error list. Operators need a sample plus the tail, not a
# 5,000-entry array that pushes the document toward the 16 MB limit.
MAX_STORED_ERRORS = 200

# A running job whose worker stopped heart-beating this long ago is reported as
# stale rather than running — the process almost certainly died.
STALE_AFTER_SECONDS = 300

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_STALE = "stale"

TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED)


def _collection():
    db_name = os.getenv("MONGODB_DATABASE", "T1API_DB")
    return get_mongo_client()[db_name][COLLECTION]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _worker_id() -> str:
    """Identify the process that owns a job, so a stale job is traceable."""
    try:
        import socket

        return f"{socket.gethostname()}:{os.getpid()}"
    except Exception:  # pragma: no cover - defensive
        return f"pid:{os.getpid()}"


def create_job(job_id: str, *, kind: str, scope: Dict[str, Any],
               selection: Optional[Dict[str, Any]] = None,
               total: int = 0) -> bool:
    """Insert a queued job. Returns False if the write failed (job still runs)."""
    doc = {
        "_id": job_id,
        "kind": kind,
        "scope": scope,
        "selection": selection or {},
        "status": STATUS_QUEUED,
        "total": total,
        "done": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "current": None,
        "per_feature": {},
        "errors": [],
        "cancel_requested": False,
        "worker": _worker_id(),
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "heartbeat_at": _now(),
    }
    try:
        _collection().insert_one(doc)
        return True
    except PyMongoError as exc:
        logger.warning("admin_jobs: could not create job %s: %s", job_id, exc)
        return False


def update_job(job_id: str, **fields: Any) -> bool:
    """Overwrite fields and refresh the heartbeat."""
    if not fields:
        return False
    fields["heartbeat_at"] = _now()
    try:
        _collection().update_one({"_id": job_id}, {"$set": fields})
        return True
    except PyMongoError as exc:
        logger.warning("admin_jobs: could not update job %s: %s", job_id, exc)
        return False


def push_errors(job_id: str, messages: List[str]) -> bool:
    """Append errors, keeping only the most recent ``MAX_STORED_ERRORS``."""
    if not messages:
        return False
    try:
        _collection().update_one(
            {"_id": job_id},
            {
                "$push": {"errors": {"$each": list(messages), "$slice": -MAX_STORED_ERRORS}},
                "$set": {"heartbeat_at": _now()},
            },
        )
        return True
    except PyMongoError as exc:
        logger.warning("admin_jobs: could not push errors for %s: %s", job_id, exc)
        return False


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Fetch one job, with ``status`` adjusted to ``stale`` when appropriate."""
    try:
        doc = _collection().find_one({"_id": job_id})
    except PyMongoError as exc:
        logger.warning("admin_jobs: could not read job %s: %s", job_id, exc)
        return None
    return _serialize(doc) if doc else None


def list_jobs(limit: int = 50, status: Optional[str] = None,
              kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """Newest-first job history."""
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    if kind:
        query["kind"] = kind
    try:
        cursor = (
            _collection()
            .find(query)
            .sort("created_at", DESCENDING)
            .limit(max(1, min(int(limit), 500)))
        )
        return [_serialize(doc) for doc in cursor]
    except PyMongoError as exc:
        logger.warning("admin_jobs: could not list jobs: %s", exc)
        return []


def request_cancel(job_id: str) -> bool:
    """Flag a job for cancellation.

    Only meaningful for a job that has not finished — the worker notices on its
    next progress flush. Returns True when a non-terminal job was flagged.
    """
    try:
        result = _collection().update_one(
            {"_id": job_id, "status": {"$nin": list(TERMINAL_STATUSES)}},
            {"$set": {"cancel_requested": True, "heartbeat_at": _now()}},
        )
        return result.matched_count > 0
    except PyMongoError as exc:
        logger.warning("admin_jobs: could not cancel job %s: %s", job_id, exc)
        return False


def is_cancelled(job_id: str) -> bool:
    """Whether cancellation has been requested. Fails open to False so a Mongo
    blip cannot spuriously abort a long-running backfill.
    """
    try:
        doc = _collection().find_one({"_id": job_id}, {"cancel_requested": 1})
        return bool(doc and doc.get("cancel_requested"))
    except PyMongoError as exc:
        logger.debug("admin_jobs: cancel check failed for %s: %s", job_id, exc)
        return False


def delete_job(job_id: str) -> bool:
    try:
        return _collection().delete_one({"_id": job_id}).deleted_count > 0
    except PyMongoError as exc:
        logger.warning("admin_jobs: could not delete job %s: %s", job_id, exc)
        return False


def running_jobs(kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """Jobs still believed to be in flight, excluding stale ones.

    Used to reject a second backfill over an overlapping scope.
    """
    query: Dict[str, Any] = {"status": {"$in": [STATUS_QUEUED, STATUS_RUNNING]}}
    if kind:
        query["kind"] = kind
    try:
        docs = [_serialize(d) for d in _collection().find(query)]
    except PyMongoError as exc:
        logger.warning("admin_jobs: could not list running jobs: %s", exc)
        return []
    return [d for d in docs if d["status"] != STATUS_STALE]


def _is_stale(doc: Dict[str, Any]) -> bool:
    if doc.get("status") not in (STATUS_QUEUED, STATUS_RUNNING):
        return False
    beat = doc.get("heartbeat_at")
    if not isinstance(beat, datetime):
        return False
    if beat.tzinfo is None:
        beat = beat.replace(tzinfo=timezone.utc)
    return beat < _now() - timedelta(seconds=STALE_AFTER_SECONDS)


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-safe view. ``_id`` is exposed as ``job_id`` and datetimes as ISO
    strings so the admin UI and API can return the document directly.
    """
    out = dict(doc)
    out["job_id"] = out.pop("_id")
    if _is_stale(doc):
        out["status"] = STATUS_STALE
    for key in ("created_at", "started_at", "finished_at", "heartbeat_at"):
        value = out.get(key)
        if isinstance(value, datetime):
            out[key] = value.isoformat()
    return out
