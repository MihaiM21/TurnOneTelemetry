"""SQLite hot backup via the online backup API + gzip."""
from __future__ import annotations

import gzip
import shutil
import sqlite3
from pathlib import Path

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


def dump(dst_gz: Path) -> Path:
    """Hot-backup the analytics SQLite DB into a gzipped file."""
    src_path = Path(settings.backup_sqlite_path)
    if not src_path.exists():
        raise FileNotFoundError(f"SQLite DB not found at {src_path}")

    dst_gz.parent.mkdir(parents=True, exist_ok=True)
    tmp_db = dst_gz.with_suffix(".tmpdb")

    logger.info(f"SQLite .backup: {src_path} -> {tmp_db}")
    src_conn = sqlite3.connect(str(src_path))
    try:
        dst_conn = sqlite3.connect(str(tmp_db))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    logger.info(f"gzip {tmp_db} -> {dst_gz}")
    with tmp_db.open("rb") as fin, gzip.open(dst_gz, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout)
    tmp_db.unlink(missing_ok=True)
    return dst_gz


def restore(src_gz: Path, target_path: Path | None = None) -> Path:
    """Restore a gzipped SQLite backup. Writes to a sibling ``.restored`` file
    first, then atomically swaps with the live DB if ``target_path`` matches
    the live path."""
    if not src_gz.exists():
        raise FileNotFoundError(src_gz)
    target = target_path or Path(settings.backup_sqlite_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(target.suffix + ".restored")

    logger.info(f"gunzip {src_gz} -> {staging}")
    with gzip.open(src_gz, "rb") as fin, staging.open("wb") as fout:
        shutil.copyfileobj(fin, fout)

    # Atomic swap on POSIX; on Windows, replace() handles target-exists.
    staging.replace(target)
    logger.info(f"SQLite restored to {target}")
    return target
