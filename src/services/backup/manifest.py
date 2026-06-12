"""Backup manifest: SHA-256 checksums, sizes, and metadata per artifact."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Artifact:
    name: str                      # logical name, e.g. "mongo"
    s3_key: str                    # full S3 object key
    sha256_plaintext: str          # checksum of pre-encryption bytes
    sha256_ciphertext: str         # checksum of uploaded (encrypted) bytes
    plaintext_bytes: int
    ciphertext_bytes: int
    encrypted: bool = True


@dataclass
class Manifest:
    backup_id: str                 # ISO-8601 UTC timestamp (filesystem-safe)
    tier: str                      # daily | weekly | monthly
    started_at: str                # ISO-8601
    finished_at: str
    app_version: str
    mongo_database: str
    sqlite_path: str
    artifacts: List[Artifact] = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
    encryption: str = "AES-256-GCM"

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=2, sort_keys=True)

    @staticmethod
    def from_json(s: str) -> "Manifest":
        d = json.loads(s)
        d["artifacts"] = [Artifact(**a) for a in d.get("artifacts", [])]
        return Manifest(**d)
