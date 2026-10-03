"""Dataset export worker: planning, resume, execution, cancel, job gating."""

from __future__ import annotations

import json
import threading
import time

import pytest

from src.core.config import settings
from src.repositories import admin_jobs
from src.services.dataset_export import exports, manifest as manifest_mod
from src.services.dataset_export.manifest import TierEntry
from src.services.dataset_export.session_export import SessionOutcome
from src.workers import dataset_export as de

TARGETS_2024 = [
    (2024, 1, "Bahrain Grand Prix", "FP1"),
    (2024, 1, "Bahrain Grand Prix", "Q"),
    (2024, 1, "Bahrain Grand Prix", "R"),
    (2024, 2, "Saudi Arabian Grand Prix", "R"),
]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "export_dir", str(tmp_path / "exports"))
    monkeypatch.setattr(settings, "export_max_years", 3)
    monkeypatch.setattr(de, "_enumerate_targets",
                        lambda *, year, identifier, session: [t for t in TARGETS_2024 if t[0] == year])
    monkeypatch.setattr(de, "list_raw_stream_sessions", lambda year=None: [{"session_key": "2024_1_R"}])
    admin_jobs._collection().delete_many({})
    with de._JOBS_LOCK:
        de._JOBS.clear()
    de.CANCELLED.clear()
    yield
    with de._JOBS_LOCK:
        de._JOBS.clear()


def _fake_export_session(calls, *, fail_keys=(), per_tier_status="done", delay=0.0):
    def fake(target, root, tiers, manifest, *, cancelled=None, resume=True):
        year, round_nr, gp, session = target
        key = manifest_mod.session_key(year, round_nr, session)
        calls.append((target, list(tiers)))
        if delay:
            time.sleep(delay)
        outcome = SessionOutcome(session_key=key)
        for tier in tiers:
            status = "failed" if key in fail_keys else per_tier_status
            manifest.mark(key, tier, TierEntry(status=status, rows=1, bytes=10), year=year, round=round_nr,
                          gp_name=gp, session=session)
            outcome.per_tier[tier] = status
            outcome.bytes_written += 10
        return outcome
    return fake


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #
def test_plan_expands_years_and_filters_session_types():
    plan = de.build_export_plan(years=[2024], session_types=["R", "q"], tiers=["tables", "telemetry"],
                                export_id="exp1")
    assert [(u.round_nr, u.session) for u in plan.units] == [(1, "Q"), (1, "R"), (2, "R")]
    assert all(u.tiers == ("tables", "telemetry") for u in plan.units)
    assert plan.scope["session_types"] == ["Q", "R"]


def test_plan_tiers_are_canonically_ordered_and_validated():
    plan = de.build_export_plan(years=[2024], tiers=["corpus", "tables"], export_id="exp1")
    assert plan.tiers == ["tables", "corpus"]
    with pytest.raises(ValueError):
        de.build_export_plan(years=[2024], tiers=["bogus"], export_id="exp1")
    with pytest.raises(ValueError):
        de.build_export_plan(years=[2020, 2021, 2022, 2023], export_id="exp1")
    with pytest.raises(exports.ExportNotFound):
        de.build_export_plan(years=[2024], export_id="../evil")


def test_plan_resume_skips_done_tiers_only():
    root = exports.export_dir("exp1")
    root.mkdir(parents=True)
    m = manifest_mod.new_manifest("exp1", {}, ["tables"])
    m.mark("2024_01_R", "tables", TierEntry(status="done"), year=2024, round=1, gp_name="B", session="R")
    m.mark("2024_01_Q", "tables", TierEntry(status="failed"), year=2024, round=1, gp_name="B", session="Q")
    manifest_mod.save(m, root)

    plan = de.build_export_plan(years=[2024], tiers=["tables", "corpus"], export_id="exp1", resume=True)
    by_key = {u.session_key: u.tiers for u in plan.units}
    assert by_key["2024_01_R"] == ("corpus",)          # tables done -> only corpus left
    assert by_key["2024_01_Q"] == ("tables", "corpus")  # failed -> redone
    assert plan.already_done == 0

    full = de.build_export_plan(years=[2024], tiers=["tables"], export_id="exp1", resume=True)
    assert "2024_01_R" not in {u.session_key for u in full.units}
    assert full.already_done == 1

    no_resume = de.build_export_plan(years=[2024], tiers=["tables"], export_id="exp1", resume=False)
    assert "2024_01_R" in {u.session_key for u in no_resume.units}


