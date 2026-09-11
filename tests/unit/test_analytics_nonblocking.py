"""Session analytics must not block the event loop.

``_track()`` is called directly inside ``async def`` handlers on ~50 analysis
endpoints. Before this was fixed, each call performed a lock-held
``sqlite3.connect`` plus several INSERT/UPDATE statements *on the event loop*,
so every concurrent request stalled behind the slowest disk write.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from src.core.observability.analytics import SessionTracker


@pytest.fixture()
def tracker(tmp_path):
    return SessionTracker(db_path=str(tmp_path / "analytics.db"))


def test_track_session_returns_immediately(tracker):
    """The call must be fire-and-forget, not a synchronous disk write."""

    async def hammer():
        start = time.perf_counter()
        for _ in range(200):
            tracker.track_session("top-speed", 2025, 1, "Q")
        return time.perf_counter() - start

    elapsed = asyncio.run(hammer())
    # Generous: a synchronous implementation took orders of magnitude longer.
    assert elapsed < 0.5, f"track_session blocked the loop for {elapsed:.3f}s"


def test_queued_writes_are_applied(tmp_path):
    """Fire-and-forget must still persist every row once flushed."""
    db = tmp_path / "analytics.db"
    tracker = SessionTracker(db_path=str(db))
    for _ in range(25):
        tracker.track_session("race-gaps", 2025, 3, "R")
    tracker.flush()

    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert count == 25


def test_tracking_failure_never_propagates(tracker, monkeypatch):
    """Analytics are best-effort; a broken write must not surface to callers."""
    def boom(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(tracker, "_track_session_blocking", boom)
    tracker.track_session("top-speed", 2025, 1, "Q")
    tracker.flush()  # must not raise
