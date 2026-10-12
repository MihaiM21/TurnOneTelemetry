"""Find stored data that belongs to a different event than the one it is filed under.

Before the calendar rework a ``(year, round)`` pair could resolve to the wrong
livetiming meeting, so one event's laps were stored under another event's
``gp_id`` -- 2026 Japan Q held Australia's data, 2025 Las Vegas held São Paulo's.
Nothing recorded which session a payload had been computed from, so the damage is
invisible per row. It is not invisible *across* rows: two different Grands Prix
never produce byte-identical qualifying results, so identical payloads under
different ``gp_id``s are the signature of the bug.

:func:`audit_mapping` is read-only. It reports

* **duplicates** -- the same ``(year, session, data_type)`` payload stored under
  more than one ``gp_id``. Every member is a suspect (which one is the true owner
  is not knowable from the data alone), and each comes with a ready
  :class:`~src.services.storage_cleanup.CleanupScope`-shaped dict, so the operator
  feeds it to ``/api/admin/storage/preview`` and then ``/purge`` and lets the
  backfill regenerate the rows from the correct session;
* **unknown gp_ids** -- stored events the season calendar has no event for (an
  old spelling of a name, a cancelled round);
* **provenance coverage** -- how many stored rows record the livetiming session
  they came from (new writes do; older rows cannot be verified).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from src.core.logging import get_logger

logger = get_logger(__name__)

#: Payloads shorter than this (``[]``, ``{}``, a one-field stub) legitimately repeat
#: across events and prove nothing.
MIN_PAYLOAD_CHARS = 60


def _payload_hash(data: Any) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _calendar_gp_ids(year: int) -> Optional[Dict[str, str]]:
    """``gp_id -> event name`` for every event the calendar knows, or ``None`` if unavailable."""
    from src.ingestion.reference import get_season_calendar

    try:
        events = get_season_calendar(year)
    except Exception as exc:  # noqa: BLE001 - a season we cannot list is reported, not fatal
        logger.warning("mapping audit: no calendar for %s: %s", year, exc)
        return None
    return {e["gp_id"]: e.get("grandPrix", "") for e in events if e.get("gp_id")}


def audit_year(year: int, gps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Audit one season's stored GP documents (pure: takes the documents, touches nothing)."""
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    verified = legacy = 0
    stored_gp_ids = set()

    for gp in gps:
        gp_id = gp.get("gp_id")
        if not gp_id:
            continue
        stored_gp_ids.add(gp_id)
        for session in gp.get("sessions") or []:
            session_type = session.get("session_type")
            for entry in session.get("data") or []:
                if entry.get("source"):
                    verified += 1
                else:
                    legacy += 1
                data = entry.get("data")
                if len(json.dumps(data, default=str)) < MIN_PAYLOAD_CHARS:
                    continue
                key = (session_type, entry.get("data_type"), _payload_hash(data))
                groups.setdefault(key, []).append(
                    {"gp_id": gp_id, "event": gp.get("name"), "source": entry.get("source")}
                )

    duplicates = []
    for (session_type, data_type, digest), members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        if len({m["gp_id"] for m in members}) < 2:
            continue
        duplicates.append({
            "year": year,
            "session": session_type,
            "data_type": data_type,
            "payload_sha256": digest,
            "gp_ids": sorted({m["gp_id"] for m in members}),
            "events": sorted({m["event"] or m["gp_id"] for m in members}),
            "scopes": [
                {"year": year, "gp": m["gp_id"], "session": session_type, "data_type": data_type}
                for m in sorted(members, key=lambda m: m["gp_id"])
            ],
        })

    known = _calendar_gp_ids(year)
    unknown = sorted(stored_gp_ids - set(known)) if known is not None else []

    return {
        "year": year,
        "gp_documents": len(gps),
        "duplicates": duplicates,
        "unknown_gp_ids": unknown,
        "calendar_available": known is not None,
        "entries_with_source": verified,
        "entries_without_source": legacy,
    }


def audit_mapping(year: Optional[int] = None) -> Dict[str, Any]:
    """Audit stored Mongo plot data (one ``year``, or every year that has v2 data)."""
    from src.repositories import raw_stream_cache, session_cache
    from src.repositories.mongo import MongoDBManager
    from src.workers.plot_inventory import _years_with_v2_data

    years = [int(year)] if year is not None else sorted(_years_with_v2_data())
    per_year = []
    for yr in years:
        try:
            manager = MongoDBManager(year=yr, version="v2")
            gps = manager.list_all_gps(year=yr)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mapping audit: cannot read %s: %s", yr, exc)
            continue
        per_year.append(audit_year(yr, gps))

    bundles = session_cache.list_bundles(year)
    raw = raw_stream_cache.list_raw_stream_sessions(year)
    duplicates = [d for row in per_year for d in row["duplicates"]]
    return {
        "years": per_year,
        "total_duplicate_groups": len(duplicates),
        "suspect_scopes": [s for d in duplicates for s in d["scopes"]],
        "bundles": {
            "total": len(bundles),
            "without_source": sum(1 for b in bundles if not b.get("source")),
        },
        "raw_stream_sessions": len(raw),
        "note": (
            "Bundles and raw streams without a recorded source are never served (they are rebuilt "
            "on next use). Mongo plot rows without a source are served; duplicates above are the "
            "ones to preview + purge via /api/admin/storage, then regenerate."
        ),
    }
