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


def _month_start(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month_start(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    year, month = now.year, now.month
    if month == 12:
        return datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(year, month + 1, 1, tzinfo=timezone.utc)


def _day_start(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _aggregate_window(match: Dict[str, Any]) -> Dict[str, Any]:
    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": None,
                "count": {"$sum": "$count"},
                "errors": {"$sum": "$errors"},
                "total_duration_ms": {"$sum": "$total_duration_ms"},
            }
        },
    ]
    docs = list(_collection().aggregate(pipeline))
    if not docs:
        return {"requests": 0, "errors": 0, "avg_ms": 0.0, "error_rate_percent": 0.0}
    d = docs[0]
    count = d.get("count", 0)
    errors = d.get("errors", 0)
    total_dur = d.get("total_duration_ms", 0.0)
    return {
        "requests": count,
        "errors": errors,
        "avg_ms": round((total_dur / count), 2) if count else 0.0,
        "error_rate_percent": round((errors / count * 100), 2) if count else 0.0,
    }


def dashboard_for_key(key_hash: str) -> Dict[str, Any]:
    """Return today / last 7d / month aggregates plus last-24h hourly series.

    All windows are UTC. Uses the existing hourly buckets — no new collection.
    """
    now = datetime.now(timezone.utc)
    day = _day_start(now)
    week = now - timedelta(days=7)
    month = _month_start(now)

    today = _aggregate_window({"key_hash": key_hash, "created_at": {"$gte": day}})
    last_7 = _aggregate_window({"key_hash": key_hash, "created_at": {"$gte": week}})
    current_month = _aggregate_window({"key_hash": key_hash, "created_at": {"$gte": month}})

    last_24 = summary_for_key(key_hash, hours=24)["buckets"]

    return {
        "today": today,
        "last_7_days": last_7,
        "current_month": current_month,
        "last_24h": last_24,
        "month_resets_at": _next_month_start(now).isoformat(),
    }


def peak_hours_for_key(key_hash: Optional[str], days: int = 30) -> List[Dict[str, Any]]:
    """Aggregate by hour-of-day (UTC) over the last ``days`` days.

    Returns 24 entries (hour 0..23) so callers can plot a fixed bar chart.
    Pass ``key_hash=None`` for a global view.
    """
    days = max(1, min(days, 90))  # collection TTL is 90 days
    since = datetime.now(timezone.utc) - timedelta(days=days)
    match: Dict[str, Any] = {"created_at": {"$gte": since}}
    if key_hash:
        match["key_hash"] = key_hash
    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": {"$substr": ["$bucket", 8, 2]},
                "count": {"$sum": "$count"},
                "errors": {"$sum": "$errors"},
            }
        },
    ]
    by_hour = {int(d["_id"]): d for d in _collection().aggregate(pipeline) if d.get("_id")}
    out: List[Dict[str, Any]] = []
    for h in range(24):
        d = by_hour.get(h)
        out.append(
            {
                "hour": h,
                "count": d.get("count", 0) if d else 0,
                "errors": d.get("errors", 0) if d else 0,
            }
        )
    return out


def global_dashboard() -> Dict[str, Any]:
    """Platform-wide totals: today / 7d / month."""
    now = datetime.now(timezone.utc)
    day = _day_start(now)
    week = now - timedelta(days=7)
    month = _month_start(now)
    return {
        "today": _aggregate_window({"created_at": {"$gte": day}}),
        "last_7_days": _aggregate_window({"created_at": {"$gte": week}}),
        "current_month": _aggregate_window({"created_at": {"$gte": month}}),
        "month_resets_at": _next_month_start(now).isoformat(),
    }


def global_peak_hours(days: int = 30) -> List[Dict[str, Any]]:
    return peak_hours_for_key(None, days=days)


def summary_for_keys(key_hashes: List[str], hours: int = 24) -> Dict[str, Any]:
    """Aggregated hourly summary across a set of key hashes (e.g. one user's keys)."""
    if not key_hashes:
        return {
            "window_hours": hours,
            "total_requests": 0,
            "total_errors": 0,
            "error_rate_percent": 0.0,
            "avg_duration_ms": 0.0,
            "buckets": [],
        }
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    pipeline = [
        {"$match": {"key_hash": {"$in": key_hashes}, "created_at": {"$gte": since}}},
        {
            "$group": {
                "_id": "$bucket",
                "count": {"$sum": "$count"},
                "errors": {"$sum": "$errors"},
                "total_duration_ms": {"$sum": "$total_duration_ms"},
            }
        },
        {"$sort": {"_id": 1}},
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
                "bucket": d.get("_id"),
                "count": d.get("count", 0),
                "errors": d.get("errors", 0),
                "avg_duration_ms": round(d.get("total_duration_ms", 0) / d["count"], 2) if d.get("count") else 0,
            }
            for d in docs
        ],
    }


def dashboard_for_keys(key_hashes: List[str], hours: Optional[int] = None) -> Dict[str, Any]:
    """Today / 7d / month / optional custom window aggregates across multiple keys."""
    if not key_hashes:
        empty = {"requests": 0, "errors": 0, "avg_ms": 0.0, "error_rate_percent": 0.0}
        return {
            "today": empty,
            "last_7_days": empty,
            "current_month": empty,
            "window": empty if hours else None,
            "window_hours": hours,
            "buckets": [],
            "month_resets_at": _next_month_start().isoformat(),
        }
    now = datetime.now(timezone.utc)
    day = _day_start(now)
    week = now - timedelta(days=7)
    month = _month_start(now)
    base = {"key_hash": {"$in": key_hashes}}

    today = _aggregate_window({**base, "created_at": {"$gte": day}})
    last_7 = _aggregate_window({**base, "created_at": {"$gte": week}})
    current_month = _aggregate_window({**base, "created_at": {"$gte": month}})

    window_stats = None
    buckets: List[Dict[str, Any]] = []
    if hours:
        summary = summary_for_keys(key_hashes, hours=hours)
        buckets = summary["buckets"]
        window_stats = {
            "requests": summary["total_requests"],
            "errors": summary["total_errors"],
            "avg_ms": summary["avg_duration_ms"],
            "error_rate_percent": summary["error_rate_percent"],
        }

    return {
        "today": today,
        "last_7_days": last_7,
        "current_month": current_month,
        "window": window_stats,
        "window_hours": hours,
        "buckets": buckets,
        "month_resets_at": _next_month_start(now).isoformat(),
    }


def peak_hours_for_keys(key_hashes: List[str], days: int = 30) -> List[Dict[str, Any]]:
    """Hour-of-day distribution aggregated across a list of key hashes."""
    days = max(1, min(days, 90))
    if not key_hashes:
        return [{"hour": h, "count": 0, "errors": 0} for h in range(24)]
    since = datetime.now(timezone.utc) - timedelta(days=days)
    pipeline = [
        {"$match": {"key_hash": {"$in": key_hashes}, "created_at": {"$gte": since}}},
        {
            "$group": {
                "_id": {"$substr": ["$bucket", 8, 2]},
                "count": {"$sum": "$count"},
                "errors": {"$sum": "$errors"},
            }
        },
    ]
    by_hour = {int(d["_id"]): d for d in _collection().aggregate(pipeline) if d.get("_id")}
    out: List[Dict[str, Any]] = []
    for h in range(24):
        d = by_hour.get(h)
        out.append({
            "hour": h,
            "count": d.get("count", 0) if d else 0,
            "errors": d.get("errors", 0) if d else 0,
        })
    return out


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
