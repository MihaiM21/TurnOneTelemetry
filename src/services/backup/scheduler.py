"""Daily backup scheduler — asyncio task started in the FastAPI lifespan."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.core.config import settings
from src.core.logging import get_logger
from src.services.backup.runner import BackupResult, BackupRunner

logger = get_logger(__name__)


def _seconds_until_next_run(now: Optional[datetime] = None) -> float:
    now = now or datetime.now(timezone.utc)
    target = now.replace(
        hour=settings.backup_schedule_hour_utc,
        minute=settings.backup_schedule_minute_utc,
        second=0,
        microsecond=0,
    )
    if target <= now:
        target = target + timedelta(days=1)
    return (target - now).total_seconds()


class BackupScheduler:
    def __init__(self) -> None:
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self.last_result: Optional[BackupResult] = None
        self.last_error: Optional[str] = None
        self.last_run_at: Optional[datetime] = None
        self.next_run_at: Optional[datetime] = None
        self._manual_event = asyncio.Event()
        self._manual_in_progress = False

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Backup scheduler started "
            f"(daily @ {settings.backup_schedule_hour_utc:02d}:{settings.backup_schedule_minute_utc:02d} UTC)"
        )

    async def stop(self) -> None:
        self.running = False
        self._manual_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def trigger_now(self) -> None:
        """Trigger an immediate run from another coroutine."""
        self._manual_event.set()

    async def _loop(self) -> None:
        try:
            while self.running:
                delay = _seconds_until_next_run()
                self.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                logger.info(f"Next scheduled backup at {self.next_run_at.isoformat()} (in {delay:.0f}s)")
                try:
                    await asyncio.wait_for(self._manual_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                self._manual_event.clear()
                if not self.running:
                    break
                await self._run_once()
        except asyncio.CancelledError:
            logger.info("Backup scheduler cancelled")
            raise

    async def _run_once(self) -> None:
        self._manual_in_progress = True
        self.last_run_at = datetime.now(timezone.utc)
        try:
            # mongodump/encrypt/upload are blocking; offload to a thread.
            result = await asyncio.to_thread(self._run_blocking)
            self.last_result = result
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            logger.error(f"Scheduled backup failed: {exc}", exc_info=True)
        finally:
            self._manual_in_progress = False

    @staticmethod
    def _run_blocking() -> BackupResult:
        return BackupRunner().run_full()


_scheduler: Optional[BackupScheduler] = None


def get_scheduler() -> BackupScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackupScheduler()
    return _scheduler
