"""CLI entry point for restoring from a backup.

Usage:
    python -m src.workers.restore_cli --backup-id 2026-06-06T02-00-00Z
    python -m src.workers.restore_cli --backup-id ... --components mongo,sqlite
    python -m src.workers.restore_cli --backup-id ... --mongo-target-db T1API_DB_RESTORE_TEST
    python -m src.workers.restore_cli --backup-id ... --verify-only
    python -m src.workers.restore_cli --list
"""
from __future__ import annotations

import argparse
import sys

from src.core.logging import get_logger, setup_logging
from src.services.backup.restore import RestoreRunner
from src.services.backup.storage import S3Storage

logger = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore T1API from an S3 backup")
    parser.add_argument("--backup-id", help="Backup ID (e.g. 2026-06-06T02-00-00Z)")
    parser.add_argument("--components", default="", help="Comma-separated subset: mongo,sqlite,volumes")
    parser.add_argument("--mongo-target-db", default=None,
                        help="Restore Mongo into this DB instead of the configured one (safe drill)")
    parser.add_argument("--mongo-drop", action="store_true",
                        help="Drop existing Mongo collections before restoring")
    parser.add_argument("--verify-only", action="store_true",
                        help="Download and checksum, but do not restore")
    parser.add_argument("--list", action="store_true", help="List available backups and exit")
    parser.add_argument("--confirm", action="store_true",
                        help="REQUIRED for actual restore (without --verify-only/--mongo-target-db)")
    args = parser.parse_args(argv)

    setup_logging()

    if args.list:
        storage = S3Storage()
        for tier in ("daily", "weekly", "monthly"):
            print(f"== {tier} ==")
            for bid in storage.list_backups(tier):
                print(f"  {bid}")
        return 0

    if not args.backup_id:
        parser.error("--backup-id is required (or use --list)")

    runner = RestoreRunner()

    if args.verify_only:
        report = runner.verify(args.backup_id)
        print(f"Verified {report.tier}/{report.backup_id}: {report.verified}")
        return 0

    # Refuse a destructive prod restore without an explicit confirmation.
    is_drill = bool(args.mongo_target_db)
    if not is_drill and not args.confirm:
        print("ERROR: pass --confirm to restore over the live database, or --mongo-target-db DB for a drill.",
              file=sys.stderr)
        return 2

    components = [c.strip() for c in args.components.split(",") if c.strip()] or None
    report = runner.restore(
        args.backup_id,
        components=components,
        mongo_target_db=args.mongo_target_db,
        mongo_drop=args.mongo_drop,
    )
    print(f"Restored {report.tier}/{report.backup_id}: restored={report.restored} skipped={report.skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
