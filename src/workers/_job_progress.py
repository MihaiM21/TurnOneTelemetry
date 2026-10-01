"""Shared progress plumbing for durable admin jobs.

Every long-running admin job (plot backfill, dataset export, archive build)
tracks state both in memory and in MongoDB via ``src/repositories/admin_jobs``.
This module owns the three pieces they all need, so no worker keeps a private
copy:

* :class:`JobWriter` — throttled write-through of a job's progress fields.
  A job of thousands of units must not cost a Mongo write per unit, so state is
  flushed at most every ``FLUSH_INTERVAL_SECONDS`` or ``FLUSH_EVERY_UNITS``
  completions, plus unconditionally on every status transition.
  Cancellation is polled on the same cadence, which bounds how long a cancel
  takes to take effect.
* :data:`CANCELLED` — in-process cancel signal so a cancel served by the owning
  worker takes effect immediately rather than after the Mongo round-trip.
* :func:`bump_metric` — best-effort Prometheus counters.

The job object is duck-typed: it must expose ``job_id``, ``errors`` (a list)
and every attribute named in :data:`BASE_FIELDS` plus any ``extra_fields`` the
worker registers.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, List, Sequence, Set

from src.repositories import admin_jobs

FLUSH_INTERVAL_SECONDS = 2.0
FLUSH_EVERY_UNITS = 25

# Progress fields every job kind shares; workers add their own via ``extra_fields``.
BASE_FIELDS: Sequence[str] = (
    "status",
    "total",
    "done",
    "success",
    "failed",
    "skipped",
    "current",
    "warnings",
    "started_at",
    "finished_at",
)

CANCELLED: Set[str] = set()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobWriter:
    """Throttled write-through of job progress to MongoDB."""

    def __init__(self, job: Any, *, extra_fields: Sequence[str] = ()) -> None:
        self.job = job
        self.fields: Sequence[str] = tuple(BASE_FIELDS) + tuple(extra_fields)
        self._last_flush = 0.0
        self._since_flush = 0
        self._pending_errors: List[str] = []
        self._lock = threading.Lock()

    def note_error(self, message: str) -> None:
        with self._lock:
            self.job.errors.append(message)
            self._pending_errors.append(message)

    def tick(self, force: bool = False) -> None:
        with self._lock:
            self._since_flush += 1
            due = (
                force
                or self._since_flush >= FLUSH_EVERY_UNITS
                or (time.monotonic() - self._last_flush) >= FLUSH_INTERVAL_SECONDS
            )
            if not due:
                return
            errors, self._pending_errors = self._pending_errors, []
            self._since_flush = 0
            self._last_flush = time.monotonic()
            job = self.job
            payload = {name: getattr(job, name, None) for name in self.fields}

        admin_jobs.update_job(job.job_id, **payload)
        if errors:
            admin_jobs.push_errors(job.job_id, errors)

    def cancelled(self) -> bool:
        if self.job.job_id in CANCELLED:
            return True
        return admin_jobs.is_cancelled(self.job.job_id)


def bump_metric(success: bool) -> None:
    try:
        from src.core.observability.monitoring import (
            BACKGROUND_JOBS_FAILED,
            BACKGROUND_JOBS_PROCESSED,
        )
        (BACKGROUND_JOBS_PROCESSED if success else BACKGROUND_JOBS_FAILED).inc()
    except Exception:  # pragma: no cover - metrics are best-effort
        pass
