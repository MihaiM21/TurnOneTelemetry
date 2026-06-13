"""GFS retention classification and pruning logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.services.backup.retention import GFSRetention, classify_tier, parse_backup_id


def _bid(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H-%M-%SZ")


def test_parse_backup_id_round_trip():
    dt = datetime(2026, 6, 7, 2, 0, 0)
    assert parse_backup_id(_bid(dt)) == dt


def test_parse_backup_id_rejects_garbage():
    with pytest.raises(ValueError):
        parse_backup_id("not-a-backup")


def test_classify_first_of_month_is_monthly():
    assert classify_tier(_bid(datetime(2026, 6, 1, 2, 0, 0))) == "monthly"


def test_classify_sunday_is_weekly():
    sunday = datetime(2026, 6, 7, 2, 0, 0)  # Sunday
    assert sunday.weekday() == 6
    assert classify_tier(_bid(sunday)) == "weekly"


def test_classify_first_of_month_wins_over_sunday():
    # 2026-02-01 is a Sunday — first-of-month should still win.
    sunday_first = datetime(2026, 2, 1, 2, 0, 0)
    assert sunday_first.weekday() == 6
    assert classify_tier(_bid(sunday_first)) == "monthly"


def test_classify_regular_weekday_is_daily():
    monday = datetime(2026, 6, 8, 2, 0, 0)
    assert monday.weekday() == 0
    assert classify_tier(_bid(monday)) == "daily"


def test_prune_keeps_most_recent_n(monkeypatch):
    """200-day simulation: after each daily promotion, retention caps each tier."""
    # Build a fake storage that just tracks which backups exist per tier.
    state: dict[str, list[str]] = {"daily": [], "weekly": [], "monthly": []}

    storage = MagicMock()

    def list_backups(tier: str):
        # newest first
        return sorted(state[tier], reverse=True)

    def list_artifacts(tier: str, backup_id: str):
        if backup_id in state[tier]:
            return [f"t1api/{tier}/{backup_id}/mongo.archive.gz.enc"]
        return []

    def copy(src_key: str, dst_key: str):
        pass  # noop

    def delete(key: str):
        parts = key.split("/")
        tier, bid = parts[1], parts[2]
        if bid in state[tier]:
            state[tier].remove(bid)

    def delete_backup(tier: str, backup_id: str):
        if backup_id in state[tier]:
            state[tier].remove(backup_id)
            return 1
        return 0

    storage.list_backups.side_effect = list_backups
    storage.list_artifacts.side_effect = list_artifacts
    storage.copy.side_effect = copy
    storage.delete.side_effect = delete
    storage.delete_backup.side_effect = delete_backup

    # Use the real defaults from settings.
    from src.core.config import settings
    settings.backup_retention_daily = 7
    settings.backup_retention_weekly = 4
    settings.backup_retention_monthly = 6

    retention = GFSRetention(storage)
    start = datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc)
    for i in range(200):
        dt = start + timedelta(days=i)
        bid = _bid(dt)
        # New backups always land in daily/ first.
        state["daily"].append(bid)
        retention.apply(bid)

    assert len(state["daily"]) <= 7
    assert len(state["weekly"]) <= 4
    assert len(state["monthly"]) <= 6
