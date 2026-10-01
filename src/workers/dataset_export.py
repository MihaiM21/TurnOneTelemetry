"""Admin dataset-export jobs: plan, estimate, run and archive.

Mirrors ``plot_inventory``'s job model — durable in ``admin_jobs`` under its
own ``kind`` so the two histories never mix, a hot in-memory cache for the
owning process, throttled progress via ``JobWriter`` and cooperative
cancellation. Concurrency is **across** sessions only; each session's tiers run
serially on one thread because the parsed streams are per-store.

Only one export job may run at a time: two jobs writing the same export id
would race on the manifest, and two writing different ids would double the
memory ceiling the concurrency cap exists to enforce.
"""

from __future__ import annotations

import shutil
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.core.config import settings
from src.core.logging import get_logger
from src.repositories import admin_jobs
from src.repositories.raw_stream_cache import list_raw_stream_sessions
from src.services.dataset_export import exports, manifest as manifest_mod
from src.services.dataset_export.manifest import Manifest, session_key
from src.services.dataset_export.schema import TIERS, Target
from src.services.dataset_export.session_export import export_session
from src.workers._job_progress import CANCELLED, JobWriter, bump_metric, now
from src.workers.plot_inventory import _enumerate_targets

logger = get_logger(__name__)

JOB_KIND = "dataset_export"
ARCHIVE_JOB_KIND = "dataset_export_archive"

# Tiers whose decoded streams are large enough to cap concurrency.
_HEAVY_TIERS = {"telemetry", "raw"}

# Rough per-session output sizes (bytes) used by the estimate only. Calibrated on
# a 2024 race weekend (zstd Parquet + gzip): a race is ~12 MB across all tiers.
EST_BYTES: Dict[str, Dict[str, float]] = {
    "tables": {"default": 1.5e5},
    "corpus": {"default": 5e4},
    "telemetry": {"R": 5e6, "S": 2.5e6, "Q": 3e6, "SQ": 2e6, "default": 3e6},
    "raw": {"R": 8e6, "S": 4e6, "Q": 4e6, "SQ": 3e6, "default": 4e6},
}
EST_SECONDS = {"cached": 20.0, "cold": 60.0}


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ExportUnit:
    year: int
    round_nr: int
    gp_name: str
    session: str
    tiers: tuple

    @property
    def target(self) -> Target:
        return (self.year, self.round_nr, self.gp_name, self.session)

    @property
    def session_key(self) -> str:
        return session_key(self.year, self.round_nr, self.session)

    @property
    def label(self) -> str:
        return f"{self.year} R{self.round_nr:02d} {self.gp_name} {self.session}"


@dataclass
class ExportPlan:
    export_id: str
    tiers: List[str]
    units: List[ExportUnit] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    resume: bool = True
    already_done: int = 0
    scope: Dict[str, Any] = field(default_factory=dict)


def _normalize_tiers(tiers: Optional[Sequence[str]]) -> List[str]:
    wanted = [t for t in (tiers or ["tables", "corpus"]) if t in TIERS]
    if not wanted:
        raise ValueError(f"no valid tiers in {tiers!r}; choose from {TIERS}")
    return [t for t in TIERS if t in wanted]


def default_export_id(job_id: str) -> str:
    return f"export_{datetime.now(timezone.utc):%Y%m%d}_{job_id}"


def build_export_plan(
    *,
    years: Sequence[int],
    identifier: Optional[Any] = None,
    session: Optional[str] = None,
    session_types: Optional[Sequence[str]] = None,
    tiers: Optional[Sequence[str]] = None,
    export_id: str,
    resume: bool = True,
) -> ExportPlan:
    """Expand years × schedule × tiers into units, dropping what is already done."""
    years = sorted({int(y) for y in years})
    if not years:
        raise ValueError("at least one year is required")
    if len(years) > settings.export_max_years:
        raise ValueError(f"at most {settings.export_max_years} years per export")
    tier_list = _normalize_tiers(tiers)
    exports.validate_export_id(export_id)
    type_filter = {t.strip().upper() for t in session_types} if session_types else None

    existing: Optional[Manifest] = None
    if resume:
        path = exports.export_dir(export_id)
        existing = manifest_mod.load(path) if path.is_dir() else None
        if existing is not None:
            manifest_mod.check_resumable(existing)

    plan = ExportPlan(
        export_id=export_id, tiers=tier_list, resume=resume,
        scope={"years": years, "gp": identifier, "session": session,
               "session_types": sorted(type_filter) if type_filter else None},
    )
    for year in years:
        targets = _enumerate_targets(year=year, identifier=identifier, session=session)
        if not targets:
            plan.warnings.append(f"{year}: no sessions in schedule")
        for target in targets:
            _yr, round_nr, gp_name, abbrev = target
            if type_filter and abbrev not in type_filter:
                continue
            key = session_key(year, round_nr, abbrev)
            pending = tuple(t for t in tier_list if not (existing and existing.is_done(key, t)))
            if not pending:
                plan.already_done += 1
                continue
            plan.units.append(ExportUnit(year, round_nr, gp_name, abbrev, pending))
    return plan


