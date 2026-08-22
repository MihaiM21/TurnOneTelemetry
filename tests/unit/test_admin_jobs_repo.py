"""Durable admin job store (offline, mongomock-backed)."""

from datetime import datetime, timedelta, timezone

import pytest

from src.repositories import admin_jobs


@pytest.fixture(autouse=True)
def _clean_collection():
    admin_jobs._collection().delete_many({})
    yield
    admin_jobs._collection().delete_many({})


def _make(job_id="j1", **kw):
    kw.setdefault("kind", "plot_backfill")
    kw.setdefault("scope", {"year": 2025})
    admin_jobs.create_job(job_id, **kw)
    return job_id


def test_create_and_read_back():
    _make("abc", scope={"year": 2025, "gp": "Monaco Grand Prix"}, total=12)

    job = admin_jobs.get_job("abc")
    assert job["job_id"] == "abc"
    assert job["status"] == admin_jobs.STATUS_QUEUED
    assert job["total"] == 12
    assert job["scope"]["gp"] == "Monaco Grand Prix"
    assert job["cancel_requested"] is False
    # Datetimes are serialized so the API can return the document directly.
    assert isinstance(job["created_at"], str)


def test_get_unknown_job_returns_none():
    assert admin_jobs.get_job("nope") is None


def test_update_job_sets_fields_and_heartbeat():
    _make("abc")
    before = admin_jobs.get_job("abc")["heartbeat_at"]

    admin_jobs.update_job("abc", status=admin_jobs.STATUS_RUNNING, done=5, current="2025 R1 R x")

    job = admin_jobs.get_job("abc")
    assert job["status"] == admin_jobs.STATUS_RUNNING
    assert job["done"] == 5
    assert job["current"] == "2025 R1 R x"
    assert job["heartbeat_at"] >= before


def test_push_errors_keeps_only_the_tail():
    _make("abc")
    # Well past the cap, pushed in batches like the worker does.
    for start in range(0, 300, 50):
        admin_jobs.push_errors("abc", [f"err-{i}" for i in range(start, start + 50)])

    errors = admin_jobs.get_job("abc")["errors"]
    assert len(errors) == admin_jobs.MAX_STORED_ERRORS
    # The tail is retained, not the head.
    assert errors[-1] == "err-299"
    assert errors[0] == f"err-{300 - admin_jobs.MAX_STORED_ERRORS}"


def test_push_errors_ignores_empty_batch():
    _make("abc")
    assert admin_jobs.push_errors("abc", []) is False
    assert admin_jobs.get_job("abc")["errors"] == []


def test_cancel_flags_a_running_job():
    _make("abc")
    admin_jobs.update_job("abc", status=admin_jobs.STATUS_RUNNING)

    assert admin_jobs.request_cancel("abc") is True
    assert admin_jobs.is_cancelled("abc") is True


def test_cancel_refuses_a_finished_job():
    _make("abc")
    admin_jobs.update_job("abc", status=admin_jobs.STATUS_COMPLETED)

    # Nothing to cancel; the flag must not be set on a terminal job.
    assert admin_jobs.request_cancel("abc") is False
    assert admin_jobs.is_cancelled("abc") is False


def test_is_cancelled_false_for_unknown_job():
    assert admin_jobs.is_cancelled("nope") is False


def test_list_jobs_is_newest_first_and_filterable():
    for i, status in enumerate([admin_jobs.STATUS_COMPLETED,
                                admin_jobs.STATUS_FAILED,
                                admin_jobs.STATUS_COMPLETED]):
        _make(f"j{i}")
        admin_jobs.update_job(f"j{i}", status=status)
        # Force a distinct creation order.
        admin_jobs._collection().update_one(
            {"_id": f"j{i}"},
            {"$set": {"created_at": datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i)}},
        )

    ids = [j["job_id"] for j in admin_jobs.list_jobs()]
    assert ids == ["j2", "j1", "j0"]

    completed = [j["job_id"] for j in admin_jobs.list_jobs(status=admin_jobs.STATUS_COMPLETED)]
    assert completed == ["j2", "j0"]

    assert admin_jobs.list_jobs(kind="something_else") == []


def test_running_job_with_dead_heartbeat_reports_stale():
    _make("abc")
    admin_jobs.update_job("abc", status=admin_jobs.STATUS_RUNNING)
    dead = datetime.now(timezone.utc) - timedelta(seconds=admin_jobs.STALE_AFTER_SECONDS + 60)
    admin_jobs._collection().update_one({"_id": "abc"}, {"$set": {"heartbeat_at": dead}})

    assert admin_jobs.get_job("abc")["status"] == admin_jobs.STATUS_STALE
    # A stale job must not block a new job over the same scope.
    assert admin_jobs.running_jobs() == []


def test_running_jobs_returns_live_work_only():
    _make("live")
    admin_jobs.update_job("live", status=admin_jobs.STATUS_RUNNING)
    _make("done")
    admin_jobs.update_job("done", status=admin_jobs.STATUS_COMPLETED)

    assert [j["job_id"] for j in admin_jobs.running_jobs()] == ["live"]


def test_delete_job():
    _make("abc")
    assert admin_jobs.delete_job("abc") is True
    assert admin_jobs.get_job("abc") is None
    assert admin_jobs.delete_job("abc") is False
