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

from src.repositories import raw_stream_cache, session_cache
from src.repositories.mongo import MongoDBManager


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
