"""Manifest serialization + sha256 helper."""
from __future__ import annotations

import hashlib
from pathlib import Path

from src.services.backup.manifest import Artifact, Manifest, sha256_file


def test_sha256_file_matches_hashlib(tmp_path: Path):
    p = tmp_path / "x.bin"
    data = b"abc" * 10_000
    p.write_bytes(data)
    assert sha256_file(p) == hashlib.sha256(data).hexdigest()


def test_manifest_json_round_trip():
    m = Manifest(
        backup_id="2026-06-06T02-00-00Z",
        tier="daily",
        started_at="2026-06-06T02:00:00+00:00",
        finished_at="2026-06-06T02:01:30+00:00",
        app_version="1.2.3",
        mongo_database="T1API_DB",
        sqlite_path="data/x.db",
        artifacts=[Artifact(
            name="mongo", s3_key="t1api/daily/2026-06-06T02-00-00Z/mongo.archive.gz.enc",
            sha256_plaintext="aa" * 32, sha256_ciphertext="bb" * 32,
            plaintext_bytes=1000, ciphertext_bytes=1028,
        )],
    )
    blob = m.to_json()
    m2 = Manifest.from_json(blob)
    assert m2.backup_id == m.backup_id
    assert m2.artifacts[0].name == "mongo"
    assert m2.artifacts[0].plaintext_bytes == 1000
    assert m2.success is True
