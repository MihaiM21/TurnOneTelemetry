"""
Durable MongoDB GridFS cache for parsed V2 raw livetiming streams.

``SessionDataStore`` caches raw streams in volatile tiers only (in-process dict +
optional Redis, 24h TTL). The derived-data bundle (``session_cache.py``) covers
the small lap-indexed *derived* structures, but the two biggest **raw** streams —
``CarData.z`` (per-driver telemetry) and ``Position.z`` (per-driver track
positions) — are never persisted, so every unique per-driver / per-pair request
re-downloads and re-decompresses the whole multi-megabyte stream.

This module adds the durable raw tier. Each parsed stream is stored gzipped in a
GridFS bucket (``v2_raw_cache``), one file per ``(session, stream)`` keyed by
``{year}_{round_nr}_{session}:{stream_name}`` — so a small ``weather`` read never
loads the huge ``car_data`` blob. GridFS chunks internally, so there is no 16 MB
per-document limit, and the bucket is covered by the existing backup system.

All functions fail open — a cache miss or a write failure must never break a
request; the caller falls back to fetching + parsing the raw stream.
"""
from __future__ import annotations

import gzip
import json
import os
import re
from datetime import datetime
from typing import Any, Optional

import gridfs

from src.core.logging import get_logger
from src.repositories.mongo import convert_numpy_types, get_mongo_client

logger = get_logger(__name__)

_BUCKET = "v2_raw_cache"
SCHEMA_VERSION = 1


def _bucket() -> gridfs.GridFS:
    db_name = os.getenv("MONGODB_DATABASE", "T1API_DB")
    return gridfs.GridFS(get_mongo_client()[db_name], collection=_BUCKET)


def _filename(year: int, round_nr: Any, session: str, stream_name: str) -> str:
    return f"{year}_{round_nr}_{session}:{stream_name}"


def get_raw_stream(
    year: int, round_nr: Any, session: str, stream_name: str
) -> Optional[Any]:
    """Return the stored parsed stream, or ``None`` on miss.

    Never raises: any GridFS error (or a stale/corrupt blob) is logged and
    treated as a miss so the caller re-fetches from livetiming.
    """
    name = _filename(year, round_nr, session, stream_name)
    try:
        grid_out = _bucket().find_one(
            {"filename": name, "metadata.schema_version": SCHEMA_VERSION}
        )
        if grid_out is None:
            return None
        return json.loads(gzip.decompress(grid_out.read()).decode("utf-8"))
    except Exception as exc:  # fail open — a cache read can never break the caller
        logger.debug("raw_stream_cache read skipped (%s): %s", name, exc)
        return None


def store_raw_stream(
    year: int, round_nr: Any, session: str, stream_name: str, data: Any
) -> bool:
    """Upsert a parsed stream (gzipped JSON). Returns ``True`` on success.

    Any existing blob for the same filename is deleted first so the cache holds
    exactly one current copy. numpy types are converted before serialising. Any
    error is swallowed — a cache write must never break a request.
    """
    name = _filename(year, round_nr, session, stream_name)
    try:
        payload = gzip.compress(
            json.dumps(convert_numpy_types(data)).encode("utf-8")
        )
        fs = _bucket()
        for existing in fs.find({"filename": name}):
            fs.delete(existing._id)
        fs.put(
            payload,
            filename=name,
            metadata={
                "year": int(year),
                "round_nr": round_nr,
                "session": session,
                "stream": stream_name,
                "schema_version": SCHEMA_VERSION,
                "created_at": datetime.utcnow(),
            },
        )
        return True
    except Exception as exc:  # fail open — a cache write can never break the caller
        logger.warning("raw_stream_cache write failed for %s: %s", name, exc)
        return False


# --------------------------------------------------------------------------- #
# Admin inventory
#
# The bucket had no visibility of any kind before the admin cache page: no way
# to see what it holds, how large it has grown, or whether a schema bump left
# unreadable blobs behind. These read/delete helpers back that page. They are
# deliberately metadata-only — listing must never load a multi-megabyte blob.
# --------------------------------------------------------------------------- #
def _files_collection():
    db_name = os.getenv("MONGODB_DATABASE", "T1API_DB")
    return get_mongo_client()[db_name][f"{_BUCKET}.files"]


def _parse_filename(name: str) -> Any:
    """``{year}_{round}_{session}:{stream}`` -> its session key, or None."""
    session_key, _, stream = str(name).partition(":")
    return (session_key, stream) if stream else None


def list_raw_stream_sessions(year: Optional[int] = None) -> list:
    """Per-session summary of the raw cache: stream count, bytes, age, drift.

    ``schema_drift`` counts blobs written under an older ``SCHEMA_VERSION``;
    those are silently unreadable (``get_raw_stream`` filters on the version),
    so they are pure wasted storage until purged.
    """
    query = {}
    if year is not None:
        query["metadata.year"] = int(year)

    sessions: dict = {}
    try:
        cursor = _files_collection().find(
            query,
            {"filename": 1, "length": 1, "uploadDate": 1, "metadata": 1},
        )
        for doc in cursor:
            meta = doc.get("metadata") or {}
            parsed = _parse_filename(doc.get("filename", ""))
            if not parsed:
                continue
            session_key, stream = parsed
            entry = sessions.setdefault(session_key, {
                "session_key": session_key,
                "year": meta.get("year"),
                "round_nr": meta.get("round_nr"),
                "session": meta.get("session"),
                "streams": [],
                "bytes": 0,
                "schema_drift": 0,
                "newest": None,
            })
            entry["streams"].append(stream)
            entry["bytes"] += int(doc.get("length") or 0)
            if meta.get("schema_version") != SCHEMA_VERSION:
                entry["schema_drift"] += 1
            uploaded = doc.get("uploadDate")
            if uploaded and (entry["newest"] is None or uploaded > entry["newest"]):
                entry["newest"] = uploaded
    except Exception as exc:
        logger.warning("raw_stream_cache inventory failed: %s", exc)
        return []

    out = []
    for entry in sessions.values():
        entry["streams"] = sorted(set(entry["streams"]))
        entry["stream_count"] = len(entry["streams"])
        if entry["newest"] is not None:
            entry["newest"] = entry["newest"].isoformat()
        out.append(entry)
    return sorted(out, key=lambda e: e["session_key"], reverse=True)


def delete_raw_streams(session_key: str) -> int:
    """Drop every stream blob for one session. Returns the number removed."""
    removed = 0
    try:
        fs = _bucket()
        for existing in fs.find({"filename": {"$regex": f"^{re.escape(session_key)}:"}}):
            fs.delete(existing._id)
            removed += 1
    except Exception as exc:
        logger.warning("raw_stream_cache purge failed for %s: %s", session_key, exc)
    return removed


def raw_cache_totals() -> dict:
    """Bucket-wide totals for the admin summary cards."""
    try:
        stats = list(_files_collection().aggregate([
            {"$group": {"_id": None, "files": {"$sum": 1}, "bytes": {"$sum": "$length"}}}
        ]))
        row = stats[0] if stats else {}
        return {"files": int(row.get("files", 0)), "bytes": int(row.get("bytes", 0))}
    except Exception as exc:
        logger.warning("raw_stream_cache totals failed: %s", exc)
        return {"files": 0, "bytes": 0}


__all__ = [
    "SCHEMA_VERSION",
    "delete_raw_streams",
    "get_raw_stream",
    "list_raw_stream_sessions",
    "raw_cache_totals",
    "store_raw_stream",
]