def estimate_export_plan(plan: ExportPlan) -> Dict[str, Any]:
    """Cost a plan without running it: unit counts, bytes, seconds, free disk."""
    by_tier: Dict[str, int] = {t: 0 for t in plan.tiers}
    est_bytes = 0.0
    for unit in plan.units:
        for tier in unit.tiers:
            by_tier[tier] = by_tier.get(tier, 0) + 1
            sizes = EST_BYTES.get(tier, {})
            est_bytes += sizes.get(unit.session, sizes.get("default", 0.0))

    cached_keys = set()
    for year in {u.year for u in plan.units}:
        for entry in list_raw_stream_sessions(year):
            cached_keys.add(entry.get("session_key"))
    cached = sum(1 for u in plan.units if f"{u.year}_{u.round_nr}_{u.session}" in cached_keys)
    cold = len(plan.units) - cached
    est_seconds = cached * EST_SECONDS["cached"] + cold * EST_SECONDS["cold"]

    try:
        free_bytes = shutil.disk_usage(exports.export_root()).free
    except OSError:
        free_bytes = None

    warnings = list(plan.warnings)
    if free_bytes is not None and est_bytes > free_bytes * 0.9:
        warnings.append("estimated size exceeds 90% of free disk space")
    if free_bytes is not None and free_bytes < settings.export_min_free_gb * 1e9:
        warnings.append(f"less than {settings.export_min_free_gb} GB free on the export volume")
    if _HEAVY_TIERS & set(plan.tiers):
        warnings.append("telemetry/raw tiers decode hundreds of MB per session; concurrency is capped")

    return {
        "export_id": plan.export_id,
        "tiers": plan.tiers,
        "sessions": len(plan.units),
        "already_done": plan.already_done,
        "units_by_tier": by_tier,
        "cached_sessions": cached,
        "cold_sessions": cold,
        "est_bytes": int(est_bytes),
        "est_seconds": int(est_seconds),
        "free_bytes": free_bytes,
        "warnings": warnings,
        "scope": plan.scope,
    }


# --------------------------------------------------------------------------- #
# Job tracking
# --------------------------------------------------------------------------- #
@dataclass
class ExportJob:
    job_id: str
    kind: str
    export_id: str
    scope: Dict[str, Any]
    status: str = "queued"
    total: int = 0
    done: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    current: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    per_tier: Dict[str, Dict[str, int]] = field(default_factory=dict)
    bytes_written: int = 0
    tiers: List[str] = field(default_factory=list)

    def record(self, tier: str, outcome: str) -> None:
        bucket = self.per_tier.setdefault(tier, {"done": 0, "skipped": 0, "failed": 0, "absent": 0, "cancelled": 0})
        bucket[outcome] = bucket.get(outcome, 0) + 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "export_id": self.export_id,
            "scope": self.scope,
            "tiers": self.tiers,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "success": self.success,
            "failed": self.failed,
            "skipped": self.skipped,
            "current": self.current,
            "per_tier": self.per_tier,
            "bytes_written": self.bytes_written,
            "errors": self.errors[-25:],
            "warnings": self.warnings[-50:],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_JOBS: Dict[str, ExportJob] = {}
_JOBS_LOCK = threading.Lock()
_MAX_JOBS = 50
_TERMINAL = ("completed", "failed", "cancelled")


def _register_job(job: ExportJob) -> None:
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job
        if len(_JOBS) > _MAX_JOBS:
            for jid in sorted(_JOBS, key=lambda k: _JOBS[k].started_at or ""):
                if _JOBS[jid].status in _TERMINAL:
                    del _JOBS[jid]
                    if len(_JOBS) <= _MAX_JOBS:
                        break


