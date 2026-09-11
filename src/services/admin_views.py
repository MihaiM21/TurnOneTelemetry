"""Read-models shared by the admin JSON API and the admin web UI.

Both surfaces need the same aggregated views (cache inventory, stored-data
browse), but they authenticate differently: the API router uses API keys and
decorates every route with ``@apply_tiered_limit``, which requires an
initialized limiter *at import time*. Keeping these functions here means the
cookie-session UI can reuse them without importing the API router — and without
inheriting that import-time dependency.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.core.logging import get_logger
from src.ingestion import circuits_store
from src.repositories import circuit_layouts, raw_stream_cache, session_cache
from src.repositories.mongo import MongoDBManager

logger = get_logger(__name__)


def cache_inventory(year: Optional[int] = None) -> Dict[str, Any]:
    """Combined raw-stream + derived-bundle cache inventory.

    ``schema_drift`` counts entries written under an older ``SCHEMA_VERSION``.
    Those are filtered out on read, so they are unreadable *and* still occupying
    storage — the main thing an operator needs this page to surface.
    """
    raw_sessions = raw_stream_cache.list_raw_stream_sessions(year)
    bundles = session_cache.list_bundles(year)
    totals = raw_stream_cache.raw_cache_totals()
    bundle_stats = session_cache.bundle_totals()
    return {
        "year": year,
        "raw": {
            "sessions": raw_sessions,
            "session_count": len(raw_sessions),
            "files": totals["files"],
            "bytes": totals["bytes"],
            "schema_version": raw_stream_cache.SCHEMA_VERSION,
            "schema_drift": sum(s["schema_drift"] for s in raw_sessions),
        },
        "bundles": {
            "sessions": bundles,
            "session_count": len(bundles),
            "total": bundle_stats["bundles"],
            "schema_version": session_cache.SCHEMA_VERSION,
            "schema_drift": bundle_stats["schema_drift"],
        },
    }


def browse_stored_data(
    year: int, gp: Optional[str] = None, session: Optional[str] = None
) -> Dict[str, Any]:
    """Stored ``data_type`` keys per GP/session, including legacy/orphan keys.

    The plot inventory only reports against the singleton expectation set, so it
    cannot show a key that nothing expects — which is exactly the key you need
    to find when a payload was generated from bad upstream data.
    """
    manager = MongoDBManager(year=year, version="v2")
    rows: List[Dict[str, Any]] = manager.summarize_stored_data(year)

    if gp:
        needle = gp.strip().lower().replace(" ", "")
        rows = [
            r for r in rows
            if needle in str(r.get("event_name", "")).lower().replace(" ", "")
            or needle == str(r.get("gp_id", "")).lower()
        ]
    if session:
        wanted = session.strip().upper()
        rows = [r for r in rows if str(r.get("session_type", "")).upper() == wanted]

    return {
        "year": year,
        "rows": rows,
        "total_sessions": len(rows),
        "total_data_types": sum(r["count"] for r in rows),
    }


def circuit_layout_status(year: int) -> Dict[str, Any]:
    """Per-event view of which circuits in ``year`` have a stored layout.

    Schedule-driven (``get_season_events``) rather than a directory scan, so a
    brand-new circuit with no file at all shows up as *missing* instead of
    silently not existing. The event -> layout link is livetiming's
    ``Circuit.Key`` (``circuitKey``), which the enrichment carries through; an
    event livetiming has not listed yet has no key and is reported as
    ``unknown_key`` rather than missing.

    ``rows[i]``: ``round, grand_prix, circuit, country, circuit_key,
    layout_present, source, file, in_mongo``. Layout sources are
    ``"multiviewer"`` (seeded/synced), ``"telemetry"`` (derived from a lap) or
    ``"invalid"`` (file present but unparseable). Mongo lookups fail open.
    """
    from src.ingestion.reference import get_season_events

    try:
        events = get_season_events(year)
        error = None
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator, never raised
        logger.warning("circuit_layout_status: season %s unavailable: %s", year, exc)
        events, error = [], str(exc)

    files = circuits_store.list_circuit_files(year)
    mongo_ids = set()
    try:
        mongo_ids = {str(doc.get("circuit_id")) for doc in circuit_layouts.list_layouts(year)}
    except Exception:  # noqa: BLE001
        logger.debug("circuit_layout_status: Mongo listing unavailable", exc_info=True)

    rows: List[Dict[str, Any]] = []
    missing = unknown_key = 0
    for event in events:
        key = event.get("circuitKey")
        circuit_id = str(key) if key is not None else None
        entry = files.get(circuit_id) if circuit_id else None
        if circuit_id is None:
            unknown_key += 1
        elif entry is None:
            missing += 1
        rows.append({
            "round": event.get("round"),
            "grand_prix": event.get("grandPrix") or event.get("name") or "",
            "circuit": event.get("circuit") or "",
            "location": event.get("location") or "",
            "country": event.get("country") or "",
            "circuit_key": key,
            "layout_present": entry is not None,
            "source": entry["source"] if entry else None,
            "file": entry["path"].name if entry else None,
            "in_mongo": circuit_id in mongo_ids if circuit_id else False,
        })

    return {
        "year": int(year),
        "rows": rows,
        "total": len(rows),
        "missing": missing,
        "unknown_key": unknown_key,
        "stored_files": len(files),
        "error": error,
    }
