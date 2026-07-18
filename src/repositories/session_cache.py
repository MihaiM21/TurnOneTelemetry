"""
Durable MongoDB cache for preprocessed V2 session data.

The V2 pipeline re-downloads and re-parses multi-megabyte livetiming streams on
every request. ``SessionDataStore`` caches raw streams only in volatile tiers
(in-process dict + optional Redis). This module adds the durable tier: a single
small "derived bundle" document per completed session that holds the lap-indexed
structures the race features consume (lap times, positions, pit stops, track
status periods, sectors, stints, weather, driver list).

One document per session, keyed by ``{year}_{round_nr}_{session}`` in the
``v2_session_cache`` collection. A full race bundle is a few hundred KB, well
under MongoDB's 16 MB document limit; an oversized guard skips the write rather
than raising if that ever changes.

All functions fail open — a cache miss or a write failure must never break a
request; the caller falls back to recomputing from the raw streams.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional

from bson import BSON
from pymongo.errors import PyMongoError

from src.core.logging import get_logger
from src.repositories.mongo import convert_numpy_types, get_mongo_client

logger = get_logger(__name__)

_COLLECTION = "v2_session_cache"
SCHEMA_VERSION = 1

# MongoDB's hard per-document limit is 16 MB. Stay well under it; the derived
# bundle is a few hundred KB in practice, so anything approaching this cap
# signals a bug (e.g. someone stuffed a raw stream into it).
_MAX_DOC_BYTES = 15 * 1024 * 1024


def _collection():
    db_name = os.getenv("MONGODB_DATABASE", "T1API_DB")
    return get_mongo_client()[db_name][_COLLECTION]


def _doc_id(year: int, round_nr: Any, session: str) -> str:
    return f"{year}_{round_nr}_{session}"


def get_session_bundle(
    year: int, round_nr: Any, session: str
) -> Optional[Dict[str, Any]]:
    """Return the stored derived bundle for a session, or ``None`` on miss.

    Never raises: any Mongo error is logged and treated as a miss so the caller
    recomputes from the raw streams.
    """
    try:
        doc = _collection().find_one(
            {"_id": _doc_id(year, round_nr, session)}, {"bundle": 1}
        )
    except PyMongoError as exc:
        logger.debug("session_cache read skipped (%s): %s", _doc_id(year, round_nr, session), exc)
        return None
    if not doc:
        return None
    bundle = doc.get("bundle")
    return bundle if isinstance(bundle, dict) else None


def store_session_bundle(
    year: int,
    round_nr: Any,
    session: str,
    bundle: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """Upsert a session's derived bundle. Returns ``True`` on success.

    numpy types are converted before writing. If the encoded document would
    exceed the size guard, the write is skipped (logged) rather than raising.
    Any Mongo error is swallowed — a cache write must never break a request.
    """
    doc_id = _doc_id(year, round_nr, session)
    try:
        clean_bundle = convert_numpy_types(bundle)
        document = {
            "_id": doc_id,
            "year": int(year),
            "round_nr": round_nr,
            "session": session,
            "schema_version": SCHEMA_VERSION,
            "created_at": datetime.utcnow(),
            "bundle": clean_bundle,
        }
        if meta:
            document["meta"] = convert_numpy_types(meta)

        size = len(BSON.encode(document))
        if size > _MAX_DOC_BYTES:
            logger.warning(
                "session_cache write skipped for %s: doc %.1f MB exceeds %.0f MB guard",
                doc_id, size / 1024 / 1024, _MAX_DOC_BYTES / 1024 / 1024,
            )
            return False

        _collection().replace_one({"_id": doc_id}, document, upsert=True)
        return True
    except Exception as exc:  # fail open on anything — a cache write must never break a request
        logger.warning("session_cache write failed for %s: %s", doc_id, exc)
        return False


__all__ = ["get_session_bundle", "store_session_bundle", "SCHEMA_VERSION"]
