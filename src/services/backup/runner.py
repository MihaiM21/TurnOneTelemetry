"""Orchestrates a full backup: dump -> encrypt -> upload -> manifest -> retention."""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.core.config import settings
from src.core.logging import get_logger
from src.services.backup.encryption import encrypt_file, load_key
from src.services.backup.jobs import mongo as mongo_job
from src.services.backup.jobs import sqlite as sqlite_job
from src.services.backup.jobs import volumes as volumes_job
from src.services.backup.manifest import Artifact, Manifest, sha256_file
from src.services.backup.metrics import (
    BACKUP_DURATION,
    BACKUP_FAILURES,
    BACKUP_LAST_SUCCESS,
    BACKUP_SIZE,
)
from src.services.backup.retention import GFSRetention, RetentionReport
from src.services.backup.storage import S3Storage

logger = get_logger(__name__)


def _backup_id_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _s3_prefix(tier: str, backup_id: str) -> str:
    return f"{settings.backup_s3_prefix}/{tier}/{backup_id}"


@dataclass
class BackupResult:
    manifest: Manifest
    retention: Optional[RetentionReport] = None
    duration_seconds: float = 0.0


class BackupRunner:
    """Runs one full backup and returns a ``BackupResult``."""

    def __init__(self, storage: Optional[S3Storage] = None) -> None:
        self.storage = storage or S3Storage()
        self.key = load_key(settings.backup_encryption_key)

    def run_full(self, backup_id: Optional[str] = None) -> BackupResult:
        backup_id = backup_id or _backup_id_now()
        started = time.time()
        started_iso = datetime.now(timezone.utc).isoformat()

        # Always land freshly in daily/; retention promotes after success.
        tier = "daily"
        prefix = _s3_prefix(tier, backup_id)

        tmp_root = Path(settings.backup_tmp_dir) / backup_id
        tmp_root.mkdir(parents=True, exist_ok=True)

        manifest = Manifest(
            backup_id=backup_id,
            tier=tier,
            started_at=started_iso,
            finished_at="",
            app_version=settings.app_version,
            mongo_database=settings.mongodb_database,
            sqlite_path=settings.backup_sqlite_path,
        )

        try:
            self._do_job("mongo", lambda: mongo_job.dump(tmp_root / "mongo.archive.gz"),
                         tmp_root, prefix, manifest, suffix=".enc")
            self._do_job("sqlite", lambda: sqlite_job.dump(tmp_root / "sqlite.db.gz"),
                         tmp_root, prefix, manifest, suffix=".enc")
            if settings.backup_include_volumes:
                self._do_job("volumes", lambda: volumes_job.dump(tmp_root / "volumes.tar.gz"),
                             tmp_root, prefix, manifest, suffix=".enc")

            manifest.finished_at = datetime.now(timezone.utc).isoformat()
            self.storage.put_bytes(f"{prefix}/manifest.json", manifest.to_json().encode("utf-8"))

            retention_report = GFSRetention(self.storage).apply(backup_id)

            duration = time.time() - started
            logger.info(f"Backup {backup_id} succeeded in {duration:.1f}s")
            return BackupResult(manifest=manifest, retention=retention_report, duration_seconds=duration)

        except Exception as exc:
            manifest.success = False
            manifest.error = str(exc)
            manifest.finished_at = datetime.now(timezone.utc).isoformat()
            BACKUP_FAILURES.labels(kind="full").inc()
            logger.error(f"Backup {backup_id} failed: {exc}", exc_info=True)
            # Best-effort: still upload manifest so operators can see the failure
            try:
                self.storage.put_bytes(f"{prefix}/manifest.json", manifest.to_json().encode("utf-8"))
            except Exception as upload_exc:
                logger.error(f"Could not upload failure manifest: {upload_exc}")
            raise
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    def _do_job(self, name: str, dump_fn, tmp_root: Path, prefix: str,
                manifest: Manifest, suffix: str) -> None:
        job_started = time.time()
        try:
            plaintext_path = dump_fn()
            if plaintext_path is None:  # job opted out (e.g. volumes when dir missing)
                logger.info(f"Job {name} produced no artifact, skipping")
                return

            plaintext_sha = sha256_file(plaintext_path)
            plaintext_size = plaintext_path.stat().st_size

            cipher_path = plaintext_path.with_name(plaintext_path.name + suffix)
            ciphertext_size, _nonce = encrypt_file(plaintext_path, cipher_path, self.key)
            plaintext_path.unlink(missing_ok=True)

            cipher_sha = sha256_file(cipher_path)
            s3_key = f"{prefix}/{cipher_path.name}"
            self.storage.upload(cipher_path, s3_key)
            cipher_path.unlink(missing_ok=True)

            manifest.artifacts.append(Artifact(
                name=name,
                s3_key=s3_key,
                sha256_plaintext=plaintext_sha,
                sha256_ciphertext=cipher_sha,
                plaintext_bytes=plaintext_size,
                ciphertext_bytes=ciphertext_size,
            ))

            BACKUP_LAST_SUCCESS.labels(kind=name).set(time.time())
            BACKUP_SIZE.labels(kind=name).observe(ciphertext_size)
            BACKUP_DURATION.labels(kind=name).observe(time.time() - job_started)
        except Exception:
            BACKUP_FAILURES.labels(kind=name).inc()
            raise
