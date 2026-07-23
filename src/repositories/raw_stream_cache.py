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


__all__ = ["get_raw_stream", "store_raw_stream", "SCHEMA_VERSION"]
