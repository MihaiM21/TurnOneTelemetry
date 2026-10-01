"""Durable manifest tracking export progress per session and tier.

The manifest is a single JSON document under the export root. It records, per session and
tier, which files were written, their row/byte counts and status, so an interrupted export
can resume without re-deriving already-completed work, and so a status page can summarize
progress without re-scanning the filesystem.
"""

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.services.dataset_export.schema import DATASET_SCHEMA_VERSION
from src.services.dataset_export.writer import FileStat

MANIFEST_FILENAME = "manifest.json"

TIER_STATUSES = ("pending", "done", "failed", "absent")

_SAVE_LOCK = threading.Lock()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def session_key(year: int, round_nr: int, session: str) -> str:
    """Build the stable session identifier used throughout the export."""
    return f"{year}_{round_nr:02d}_{session}"


class SchemaMismatch(Exception):
    """Raised when a manifest's schema_version does not match the current one."""


@dataclass
class TierEntry:
    """Progress/result for one (session, tier) pair."""

    status: str = "pending"
    files: List[FileStat] = field(default_factory=list)
    rows: int = 0
    bytes: int = 0
    error: Optional[str] = None
    finished_at: Optional[str] = None
    absent_streams: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "files": [f.to_dict() for f in self.files],
            "rows": self.rows,
            "bytes": self.bytes,
            "error": self.error,
            "finished_at": self.finished_at,
            "absent_streams": list(self.absent_streams),
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "TierEntry":
        return TierEntry(
            status=data.get("status", "pending"),
            files=[FileStat.from_dict(f) for f in data.get("files", [])],
            rows=int(data.get("rows", 0)),
            bytes=int(data.get("bytes", 0)),
            error=data.get("error"),
            finished_at=data.get("finished_at"),
            absent_streams=list(data.get("absent_streams", [])),
        )


@dataclass
class SessionEntry:
    """One session's tier progress within the export."""

    year: int
    round: int
    gp_name: str
    session: str
    tiers: Dict[str, TierEntry] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "year": self.year,
            "round": self.round,
            "gp_name": self.gp_name,
            "session": self.session,
            "tiers": {k: v.to_dict() for k, v in self.tiers.items()},
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "SessionEntry":
        return SessionEntry(
            year=data["year"],
            round=data["round"],
            gp_name=data.get("gp_name", ""),
            session=data["session"],
            tiers={k: TierEntry.from_dict(v) for k, v in data.get("tiers", {}).items()},
        )


@dataclass
class Manifest:
    """Top-level durable record of one export run."""

    export_id: str
    scope: Dict[str, Any]
    tiers_requested: List[str]
    schema_version: str = DATASET_SCHEMA_VERSION
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)
    sessions: Dict[str, SessionEntry] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "export_id": self.export_id,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "scope": self.scope,
            "tiers_requested": list(self.tiers_requested),
            "sessions": {k: v.to_dict() for k, v in self.sessions.items()},
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Manifest":
        return Manifest(
            export_id=data["export_id"],
            schema_version=data.get("schema_version", DATASET_SCHEMA_VERSION),
            created_at=data.get("created_at", _utcnow_iso()),
            updated_at=data.get("updated_at", _utcnow_iso()),
            scope=data.get("scope", {}),
            tiers_requested=list(data.get("tiers_requested", [])),
            sessions={k: SessionEntry.from_dict(v) for k, v in data.get("sessions", {}).items()},
        )

    def is_done(self, key: str, tier: str) -> bool:
        entry = self.sessions.get(key)
        if entry is None:
            return False
        tier_entry = entry.tiers.get(tier)
        if tier_entry is None:
            return False
        return tier_entry.status in ("done", "absent")

    def mark(
        self,
        key: str,
        tier: str,
        entry: TierEntry,
        *,
        year: int,
        round: int,
        gp_name: str,
        session: str,
    ) -> None:
        session_entry = self.sessions.get(key)
        if session_entry is None:
            session_entry = SessionEntry(year=year, round=round, gp_name=gp_name, session=session)
            self.sessions[key] = session_entry
        session_entry.tiers[tier] = entry
        self.updated_at = _utcnow_iso()

    def totals(self) -> Dict[str, Any]:
        sessions_total = len(self.sessions)
        sessions_done = 0
        sessions_failed = 0
        total_bytes = 0
        rows_by_table: Dict[str, int] = {}
        by_tier: Dict[str, Dict[str, int]] = {
            tier: {"done": 0, "failed": 0, "absent": 0, "pending": 0} for tier in self.tiers_requested
        }

        for session_entry in self.sessions.values():
            all_ok = True
            any_failed = False
            for tier in self.tiers_requested:
                tier_entry = session_entry.tiers.get(tier)
                status = tier_entry.status if tier_entry else "pending"
                if tier not in by_tier:
                    by_tier[tier] = {"done": 0, "failed": 0, "absent": 0, "pending": 0}
                by_tier[tier][status] = by_tier[tier].get(status, 0) + 1

                if status == "failed":
                    any_failed = True
                if status not in ("done", "absent"):
                    all_ok = False

                if tier_entry:
                    total_bytes += tier_entry.bytes
                    for file_stat in tier_entry.files:
                        table_name = file_stat.path.split("/")[0]
                        rows_by_table[table_name] = rows_by_table.get(table_name, 0) + file_stat.rows

            if any_failed:
                sessions_failed += 1
            elif all_ok:
                sessions_done += 1

        return {
            "sessions_total": sessions_total,
            "sessions_done": sessions_done,
            "sessions_failed": sessions_failed,
            "bytes": total_bytes,
            "rows_by_table": rows_by_table,
            "by_tier": by_tier,
        }


def manifest_path(export_dir: Path) -> Path:
    return export_dir / MANIFEST_FILENAME


def load(export_dir: Path) -> Optional[Manifest]:
    """Load the manifest from ``export_dir``, or None if it does not exist."""
    path = manifest_path(export_dir)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return Manifest.from_dict(data)


def save(manifest: Manifest, export_dir: Path) -> None:
    """Atomically persist the manifest to ``export_dir``."""
    with _SAVE_LOCK:
        export_dir.mkdir(parents=True, exist_ok=True)
        path = manifest_path(export_dir)
        tmp_path = path.with_name(path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(manifest.to_dict(), fh, default=str, ensure_ascii=False)
        os.replace(tmp_path, path)


def check_resumable(manifest: Manifest) -> None:
    """Raise SchemaMismatch if the manifest's schema_version differs from the current one."""
    if manifest.schema_version != DATASET_SCHEMA_VERSION:
        raise SchemaMismatch(
            f"manifest schema_version {manifest.schema_version!r} != current {DATASET_SCHEMA_VERSION!r}"
        )


def new_manifest(export_id: str, scope: Dict[str, Any], tiers: List[str]) -> Manifest:
    """Create a fresh Manifest for a new export run."""
    return Manifest(export_id=export_id, scope=scope, tiers_requested=list(tiers))
