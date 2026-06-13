"""MongoDB backup/restore via ``mongodump``/``mongorestore``."""
from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import quote_plus

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


def _mongo_uri() -> str:
    return (
        f"mongodb://{settings.mongodb_user}:{quote_plus(settings.mongodb_password)}@"
        f"{settings.mongodb_host}:{settings.mongodb_port}/?directConnection=true"
    )


def dump(dst: Path) -> Path:
    """Run ``mongodump --archive --gzip`` for the configured database."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        settings.backup_mongodump_bin,
        f"--uri={_mongo_uri()}",
        f"--db={settings.mongodb_database}",
        f"--archive={dst}",
        "--gzip",
    ]
    logger.info(f"Running mongodump -> {dst}")
    # Stream stderr to logs; never log the URI (contains the password).
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        # mongodump writes progress to stderr; only log the tail on failure
        # and never echo the command (it contains credentials).
        tail = (proc.stderr or "").splitlines()[-20:]
        raise RuntimeError(f"mongodump failed (rc={proc.returncode}): {' | '.join(tail)}")
    if not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError(f"mongodump produced empty archive at {dst}")
    return dst


def restore(archive: Path, target_db: str | None = None, drop: bool = False) -> None:
    """Restore from a ``mongodump --archive --gzip`` file.

    If ``target_db`` is provided, the archive is restored under that name via
    ``--nsFrom``/``--nsTo`` mapping. ``drop=True`` drops existing collections
    before restoring (still scoped to the target database).
    """
    if not archive.exists():
        raise FileNotFoundError(archive)
    cmd = [
        settings.backup_mongorestore_bin,
        f"--uri={_mongo_uri()}",
        f"--archive={archive}",
        "--gzip",
    ]
    if drop:
        cmd.append("--drop")
    if target_db and target_db != settings.mongodb_database:
        cmd += [
            f"--nsFrom={settings.mongodb_database}.*",
            f"--nsTo={target_db}.*",
        ]
    logger.info(
        f"Running mongorestore from {archive} "
        f"(target_db={target_db or settings.mongodb_database}, drop={drop})"
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        tail = (proc.stderr or "").splitlines()[-20:]
        raise RuntimeError(f"mongorestore failed (rc={proc.returncode}): {' | '.join(tail)}")
