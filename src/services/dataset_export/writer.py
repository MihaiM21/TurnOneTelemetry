"""File writers for the dataset export.

All writes are tmp-then-``os.replace`` so a crash or cancellation never leaves a partial
file at the final path, and every writer returns a :class:`FileStat` (relative POSIX path,
row count, byte size, sha256) that the manifest persists for auditing and resumability.
"""

import gzip
import json
import os
import threading
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.core.logging import get_logger
from src.services.dataset_export.schema import TableSpec, arrow_schema, coerce, file_columns

logger = get_logger(__name__)

_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class FileStat:
    """Metadata about one written file, relative to the export root."""

    path: str
    rows: int
    bytes: int
    sha256: str

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "rows": self.rows, "bytes": self.bytes, "sha256": self.sha256}

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "FileStat":
        return FileStat(
            path=data["path"],
            rows=int(data["rows"]),
            bytes=int(data["bytes"]),
            sha256=data["sha256"],
        )


def partition_dir(root: Path, table: str, year: int, round_nr: int, session: str) -> Path:
    """Return the Hive-partitioned directory for one table/session combination."""
    return root / table / f"year={year}" / f"round={round_nr:02d}" / f"session={session}"


def relpath(root: Path, path: Path) -> str:
    """Return ``path`` relative to ``root`` as a POSIX-style string."""
    return path.relative_to(root).as_posix()


def sha256_of(path: Path) -> str:
    """Compute the sha256 hex digest of a file, reading it in fixed-size chunks."""
    digest = sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finalize_tmp(tmp_path: Path, final_path: Path, root: Path, rows: int) -> FileStat:
    os.replace(tmp_path, final_path)
    size = final_path.stat().st_size
    digest = sha256_of(final_path)
    return FileStat(path=relpath(root, final_path), rows=rows, bytes=size, sha256=digest)


def write_parquet(df: pd.DataFrame, path: Path, spec: TableSpec, root: Path) -> FileStat:
    """Coerce ``df`` to ``spec`` and write it as a single Parquet file at ``path``."""
    coerced = coerce(df, spec)[file_columns(spec)]
    table = pa.Table.from_pandas(coerced, schema=arrow_schema(spec), preserve_index=False)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        pq.write_table(table, tmp_path, compression="zstd")
        return _finalize_tmp(tmp_path, path, root, len(coerced))
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


class ParquetChunkWriter:
    """Context manager that appends DataFrame chunks to a single Parquet file.

    Opens the underlying ``pyarrow.parquet.ParquetWriter`` lazily on the first ``write``
    call. On a clean exit, closes the writer and atomically replaces the final path; if no
    chunk was ever written, an empty file with the declared schema is produced instead. On
    an exception, the writer is closed and the temp file removed.
    """

    def __init__(self, path: Path, spec: TableSpec, root: Path):
        self.path = path
        self.spec = spec
        self.root = root
        self.rows = 0
        self._schema = arrow_schema(spec)
        self._writer: Optional[pq.ParquetWriter] = None
        self._tmp_path = path.with_name(path.name + ".tmp")
        self.stat: Optional[FileStat] = None

    def __enter__(self) -> "ParquetChunkWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return self

    def write(self, df: pd.DataFrame) -> None:
        coerced = coerce(df, self.spec)[file_columns(self.spec)]
        table = pa.Table.from_pandas(coerced, schema=self._schema, preserve_index=False)
        if self._writer is None:
            self._writer = pq.ParquetWriter(self._tmp_path, self._schema, compression="zstd")
        self._writer.write_table(table)
        self.rows += len(coerced)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None

        if exc_type is not None:
            if self._tmp_path.exists():
                self._tmp_path.unlink()
            return

        if self.rows == 0:
            empty_table = pa.Table.from_pandas(
                coerce(pd.DataFrame(), self.spec)[file_columns(self.spec)], schema=self._schema,
                preserve_index=False,
            )
            pq.write_table(empty_table, self._tmp_path, compression="zstd")

        self.stat = _finalize_tmp(self._tmp_path, self.path, self.root, self.rows)


def write_jsonl_gz(records: Iterable[dict], path: Path, root: Path) -> FileStat:
    """Write records as gzip-compressed JSON Lines, one ``json.dumps`` per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    rows = 0
    try:
        with gzip.open(tmp_path, "wt", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, default=str, ensure_ascii=False))
                fh.write("\n")
                rows += 1
        return _finalize_tmp(tmp_path, path, root, rows)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def write_json(obj: Any, path: Path, root: Path) -> FileStat:
    """Write a single JSON object/array to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, default=str, ensure_ascii=False, indent=None)
        return _finalize_tmp(tmp_path, path, root, 1)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def append_jsonl(records: Iterable[dict], path: Path, lock: threading.Lock) -> int:
    """Append records as plain (uncompressed) JSON Lines under ``lock``. Returns count."""
    count = 0
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, default=str, ensure_ascii=False))
                fh.write("\n")
                count += 1
    return count


def rewrite_jsonl_without(path: Path, session_key: str, lock: threading.Lock) -> int:
    """Drop lines whose JSON ``session_key`` field equals ``session_key``. Returns removed count."""
    with lock:
        if not path.exists():
            return 0

        removed = 0
        tmp_path = path.with_name(path.name + ".tmp")
        try:
            with open(path, "r", encoding="utf-8") as src, open(tmp_path, "w", encoding="utf-8") as dst:
                for line in src:
                    stripped = line.rstrip("\n")
                    if not stripped:
                        continue
                    try:
                        rec = json.loads(stripped)
                    except (json.JSONDecodeError, ValueError):
                        dst.write(stripped + "\n")
                        continue
                    if isinstance(rec, dict) and rec.get("session_key") == session_key:
                        removed += 1
                        continue
                    dst.write(stripped + "\n")
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise
        return removed
