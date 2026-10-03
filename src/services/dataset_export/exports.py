"""Read-model and file operations over the export directory.

Shared by the admin API and the admin UI (which calls services in-process, per
the admin-console rule), so both surfaces see the same listing, the same path
guard and the same delete/archive semantics.

Every path that reaches the filesystem goes through ``resolve_within`` — an
export id and a relative file path both arrive over HTTP, so a crafted value
must not be able to read or delete anything outside ``settings.export_dir``.
"""

from __future__ import annotations

import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.core.config import settings
from src.core.logging import get_logger
from src.services.dataset_export import manifest as manifest_mod
from src.services.dataset_export.manifest import Manifest
from src.services.dataset_export.writer import FileStat, relpath, sha256_of
from src.services.plotting.output import UnsafeOutputPath, resolve_within

logger = get_logger(__name__)

EXPORT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ARCHIVE_SUFFIX = ".zip"


class ExportNotFound(LookupError):
    """No export with that id (or the id is malformed)."""


class ExportBusy(RuntimeError):
    """The export is referenced by a running job."""


@dataclass
class ExportSummary:
    export_id: str
    created_at: Optional[str]
    updated_at: Optional[str]
    schema_version: Optional[str]
    tiers: List[str] = field(default_factory=list)
    scope: Dict[str, Any] = field(default_factory=dict)
    sessions_total: int = 0
    sessions_done: int = 0
    sessions_failed: int = 0
    bytes: int = 0
    rows_by_table: Dict[str, int] = field(default_factory=dict)
    archive: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def export_root() -> Path:
    root = Path(settings.export_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def validate_export_id(export_id: str) -> str:
    if not isinstance(export_id, str) or not EXPORT_ID_RE.match(export_id):
        raise ExportNotFound(f"invalid export id {export_id!r}")
    return export_id


def export_dir(export_id: str) -> Path:
    """``{export_root}/{export_id}`` — validated and resolved inside the root."""
    validate_export_id(export_id)
    try:
        return resolve_within(export_id, export_root())
    except UnsafeOutputPath as exc:
        raise ExportNotFound(str(exc)) from exc


def load_manifest(export_id: str) -> Manifest:
    path = export_dir(export_id)
    manifest = manifest_mod.load(path) if path.is_dir() else None
    if manifest is None:
        raise ExportNotFound(export_id)
    return manifest


def archive_path(export_id: str) -> Path:
    validate_export_id(export_id)
    return export_root() / f"{export_id}{ARCHIVE_SUFFIX}"


def archive_status(export_id: str) -> Dict[str, Any]:
    path = archive_path(export_id)
    tmp = path.with_name(path.name + ".tmp")
    if path.is_file():
        return {"status": "ready", "bytes": path.stat().st_size}
    if tmp.exists():
        return {"status": "building", "bytes": tmp.stat().st_size}
    return {"status": "none", "bytes": 0}


def summarize(export_id: str) -> ExportSummary:
    manifest = load_manifest(export_id)
    totals = manifest.totals()
    return ExportSummary(
        export_id=export_id,
        created_at=manifest.created_at,
        updated_at=manifest.updated_at,
        schema_version=manifest.schema_version,
        tiers=list(manifest.tiers_requested),
        scope=dict(manifest.scope),
        sessions_total=totals.get("sessions_total", 0),
        sessions_done=totals.get("sessions_done", 0),
        sessions_failed=totals.get("sessions_failed", 0),
        bytes=totals.get("bytes", 0),
        rows_by_table=totals.get("rows_by_table", {}),
        archive=archive_status(export_id),
    )


def list_exports() -> List[ExportSummary]:
    """Every export directory holding a manifest, newest first."""
    out: List[ExportSummary] = []
    for child in export_root().iterdir():
        if not child.is_dir() or not EXPORT_ID_RE.match(child.name):
            continue
        try:
            out.append(summarize(child.name))
        except ExportNotFound:
            continue
        except Exception:  # noqa: BLE001 - one corrupt manifest must not hide the rest
            logger.exception("Unreadable export manifest in %s", child)
    out.sort(key=lambda s: s.created_at or "", reverse=True)
    return out


def list_export_files(export_id: str, prefix: str = "") -> List[FileStat]:
    """Files recorded in the manifest (so only completed files are listed)."""
    manifest = load_manifest(export_id)
    files: List[FileStat] = []
    for entry in manifest.sessions.values():
        for tier in entry.tiers.values():
            files.extend(tier.files)
    corpus_dir = export_dir(export_id) / "corpus"
    if corpus_dir.is_dir():
        for path in sorted(corpus_dir.iterdir()):
            if path.is_file() and not path.name.endswith(".tmp"):
                files.append(FileStat(path=relpath(export_dir(export_id), path), rows=0,
                                      bytes=path.stat().st_size, sha256=""))
    root = export_dir(export_id)
    manifest_file = manifest_mod.manifest_path(root)
    if manifest_file.is_file():
        files.append(FileStat(path=manifest_mod.MANIFEST_FILENAME, rows=0,
                              bytes=manifest_file.stat().st_size, sha256=""))
    if prefix:
        files = [f for f in files if f.path.startswith(prefix)]
    files.sort(key=lambda f: f.path)
    return files


def resolve_export_file(export_id: str, relative: str) -> Path:
    """A file inside the export, or ``ExportNotFound`` — never a path outside it.

    In-progress ``*.tmp`` files are hidden too: they are being written by the
    job and would be served truncated.
    """
    base = export_dir(export_id)
    if not relative or relative.endswith(".tmp") or "\\" in relative:
        raise ExportNotFound(relative)
    try:
        path = resolve_within(relative, base)
    except UnsafeOutputPath as exc:
        raise ExportNotFound(relative) from exc
    if path == base or not path.is_file():
        raise ExportNotFound(relative)
    return path


def build_archive(export_id: str, *, cancelled: Optional[Callable[[], bool]] = None) -> Path:
    """Zip the whole export (``ZIP_STORED``: Parquet and gzip are already compressed).

    Written to ``{id}.zip.tmp`` and renamed on success so a half-built archive
    is never served. Returns the archive path; raises ``ExportBusy`` if
    cancelled midway (the tmp is removed).
    """
    cancelled = cancelled or (lambda: False)
    base = export_dir(export_id)
    load_manifest(export_id)
    target = archive_path(export_id)
    tmp = target.with_name(target.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for path in sorted(base.rglob("*")):
                if not path.is_file() or path.name.endswith(".tmp"):
                    continue
                if cancelled():
                    raise ExportBusy("archive build cancelled")
                zf.write(path, arcname=f"{export_id}/{relpath(base, path)}")
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def archive_stat(export_id: str) -> Optional[FileStat]:
    path = archive_path(export_id)
    if not path.is_file():
        return None
    return FileStat(path=path.name, rows=0, bytes=path.stat().st_size, sha256=sha256_of(path))


def delete_export(export_id: str, *, in_use: Optional[Callable[[str], bool]] = None) -> int:
    """Remove the export directory and its archive; returns bytes freed.

    ``in_use(export_id)`` lets the caller veto (a running job still writing
    it) without this module importing the worker layer.
    """
    base = export_dir(export_id)
    if not base.is_dir():
        raise ExportNotFound(export_id)
    if in_use is not None and in_use(export_id):
        raise ExportBusy(f"export {export_id} has a running job")
    freed = 0
    for path in base.rglob("*"):
        if path.is_file():
            freed += path.stat().st_size
    # Re-resolve immediately before the destructive call.
    shutil.rmtree(resolve_within(export_id, export_root()))
    archive = archive_path(export_id)
    if archive.is_file():
        freed += archive.stat().st_size
        archive.unlink()
    return freed
