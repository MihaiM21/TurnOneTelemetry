"""User-owned API key management (JWT-protected)."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from src.auth.dependencies import get_current_user
from src.core.security.api_keys import invalidate_key_cache
from src.core.security.rate_limiting import apply_tiered_limit, limits_for_tier
from src.repositories.api_key_usage import (
    dashboard_for_key,
    peak_hours_for_key,
    summary_for_key,
)
from src.repositories.api_keys import (
    create_api_key,
    find_by_id,
    list_for_owner,
    revoke,
)

router = APIRouter(prefix="/api/keys", tags=["Auth"])
me_router = APIRouter(prefix="/api/me", tags=["Auth"])


class CreateKeyRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)


@router.get("")
async def list_my_keys(user: dict = Depends(get_current_user)):
    keys = await run_in_threadpool(list_for_owner, user["id"])
    return {"keys": keys}


@router.post("", status_code=status.HTTP_201_CREATED)
@apply_tiered_limit("public")
async def create_my_key(
    request: Request,
    body: CreateKeyRequest,
    user: dict = Depends(get_current_user),
):
    """Mint a new API key for the current user.

    The ``raw_key`` is returned **once**; persist it client-side immediately
    because we only store its hash.
    """
    # Non-admin users always get standard tier — admins promote keys via
    # the admin endpoints.
    key = await run_in_threadpool(create_api_key, user["id"], body.label, "standard")
    return {
        "id": key["id"],
        "raw_key": key["raw_key"],
        "key_prefix": key["key_prefix"],
        "tier": key["tier"],
        "label": key["label"],
        "created_at": key["created_at"],
        "warning": "Store this key now — it will not be shown again.",
    }


@router.get("/{key_id}/usage")
async def get_my_key_usage(
    key_id: str,
    hours: int = Query(24, ge=1, le=720),
    user: dict = Depends(get_current_user),
):
    """Return hourly usage stats for one of the current user's API keys."""
    existing = await run_in_threadpool(find_by_id, key_id)
    if not existing or str(existing.get("owner_id")) != user["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    key_hash = existing.get("key_hash")
    if not key_hash:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    stats = await run_in_threadpool(summary_for_key, key_hash, hours)
    return {
        "key_id": key_id,
        "key_prefix": existing.get("key_prefix"),
        "label": existing.get("label"),
        **stats,
    }


def _build_quota(tier: str, used_this_month: int, month_resets_at: str) -> dict:
    limits = limits_for_tier(tier)
    monthly_limit = limits["monthly_quota"]
    remaining = max(0, monthly_limit - used_this_month)
    percent = round(min(100.0, (used_this_month / monthly_limit * 100)), 2) if monthly_limit else 0.0
    return {
        "monthly_limit": monthly_limit,
        "used_this_month": used_this_month,
        "remaining": remaining,
        "percent_used": percent,
        "resets_at": month_resets_at,
    }


@router.get("/{key_id}/dashboard")
async def get_my_key_dashboard(
    key_id: str,
    user: dict = Depends(get_current_user),
):
    """Dashboard summary for one of the current user's API keys.

    Returns today/7d/month totals, quota usage, peak UTC hour, and the
    last 24h hourly time-series for charting.
    """
    existing = await run_in_threadpool(find_by_id, key_id)
    if not existing or str(existing.get("owner_id")) != user["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    key_hash = existing.get("key_hash")
    if not key_hash:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")

    tier = existing.get("tier") or "standard"
    stats = await run_in_threadpool(dashboard_for_key, key_hash)
    peak = await run_in_threadpool(peak_hours_for_key, key_hash, 30)
    peak_entry = max(peak, key=lambda r: r["count"]) if peak else {"hour": 0, "count": 0}

    used_month = stats["current_month"]["requests"]
    quota = _build_quota(tier, used_month, stats["month_resets_at"])
    rate_limit = limits_for_tier(tier)

    return {
        "key": {
            "id": key_id,
            "prefix": existing.get("key_prefix"),
            "label": existing.get("label"),
            "tier": tier,
        },
        "quota": quota,
        "rate_limit": {
            "per_minute": rate_limit["per_minute"],
            "per_hour": rate_limit["per_hour"],
        },
        "usage": {
            "today": stats["today"],
            "last_7_days": stats["last_7_days"],
            "current_month": stats["current_month"],
        },
        "peak_hour_utc": peak_entry,
        "last_24h": stats["last_24h"],
    }


@router.get("/{key_id}/peak-hours")
async def get_my_key_peak_hours(
    key_id: str,
    days: int = Query(30, ge=1, le=90),
    user: dict = Depends(get_current_user),
):
    """24-entry hour-of-day (UTC) request distribution over the window."""
    existing = await run_in_threadpool(find_by_id, key_id)
    if not existing or str(existing.get("owner_id")) != user["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    key_hash = existing.get("key_hash")
    if not key_hash:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    series = await run_in_threadpool(peak_hours_for_key, key_hash, days)
    peak = max(series, key=lambda r: r["count"]) if series else {"hour": 0, "count": 0}
    return {"key_id": key_id, "days": days, "peak_hour_utc": peak, "by_hour": series}


@me_router.get("/usage")
async def get_my_usage(user: dict = Depends(get_current_user)):
    """Aggregate dashboard across **all** active keys owned by the user.

    Sums today / 7d / month totals and lists per-key summaries so a single
    request powers a "my account" view.
    """
    serialized = await run_in_threadpool(list_for_owner, user["id"])

    totals = {
        "today": {"requests": 0, "errors": 0},
        "last_7_days": {"requests": 0, "errors": 0},
        "current_month": {"requests": 0, "errors": 0},
    }
    per_key = []
    month_resets_at = None
    tier_for_quota = "standard"

    for k in serialized:
        key_id = k.get("id")
        if k.get("revoked_at") or not key_id:
            continue
        raw = await run_in_threadpool(find_by_id, key_id)
        key_hash = raw.get("key_hash") if raw else None
        if not key_hash:
            continue
        stats = await run_in_threadpool(dashboard_for_key, key_hash)
        for window in ("today", "last_7_days", "current_month"):
            totals[window]["requests"] += stats[window]["requests"]
            totals[window]["errors"] += stats[window]["errors"]
        month_resets_at = stats["month_resets_at"]
        if k.get("tier") == "premium":
            tier_for_quota = "premium"
        per_key.append({
            "id": key_id,
            "prefix": k.get("key_prefix"),
            "label": k.get("label"),
            "tier": k.get("tier"),
            "today": stats["today"]["requests"],
            "current_month": stats["current_month"]["requests"],
        })

    quota = _build_quota(
        tier_for_quota,
        totals["current_month"]["requests"],
        month_resets_at or "",
    )
    return {
        "user_id": user["id"],
        "key_count": len(per_key),
        "totals": totals,
        "quota": quota,
        "keys": per_key,
    }


@router.delete("/{key_id}", status_code=status.HTTP_200_OK)
async def revoke_my_key(key_id: str, user: dict = Depends(get_current_user)):
    existing = await run_in_threadpool(find_by_id, key_id)
    if not existing or str(existing.get("owner_id")) != user["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    revoked = await run_in_threadpool(revoke, key_id, user["id"])
    if revoked and existing.get("key_hash"):
        invalidate_key_cache(existing["key_hash"])
    return {"id": key_id, "revoked": True}
