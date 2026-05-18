"""Per-API-key hourly usage counters.

One document per ``(key_hash, yyyymmddhh)`` bucket; upserted with ``$inc``
so writes are O(1) and lock-free. A TTL index on ``created_at`` keeps the
collection self-pruning.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.repositories.mongo import get_mongo_client

USAGE_TTL_DAYS = 90


def _collection():
    db_name = os.getenv("MONGODB_DATABASE", "T1API_DB")
    return get_mongo_client()[db_name]["api_key_usage"]


def _bucket(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%d%H")


def record(key_hash: str, status_code: int, duration_ms: float) -> None:
    if not key_hash:
        return
    now = datetime.now(timezone.utc)
    bucket = _bucket(now)
    inc: Dict[str, Any] = {"count": 1, "total_duration_ms": float(duration_ms)}
    if status_code >= 400:
        inc["errors"] = 1
    try:
        _collection().update_one(
            {"key_hash": key_hash, "bucket": bucket},
            {
                "$inc": inc,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    except Exception:
        # Usage tracking must never break a request.
        pass


def summary_for_key(key_hash: str, hours: int = 24) -> Dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    pipeline = [
        {"$match": {"key_hash": key_hash, "created_at": {"$gte": since}}},
        {"$sort": {"bucket": 1}},
    ]
    docs = list(_collection().aggregate(pipeline))
    total = sum(d.get("count", 0) for d in docs)
    errors = sum(d.get("errors", 0) for d in docs)
    total_dur = sum(d.get("total_duration_ms", 0.0) for d in docs)
    avg_ms = (total_dur / total) if total else 0.0
    return {
        "window_hours": hours,
        "total_requests": total,
        "total_errors": errors,
        "error_rate_percent": round((errors / total * 100), 2) if total else 0.0,
        "avg_duration_ms": round(avg_ms, 2),
        "buckets": [
            {
                "bucket": d.get("bucket"),
                "count": d.get("count", 0),
                "errors": d.get("errors", 0),
                "avg_duration_ms": round(d.get("total_duration_ms", 0) / d["count"], 2) if d.get("count") else 0,
            }
            for d in docs
        ],
    }


def top_keys(hours: int = 24, limit: int = 20) -> List[Dict[str, Any]]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    pipeline = [
        {"$match": {"created_at": {"$gte": since}}},
        {
            "$group": {
                "_id": "$key_hash",
                "count": {"$sum": "$count"},
                "errors": {"$sum": "$errors"},
                "total_duration_ms": {"$sum": "$total_duration_ms"},
            }
        },
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    out: List[Dict[str, Any]] = []
    for d in _collection().aggregate(pipeline):
        count = d.get("count", 0)
        out.append(
            {
                "key_hash": d["_id"],
                "count": count,
                "errors": d.get("errors", 0),
                "avg_duration_ms": round((d.get("total_duration_ms", 0) / count), 2) if count else 0,
            }
        )
    return out