def get_job(job_id: str) -> Optional[ExportJob]:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def get_job_dict(job_id: str) -> Optional[Dict[str, Any]]:
    job = get_job(job_id)
    if job is not None:
        return job.as_dict()
    doc = admin_jobs.get_job(job_id)
    if doc is not None and doc.get("kind") not in (JOB_KIND, ARCHIVE_JOB_KIND):
        return None
    return doc


def list_jobs(limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
    stored = admin_jobs.list_jobs(limit=limit, status=status, kind=JOB_KIND)
    stored += admin_jobs.list_jobs(limit=limit, status=status, kind=ARCHIVE_JOB_KIND)
    with _JOBS_LOCK:
        live = {jid: job.as_dict() for jid, job in _JOBS.items()}
    merged: List[Dict[str, Any]] = []
    for doc in stored:
        current = live.pop(doc["job_id"], None)
        merged.append({**doc, **current} if current else doc)
    for extra in live.values():
        if status is None or extra.get("status") == status:
            merged.append(extra)
    merged.sort(key=lambda d: d.get("created_at") or d.get("started_at") or "", reverse=True)
    return merged[:limit]


def cancel_job(job_id: str) -> bool:
    flagged = admin_jobs.request_cancel(job_id)
    job = get_job(job_id)
    if job is not None and job.status in ("queued", "running"):
        CANCELLED.add(job_id)
        return True
    return flagged


def running_jobs() -> List[Dict[str, Any]]:
    """Live export/archive jobs, from both the process cache and Mongo."""
    docs = admin_jobs.running_jobs(kind=JOB_KIND) + admin_jobs.running_jobs(kind=ARCHIVE_JOB_KIND)
    seen = {d["job_id"] for d in docs}
    with _JOBS_LOCK:
        for jid, job in _JOBS.items():
            if job.status in ("queued", "running") and jid not in seen:
                docs.append(job.as_dict())
    return docs


def export_in_use(export_id: str) -> bool:
    return any(d.get("export_id") == export_id or (d.get("scope") or {}).get("export_id") == export_id
               for d in running_jobs())


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def _concurrency_cap(tiers: Sequence[str], requested: int) -> int:
    cap = settings.export_max_concurrency if _HEAVY_TIERS & set(tiers) else 4
    return max(1, min(requested, cap))


def execute_export(job: ExportJob, plan: ExportPlan, *, concurrency: int = 1) -> None:
    """Run every unit of ``plan``, persisting the manifest after each session."""
    job.status = "running"
    job.started_at = job.started_at or now()
    job.total = len(plan.units)
    job.warnings = list(plan.warnings)
    job.tiers = list(plan.tiers)
    _register_job(job)

    writer = JobWriter(job, extra_fields=("per_tier", "bytes_written", "export_id", "tiers"))
    writer.tick(force=True)

    root: Path = exports.export_dir(plan.export_id)
    root.mkdir(parents=True, exist_ok=True)
    manifest = manifest_mod.load(root) if plan.resume else None
    if manifest is None:
        manifest = manifest_mod.new_manifest(plan.export_id, plan.scope, plan.tiers)
    else:
        manifest.tiers_requested = sorted(set(manifest.tiers_requested) | set(plan.tiers), key=TIERS.index)
    manifest_lock = threading.Lock()
    progress_lock = threading.Lock()

    def _save() -> None:
        with manifest_lock:
            manifest_mod.save(manifest, root)

    _save()

    def _run_unit(unit: ExportUnit) -> None:
        with progress_lock:
            job.current = unit.label
        try:
            with manifest_lock:
                snapshot_done = {t: manifest.is_done(unit.session_key, t) for t in unit.tiers}
            outcome = export_session(
                unit.target, root, [t for t in unit.tiers if not snapshot_done[t]],
                manifest, cancelled=writer.cancelled, resume=plan.resume,
            )
            _save()
            with progress_lock:
                job.done += 1
                job.bytes_written += outcome.bytes_written
                job.warnings.extend(outcome.warnings)
                for tier, status in outcome.per_tier.items():
                    job.record(tier, status)
                if outcome.failed:
                    job.failed += 1
                    writer.note_error(f"{unit.label}: " + "; ".join(
                        f"{t}={s}" for t, s in outcome.per_tier.items() if s == "failed"))
                elif all(s == "skipped" for s in outcome.per_tier.values()):
                    job.skipped += 1
                else:
                    job.success += 1
            bump_metric(not outcome.failed)
        except Exception as exc:  # noqa: BLE001 - keep the job alive
            logger.exception("Export unit %s failed", unit.label)
            with progress_lock:
                job.done += 1
                job.failed += 1
            writer.note_error(f"{unit.label}: {exc}")
            bump_metric(False)
        finally:
            writer.tick()

    groups: "OrderedDict[str, List[ExportUnit]]" = OrderedDict()
    for unit in plan.units:
        groups.setdefault(unit.session_key, []).append(unit)

    def _run_group(units: List[ExportUnit]) -> None:
        for unit in units:
            if writer.cancelled():
                return
            _run_unit(unit)

    workers = _concurrency_cap(plan.tiers, concurrency)
    try:
        if workers == 1 or len(groups) <= 1:
            for units in groups.values():
                if writer.cancelled():
                    break
                _run_group(units)
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="export") as pool:
                list(pool.map(_run_group, list(groups.values())))
        if writer.cancelled():
            job.status = "cancelled"
        elif job.failed and job.failed == job.total:
            job.status = "failed"
        else:
            job.status = "completed"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Export job %s crashed", job.job_id)
        job.status = "failed"
        writer.note_error(str(exc))
    finally:
        job.current = None
        job.finished_at = now()
        _save()
        CANCELLED.discard(job.job_id)
        writer.tick(force=True)


