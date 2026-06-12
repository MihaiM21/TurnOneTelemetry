"""Restore artifacts from S3: download -> verify sha256 -> decrypt -> apply."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from src.core.config import settings
from src.core.logging import get_logger
from src.services.backup.encryption import decrypt_file, load_key
from src.services.backup.jobs import mongo as mongo_job
from src.services.backup.jobs import sqlite as sqlite_job
from src.services.backup.jobs import volumes as volumes_job
from src.services.backup.manifest import Artifact, Manifest, sha256_file
from src.services.backup.storage import S3Storage

logger = get_logger(__name__)


@dataclass
class RestoreReport:
    backup_id: str
    tier: str
    verified: List[str] = field(default_factory=list)
    restored: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


class RestoreRunner:
    def __init__(self, storage: Optional[S3Storage] = None) -> None:
        self.storage = storage or S3Storage()
        self.key = load_key(settings.backup_encryption_key)

    def find_tier(self, backup_id: str) -> str:
        for tier in ("daily", "weekly", "monthly"):
            if backup_id in self.storage.list_backups(tier):
                return tier
        raise FileNotFoundError(f"backup id {backup_id} not found in any tier")

    def load_manifest(self, backup_id: str, tier: Optional[str] = None) -> Manifest:
        tier = tier or self.find_tier(backup_id)
        key = f"{settings.backup_s3_prefix}/{tier}/{backup_id}/manifest.json"
        return Manifest.from_json(self.storage.get_bytes(key).decode("utf-8"))

    def verify(self, backup_id: str) -> RestoreReport:
        """Download each artifact and confirm ciphertext sha256 matches manifest."""
        tier = self.find_tier(backup_id)
        manifest = self.load_manifest(backup_id, tier)
        report = RestoreReport(backup_id=backup_id, tier=tier)
        tmp = Path(settings.backup_tmp_dir) / f"verify-{backup_id}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            for artifact in manifest.artifacts:
                local = tmp / Path(artifact.s3_key).name
                self.storage.download(artifact.s3_key, local)
                actual = sha256_file(local)
                if actual != artifact.sha256_ciphertext:
                    raise RuntimeError(
                        f"sha256 mismatch on {artifact.name}: expected {artifact.sha256_ciphertext}, got {actual}"
                    )
                report.verified.append(artifact.name)
            return report
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def restore(
        self,
        backup_id: str,
        *,
        components: Optional[List[str]] = None,
        mongo_target_db: Optional[str] = None,
        mongo_drop: bool = False,
    ) -> RestoreReport:
        """Restore the named ``components`` (default: all in manifest).

        ``mongo_target_db`` lets you restore into a throwaway DB for drills
        without overwriting production. ``mongo_drop=True`` drops collections
        in the target DB before restore.
        """
        tier = self.find_tier(backup_id)
        manifest = self.load_manifest(backup_id, tier)
        report = RestoreReport(backup_id=backup_id, tier=tier)
        wanted = set(components) if components else {a.name for a in manifest.artifacts}

        tmp = Path(settings.backup_tmp_dir) / f"restore-{backup_id}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            by_name: Dict[str, Artifact] = {a.name: a for a in manifest.artifacts}
            for name in wanted:
                artifact = by_name.get(name)
                if artifact is None:
                    report.skipped.append(name)
                    continue
                self._restore_one(artifact, tmp, mongo_target_db=mongo_target_db, mongo_drop=mongo_drop)
                report.restored.append(name)
            return report
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _restore_one(
        self,
        artifact: Artifact,
        tmp: Path,
        *,
        mongo_target_db: Optional[str],
        mongo_drop: bool,
    ) -> None:
        cipher_local = tmp / Path(artifact.s3_key).name
        self.storage.download(artifact.s3_key, cipher_local)

        actual_cipher = sha256_file(cipher_local)
        if actual_cipher != artifact.sha256_ciphertext:
            raise RuntimeError(f"ciphertext sha256 mismatch on {artifact.name}")

        # Drop the `.enc` suffix to recover the plaintext filename.
        plain_name = cipher_local.name[:-4] if cipher_local.name.endswith(".enc") else cipher_local.name + ".plain"
        plain_local = tmp / plain_name
        decrypt_file(cipher_local, plain_local, self.key)

        actual_plain = sha256_file(plain_local)
        if actual_plain != artifact.sha256_plaintext:
            raise RuntimeError(f"plaintext sha256 mismatch on {artifact.name}")

        if artifact.name == "mongo":
            mongo_job.restore(plain_local, target_db=mongo_target_db, drop=mongo_drop)
        elif artifact.name == "sqlite":
            sqlite_job.restore(plain_local)
        elif artifact.name == "volumes":
            volumes_job.restore(plain_local)
        else:
            raise ValueError(f"unknown artifact name: {artifact.name}")
