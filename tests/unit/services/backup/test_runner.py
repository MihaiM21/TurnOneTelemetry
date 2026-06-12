"""End-to-end backup runner test with an in-memory S3 stub.

Avoids requiring moto by stubbing ``S3Storage`` directly. The runner is
exercised against real on-disk encryption + manifest production, and we
assert the resulting artifacts are restorable via ``RestoreRunner``.
"""
from __future__ import annotations

import base64
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.config import settings
from src.services.backup.manifest import Manifest


class InMemoryS3:
    """Minimal duck-typed replacement for S3Storage used in tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    # uploads
    def upload(self, local_path: Path, key: str, content_type: str = "application/octet-stream") -> None:
        self.objects[key] = Path(local_path).read_bytes()

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/json") -> None:
        self.objects[key] = data

    # downloads
    def download(self, key: str, local_path: Path) -> None:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_bytes(self.objects[key])

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    # listing / mutation
    def copy(self, src_key: str, dst_key: str) -> None:
        self.objects[dst_key] = self.objects[src_key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def list_prefix(self, prefix: str):
        for k in list(self.objects.keys()):
            if k.startswith(prefix):
                yield {"Key": k, "Size": len(self.objects[k])}

    def ping(self) -> bool:
        return True

    def list_backups(self, tier: str):
        prefix = f"{settings.backup_s3_prefix}/{tier}/"
        ids = set()
        for k in self.objects:
            if k.startswith(prefix):
                rest = k[len(prefix):]
                if "/" in rest:
                    ids.add(rest.split("/", 1)[0])
        return sorted(ids, reverse=True)

    def list_artifacts(self, tier: str, backup_id: str):
        prefix = f"{settings.backup_s3_prefix}/{tier}/{backup_id}/"
        return [k for k in self.objects if k.startswith(prefix)]

    def delete_backup(self, tier: str, backup_id: str) -> int:
        keys = self.list_artifacts(tier, backup_id)
        for k in keys:
            del self.objects[k]
        return len(keys)


@pytest.fixture
def configured_backup(tmp_path, monkeypatch):
    """Seed backup settings and stub out the mongo job (mongodump unavailable in CI)."""
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(settings, "backup_encryption_key", key)
    monkeypatch.setattr(settings, "backup_s3_prefix", "t1api-test")
    monkeypatch.setattr(settings, "backup_tmp_dir", str(tmp_path / "tmp"))
    monkeypatch.setattr(settings, "backup_include_volumes", False)
    monkeypatch.setattr(settings, "backup_retention_daily", 3)
    monkeypatch.setattr(settings, "backup_retention_weekly", 2)
    monkeypatch.setattr(settings, "backup_retention_monthly", 2)

    # Real SQLite DB on disk.
    sqlite_path = tmp_path / "session_analytics.db"
    conn = sqlite3.connect(str(sqlite_path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO t (name) VALUES ('hamilton'), ('verstappen')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings, "backup_sqlite_path", str(sqlite_path))

    # Stub mongo dump/restore — we don't need a real mongodump binary.
    def fake_mongo_dump(dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"fake-mongo-archive-bytes" * 100)
        return dst

    captured = {"restore_args": None}

    def fake_mongo_restore(archive, target_db=None, drop=False):
        captured["restore_args"] = (Path(archive).read_bytes(), target_db, drop)

    monkeypatch.setattr("src.services.backup.runner.mongo_job.dump", fake_mongo_dump)
    monkeypatch.setattr("src.services.backup.restore.mongo_job.restore", fake_mongo_restore)

    return {"sqlite_path": sqlite_path, "captured": captured}


def test_run_full_uploads_artifacts_and_manifest(configured_backup, monkeypatch):
    from src.services.backup.runner import BackupRunner

    fake_s3 = InMemoryS3()
    monkeypatch.setattr("src.services.backup.runner.S3Storage", lambda: fake_s3)
    monkeypatch.setattr("src.services.backup.retention.S3Storage", lambda: fake_s3)

    result = BackupRunner(storage=fake_s3).run_full(backup_id="2026-06-08T02-00-00Z")

    assert result.manifest.success is True
    assert {a.name for a in result.manifest.artifacts} == {"mongo", "sqlite"}
    # Artifacts + manifest uploaded under daily/ (Monday = daily tier).
    keys = list(fake_s3.objects.keys())
    assert any(k.endswith("/manifest.json") for k in keys)
    assert any("mongo.archive.gz.enc" in k for k in keys)
    assert any("sqlite.db.gz.enc" in k for k in keys)


def test_restore_round_trip(configured_backup, monkeypatch, tmp_path):
    from src.services.backup.restore import RestoreRunner
    from src.services.backup.runner import BackupRunner

    fake_s3 = InMemoryS3()
    monkeypatch.setattr("src.services.backup.runner.S3Storage", lambda: fake_s3)
    monkeypatch.setattr("src.services.backup.retention.S3Storage", lambda: fake_s3)
    monkeypatch.setattr("src.services.backup.restore.S3Storage", lambda: fake_s3)

    backup_id = "2026-06-08T02-00-00Z"
    BackupRunner(storage=fake_s3).run_full(backup_id=backup_id)

    # Point sqlite restore at a fresh target file.
    restored_sqlite = tmp_path / "restored.db"
    monkeypatch.setattr(settings, "backup_sqlite_path", str(restored_sqlite))

    runner = RestoreRunner(storage=fake_s3)
    verify_report = runner.verify(backup_id)
    assert set(verify_report.verified) == {"mongo", "sqlite"}

    restore_report = runner.restore(backup_id)
    assert set(restore_report.restored) == {"mongo", "sqlite"}

    # SQLite was actually written to the configured path with the same rows.
    assert restored_sqlite.exists()
    conn = sqlite3.connect(str(restored_sqlite))
    names = [row[0] for row in conn.execute("SELECT name FROM t ORDER BY id")]
    conn.close()
    assert names == ["hamilton", "verstappen"]

    # Mongo restore got our fake archive bytes back (round-trip through encrypt).
    captured = configured_backup["captured"]["restore_args"]
    assert captured is not None
    assert captured[0] == b"fake-mongo-archive-bytes" * 100


def test_corrupted_artifact_fails_verify(configured_backup, monkeypatch):
    from src.services.backup.restore import RestoreRunner
    from src.services.backup.runner import BackupRunner

    fake_s3 = InMemoryS3()
    monkeypatch.setattr("src.services.backup.runner.S3Storage", lambda: fake_s3)
    monkeypatch.setattr("src.services.backup.retention.S3Storage", lambda: fake_s3)
    monkeypatch.setattr("src.services.backup.restore.S3Storage", lambda: fake_s3)

    backup_id = "2026-06-08T02-00-00Z"
    BackupRunner(storage=fake_s3).run_full(backup_id=backup_id)

    # Tamper with the mongo artifact ciphertext.
    for k in list(fake_s3.objects.keys()):
        if k.endswith("mongo.archive.gz.enc"):
            fake_s3.objects[k] = fake_s3.objects[k][:-1] + b"\x00"

    runner = RestoreRunner(storage=fake_s3)
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        runner.verify(backup_id)