def start_export_job(
    *,
    years: Sequence[int],
    identifier: Optional[Any] = None,
    session: Optional[str] = None,
    session_types: Optional[Sequence[str]] = None,
    tiers: Optional[Sequence[str]] = None,
    export_id: Optional[str] = None,
    resume: bool = True,
    concurrency: Optional[int] = None,
) -> ExportJob:
    """Plan and launch an export on a daemon thread.

    Raises ``RuntimeError`` when another export/archive job is running and
    ``ValueError`` for an unusable scope; the router maps those to 409 / 400.
    """
    if running_jobs():
        raise RuntimeError("an export job is already running")
    job_id = uuid.uuid4().hex[:12]
    export_id = export_id or default_export_id(job_id)
    plan = build_export_plan(
        years=years, identifier=identifier, session=session, session_types=session_types,
        tiers=tiers, export_id=export_id, resume=resume,
    )
    workers = _concurrency_cap(plan.tiers, concurrency or settings.export_concurrency)
    scope = {**plan.scope, "export_id": export_id, "tiers": plan.tiers, "resume": resume,
             "concurrency": workers}
    job = ExportJob(job_id=job_id, kind=JOB_KIND, export_id=export_id, scope=scope,
                    total=len(plan.units), started_at=now(), tiers=plan.tiers,
                    warnings=list(plan.warnings))
    _register_job(job)
    admin_jobs.create_job(job_id, kind=JOB_KIND, scope=scope, selection={"tiers": plan.tiers},
                          total=len(plan.units))

    thread = threading.Thread(
        target=execute_export, args=(job, plan), kwargs={"concurrency": workers},
        name=f"dataset-export-{job_id}", daemon=True,
    )
    thread.start()
    return job


def _run_archive(job: ExportJob) -> None:
    job.status = "running"
    job.started_at = job.started_at or now()
    job.total = 1
    _register_job(job)
    writer = JobWriter(job, extra_fields=("export_id",))
    writer.tick(force=True)
    try:
        path = exports.build_archive(job.export_id, cancelled=writer.cancelled)
        job.bytes_written = path.stat().st_size
        job.done = job.success = 1
        job.status = "completed"
    except exports.ExportBusy:
        job.status = "cancelled"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Archive build for %s failed", job.export_id)
        job.done = job.failed = 1
        job.status = "failed"
        writer.note_error(str(exc))
    finally:
        job.finished_at = now()
        CANCELLED.discard(job.job_id)
        writer.tick(force=True)


def start_archive_job(export_id: str) -> ExportJob:
    """Zip a finished export on a daemon thread; 409-style errors as ``RuntimeError``."""
    exports.load_manifest(export_id)
    if export_in_use(export_id):
        raise RuntimeError(f"export {export_id} has a running job")
    if exports.archive_status(export_id)["status"] == "building":
        raise RuntimeError(f"archive for {export_id} is already being built")
    job_id = uuid.uuid4().hex[:12]
    scope = {"export_id": export_id}
    job = ExportJob(job_id=job_id, kind=ARCHIVE_JOB_KIND, export_id=export_id, scope=scope,
                    total=1, started_at=now())
    _register_job(job)
    admin_jobs.create_job(job_id, kind=ARCHIVE_JOB_KIND, scope=scope, total=1)
    threading.Thread(target=_run_archive, args=(job,), name=f"dataset-archive-{job_id}", daemon=True).start()
    return job