def test_plan_refuses_schema_mismatch():
    root = exports.export_dir("exp1")
    root.mkdir(parents=True)
    m = manifest_mod.new_manifest("exp1", {}, ["tables"])
    m.schema_version = "0.0.1"
    manifest_mod.save(m, root)
    with pytest.raises(manifest_mod.SchemaMismatch):
        de.build_export_plan(years=[2024], export_id="exp1", resume=True)


def test_estimate_counts_cached_sessions_and_bytes():
    plan = de.build_export_plan(years=[2024], tiers=["tables", "telemetry"], export_id="exp1")
    est = de.estimate_export_plan(plan)
    assert est["sessions"] == 4
    assert est["cached_sessions"] == 1 and est["cold_sessions"] == 3
    assert est["units_by_tier"] == {"tables": 4, "telemetry": 4}
    assert est["est_bytes"] > 4 * 2e6
    assert any("concurrency is capped" in w for w in est["warnings"])
    json.dumps(est)


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def test_execute_records_outcomes_and_persists_manifest(monkeypatch):
    calls = []
    monkeypatch.setattr(de, "export_session", _fake_export_session(calls, fail_keys={"2024_01_Q"}))
    plan = de.build_export_plan(years=[2024], tiers=["tables"], export_id="exp1")
    job = de.ExportJob(job_id="j1", kind=de.JOB_KIND, export_id="exp1", scope={})
    admin_jobs.create_job("j1", kind=de.JOB_KIND, scope={}, total=4)

    de.execute_export(job, plan, concurrency=1)

    assert job.status == "completed"
    assert (job.done, job.success, job.failed, job.skipped) == (4, 3, 1, 0)
    assert job.per_tier["tables"]["done"] == 3 and job.per_tier["tables"]["failed"] == 1
    assert job.bytes_written == 40
    assert job.errors and "2024 R01" in job.errors[0]
    # sessions were passed as abbreviations, in schedule order
    assert [c[0][3] for c in calls] == ["FP1", "Q", "R", "R"]

    saved = manifest_mod.load(exports.export_dir("exp1"))
    assert saved.is_done("2024_01_R", "tables") and not saved.is_done("2024_01_Q", "tables")
    stored = admin_jobs.get_job("j1")
    assert stored["status"] == "completed" and stored["per_tier"]["tables"]["failed"] == 1
    assert stored["export_id"] == "exp1"


def test_execute_all_skipped_counts_skipped(monkeypatch):
    monkeypatch.setattr(de, "export_session", _fake_export_session([], per_tier_status="skipped"))
    plan = de.build_export_plan(years=[2024], tiers=["tables"], export_id="exp1")
    job = de.ExportJob(job_id="j2", kind=de.JOB_KIND, export_id="exp1", scope={})
    de.execute_export(job, plan)
    assert job.skipped == 4 and job.success == 0 and job.status == "completed"


def test_execute_cancel_stops_between_sessions(monkeypatch):
    calls = []

    def fake(target, root, tiers, manifest, *, cancelled=None, resume=True):
        out = _fake_export_session(calls)(target, root, tiers, manifest, cancelled=cancelled, resume=resume)
        de.CANCELLED.add("j3")  # cancel after the first session
        return out

    monkeypatch.setattr(de, "export_session", fake)
    plan = de.build_export_plan(years=[2024], tiers=["tables"], export_id="exp1")
    job = de.ExportJob(job_id="j3", kind=de.JOB_KIND, export_id="exp1", scope={})
    de.execute_export(job, plan)
    assert job.status == "cancelled"
    assert len(calls) == 1
    assert "j3" not in de.CANCELLED


