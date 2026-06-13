"""Grandfather-Father-Son retention policy.

Promotion rules (run after each successful daily backup):
  - Backup taken on the 1st of the month -> promote to ``monthly/``
  - Otherwise, backup taken on Sunday   -> promote to ``weekly/``
  - All other backups stay in ``daily/``

Each tier is pruned to its configured retention count (oldest first).

``backup_id`` format is ``YYYY-MM-DDTHH-MM-SSZ`` (filesystem-safe ISO-8601).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple

from src.core.config import settings
from src.core.logging import get_logger
from src.services.backup.storage import S3Storage

logger = get_logger(__name__)

_BACKUP_ID_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})Z$")


def parse_backup_id(backup_id: str) -> datetime:
    m = _BACKUP_ID_RE.match(backup_id)
    if not m:
        raise ValueError(f"invalid backup id: {backup_id}")
    y, mo, d, h, mi, s = (int(x) for x in m.groups())
    return datetime(y, mo, d, h, mi, s)


def classify_tier(backup_id: str) -> str:
    dt = parse_backup_id(backup_id)
    if dt.day == 1:
        return "monthly"
    if dt.weekday() == 6:  # Sunday
        return "weekly"
    return "daily"


@dataclass
class RetentionReport:
    promoted: List[Tuple[str, str]]   # (from_tier, to_tier) pairs
    pruned: List[Tuple[str, str]]     # (tier, backup_id)


class GFSRetention:
    def __init__(self, storage: S3Storage) -> None:
        self.storage = storage

    def apply(self, latest_backup_id: str) -> RetentionReport:
        report = RetentionReport(promoted=[], pruned=[])
        target_tier = classify_tier(latest_backup_id)

        if target_tier != "daily":
            self._promote(latest_backup_id, "daily", target_tier)
            report.promoted.append(("daily", target_tier))

        self._prune("daily", settings.backup_retention_daily, report)
        self._prune("weekly", settings.backup_retention_weekly, report)
        self._prune("monthly", settings.backup_retention_monthly, report)
        return report

    def _promote(self, backup_id: str, from_tier: str, to_tier: str) -> None:
        keys = self.storage.list_artifacts(from_tier, backup_id)
        for src_key in keys:
            dst_key = src_key.replace(
                f"/{from_tier}/{backup_id}/",
                f"/{to_tier}/{backup_id}/",
                1,
            )
            self.storage.copy(src_key, dst_key)
            self.storage.delete(src_key)
        logger.info(f"Promoted backup {backup_id}: {from_tier} -> {to_tier} ({len(keys)} objects)")

    def _prune(self, tier: str, keep: int, report: RetentionReport) -> None:
        ids = self.storage.list_backups(tier)  # newest first
        for stale_id in ids[keep:]:
            deleted = self.storage.delete_backup(tier, stale_id)
            report.pruned.append((tier, stale_id))
            logger.info(f"Pruned {tier}/{stale_id} ({deleted} objects)")
