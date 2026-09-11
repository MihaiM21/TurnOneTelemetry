"""
Durable MongoDB mirror of stored circuit-layout JSON documents.

``src/domain/data/circuits/`` is written to at runtime -- ``circuits_sync``
writes multiviewer layouts as they are discovered, and
``src.services.circuit_derivation`` writes provisional telemetry-derived
layouts -- but the Docker image does not mount ``src/``, so anything written
there is lost on the next rebuild. This collection is the durable copy: every
call to :func:`src.ingestion.circuits_store.save_circuit_layout` mirrors the
layout here, and :func:`src.ingestion.circuits_store.hydrate_from_mongo`
re-materialises missing files from it at startup.

One document per ``(year, circuit_id)`` in the ``circuit_layouts`` collection,
keyed by ``_doc_id``. Both ``layout`` and ``summary`` are stored verbatim
(already ``model_dump()``ed by the caller) alongside a few denormalised
fields used for listing without loading the full payload.

All functions fail open -- a read miss or a write failure must never break a
request; the caller falls back to (or continues using) the on-disk file.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from pymongo.errors import PyMongoError

from src.core.logging import get_logger
from src.repositories.mongo import convert_numpy_types, get_mongo_client
from src.workers.organize_circuits import slugify

logger = get_logger(__name__)

COLLECTION = "circuit_layouts"


def _collection():
    db_name = os.getenv("MONGODB_DATABASE", "T1API_DB")
    return get_mongo_client()[db_name][COLLECTION]


def _doc_id(year: int, circuit_id: Any) -> str:
    return f"{int(year)}_{circuit_id}"


def store_layout(layout: Dict[str, Any], summary: Dict[str, Any]) -> bool:
    """Upsert one circuit layout. Returns ``True`` on success.

    numpy types are converted before writing. Any Mongo error is swallowed --
    a mirror write must never break the on-disk save it accompanies.
    """
    try:
        year = int(layout["year"])
        circuit_id = str(layout["circuit_id"])
        doc = {
            "_id": _doc_id(year, circuit_id),
            "year": year,
            "circuit_id": circuit_id,
            "slug": slugify(layout.get("name") or ""),
            "source": layout.get("source", "multiviewer"),
            "name": layout.get("name", ""),
            "updated_at": datetime.utcnow(),
            "layout": convert_numpy_types(layout),
            "summary": convert_numpy_types(summary),
        }
        _collection().replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return True
    except Exception:  # fail open -- a mirror write must never break the caller
        logger.warning("circuit_layouts: write failed for %s", layout.get("circuit_id"), exc_info=True)
        return False


def get_layout(year: int, circuit_id: Any) -> Optional[Dict[str, Any]]:
    """Return the full stored document for ``(year, circuit_id)``, or ``None``."""
    try:
        return _collection().find_one({"_id": _doc_id(year, circuit_id)})
    except PyMongoError:
        logger.warning("circuit_layouts: read failed for %s", _doc_id(year, circuit_id), exc_info=True)
        return None


def list_layouts(year: Optional[int] = None) -> List[Dict[str, Any]]:
    """Metadata-only listing (excludes ``layout``/``summary``), sorted by year, circuit_id."""
    query: Dict[str, Any] = {}
    if year is not None:
        query["year"] = int(year)
    try:
        cursor = _collection().find(query, {"layout": 0, "summary": 0})
        rows = list(cursor)
    except PyMongoError:
        logger.warning("circuit_layouts: list failed", exc_info=True)
        return []
    return sorted(rows, key=lambda r: (r.get("year"), str(r.get("circuit_id"))))


def iter_layouts() -> Iterator[Dict[str, Any]]:
    """Yield every full stored document. Yields nothing on error."""
    try:
        cursor = _collection().find({})
    except PyMongoError:
        logger.warning("circuit_layouts: iter failed", exc_info=True)
        return
    for doc in cursor:
        yield doc


def delete_layout(year: int, circuit_id: Any) -> bool:
    """Drop one stored layout. Returns ``True`` iff a document was deleted."""
    try:
        return _collection().delete_one({"_id": _doc_id(year, circuit_id)}).deleted_count > 0
    except PyMongoError:
        logger.warning("circuit_layouts: delete failed for %s", _doc_id(year, circuit_id), exc_info=True)
        return False


__all__ = [
    "COLLECTION",
    "delete_layout",
    "get_layout",
    "iter_layouts",
    "list_layouts",
    "store_layout",
]