def test_execute_concurrency_is_capped_for_heavy_tiers(monkeypatch):
    seen = []
    lock = threading.Lock()

    def fake(target, root, tiers, manifest, *, cancelled=None, resume=True):
        with lock:
            seen.append(threading.current_thread().name)
        return _fake_export_session([])(target, root, tiers, manifest)

    monkeypatch.setattr(de, "export_session", fake)
    monkeypatch.setattr(settings, "export_max_concurrency", 2)
    plan = de.build_export_plan(years=[2024], tiers=["telemetry"], export_id="exp1")
    job = de.ExportJob(job_id="j4", kind=de.JOB_KIND, export_id="exp1", scope={})
    de.execute_export(job, plan, concurrency=8)
    assert job.status == "completed" and job.done == 4
    assert all(name.startswith("export") for name in seen)
    assert de._concurrency_cap(["telemetry"], 8) == 2
    assert de._concurrency_cap(["tables"], 8) == 4


def test_unit_exception_is_recorded_not_fatal(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(de, "export_session", boom)
    plan = de.build_export_plan(years=[2024], tiers=["tables"], export_id="exp1")
    job = de.ExportJob(job_id="j5", kind=de.JOB_KIND, export_id="exp1", scope={})
    de.execute_export(job, plan)
    assert job.status == "failed" and job.failed == 4
    assert all("kaboom" in e for e in job.errors)


# --------------------------------------------------------------------------- #
# Job gating / listing
# --------------------------------------------------------------------------- #
def test_start_job_refuses_while_one_runs(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow(target, root, tiers, manifest, *, cancelled=None, resume=True):
        started.set()
        release.wait(5)
        return _fake_export_session([])(target, root, tiers, manifest)

    monkeypatch.setattr(de, "export_session", slow)
    job = de.start_export_job(years=[2024], tiers=["tables"], session_types=["R"])
    assert job.export_id.startswith("export_") and job.job_id in job.export_id
    assert started.wait(5)
    with pytest.raises(RuntimeError):
        de.start_export_job(years=[2024], tiers=["tables"])
    assert de.export_in_use(job.export_id)
    with pytest.raises(RuntimeError):
        de.start_archive_job(job.export_id)
    release.set()
    for _ in range(100):
        if job.status in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert job.status == "completed"
    assert de.get_job_dict(job.job_id)["status"] == "completed"
    listed = de.list_jobs()
    assert listed and listed[0]["job_id"] == job.job_id and listed[0]["kind"] == de.JOB_KIND


def test_get_job_dict_ignores_other_kinds():
    admin_jobs.create_job("plot1", kind="plot_backfill", scope={}, total=1)
    assert de.get_job_dict("plot1") is None
    assert de.get_job_dict("missing") is None


def test_cancel_job_flags_memory_and_mongo():
    job = de.ExportJob(job_id="j6", kind=de.JOB_KIND, export_id="exp1", scope={}, status="running")
    de._register_job(job)
    admin_jobs.create_job("j6", kind=de.JOB_KIND, scope={}, total=1)
    assert de.cancel_job("j6") is True
    assert "j6" in de.CANCELLED
    assert admin_jobs.is_cancelled("j6") is True


def test_archive_job_builds_zip(monkeypatch):
    root = exports.export_dir("exp1")
    root.mkdir(parents=True)
    (root / "data.txt").write_text("hello")
    manifest_mod.save(manifest_mod.new_manifest("exp1", {}, ["tables"]), root)
    job = de.start_archive_job("exp1")
    for _ in range(100):
        if job.status in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert job.status == "completed", job.errors
    assert exports.archive_status("exp1")["status"] == "ready"
    assert job.bytes_written > 0
