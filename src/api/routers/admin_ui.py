"""Server-rendered admin dashboard at ``/admin``.

Authenticated via the same form-cookie flow as ``/docs`` so it works in any
browser without sending an API key. Data is fetched server-side from the
existing repositories — no client-side JS framework, no build step.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.core.config import settings
from src.core.observability.monitoring import get_request_tracker
from src.core.security.api_keys import invalidate_key_cache
from src.core.security.rate_limiting import limits_for_tier
from src.repositories import api_key_usage as _key_usage
from src.repositories import api_keys as _keys
from src.repositories import users as _users

router = APIRouter(tags=["Admin UI"])

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_COOKIE = "t1api_admin_session"


def _is_admin_session(request: Request) -> bool:
    return request.cookies.get(_COOKIE) == _expected_token()


def _expected_token() -> str:
    # Derive a stable per-process token from the JWT secret. This is fine for
    # a small operator-only dashboard; rotating the secret invalidates it.
    import hashlib

    secret = settings.jwt_secret_key or settings.api_secret_key or "dev"
    return hashlib.sha256(f"admin-ui::{secret}".encode()).hexdigest()


def _check_credentials(username: str, password: str) -> bool:
    expected_user = settings.admin_email or settings.docs_username
    expected_pass = settings.admin_password or settings.docs_password
    if not expected_user or not expected_pass:
        return False
    import secrets as _secrets

    return _secrets.compare_digest(username, expected_user) and _secrets.compare_digest(
        password, expected_pass
    )


@router.get("/admin/login", include_in_schema=False)
async def admin_login_page(request: Request, error: bool = False):
    return templates.TemplateResponse(
        request,
        "admin/login.html",
        {"error": error, "version": settings.app_version},
    )


@router.post("/admin/login", include_in_schema=False)
async def admin_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if not _check_credentials(username, password):
        return RedirectResponse("/admin/login?error=true", status_code=302)
    resp = RedirectResponse("/admin", status_code=302)
    resp.set_cookie(_COOKIE, _expected_token(), max_age=8 * 3600, httponly=True, samesite="lax")
    return resp


@router.get("/admin/logout", include_in_schema=False)
async def admin_logout():
    resp = RedirectResponse("/admin/login", status_code=302)
    resp.delete_cookie(_COOKIE)
    return resp


@router.get("/admin", include_in_schema=False)
async def admin_dashboard(request: Request, q: str = Query("", description="search users/keys")):
    if not _is_admin_session(request):
        return RedirectResponse("/admin/login", status_code=302)

    users = await run_in_threadpool(_users.list_users, 100, 0)
    keys = await run_in_threadpool(_keys.list_all, None, 200)

    query = (q or "").strip().lower()
    if query:
        users = [u for u in users if query in (u.get("email") or "").lower()]
        keys = [
            k for k in keys
            if query in (k.get("key_prefix") or "").lower()
            or query in (k.get("label") or "").lower()
        ]
    top = await run_in_threadpool(_key_usage.top_keys, 24, 20)
    global_stats = await run_in_threadpool(_key_usage.global_dashboard)
    peak_series = await run_in_threadpool(_key_usage.global_peak_hours, 30)
    peak_max = max((r["count"] for r in peak_series), default=0)
    peak_hour = max(peak_series, key=lambda r: r["count"]) if peak_series else {"hour": 0, "count": 0}

    tracker = get_request_tracker()
    perf = tracker.get_endpoint_stats()
    perf_rows = sorted(
        (
            {"endpoint": ep, **stats}
            for ep, stats in perf.items()
        ),
        key=lambda r: r["total_requests"],
        reverse=True,
    )[:30]

    # Decorate top-usage rows with key metadata.
    top_rows = []
    for row in top:
        meta = await run_in_threadpool(_keys.find_active_by_hash, row["key_hash"])
        top_rows.append({
            **row,
            "key_prefix": meta.get("key_prefix") if meta else None,
            "label": meta.get("label") if meta else None,
            "tier": meta.get("tier") if meta else None,
        })

    # Quota-usage table: per active key, % of monthly cap used.
    quota_rows = []
    for k in keys:
        if k.get("revoked_at"):
            continue
        raw = await run_in_threadpool(_keys.find_by_id, k["id"])
        key_hash = raw.get("key_hash") if raw else None
        if not key_hash:
            continue
        stats = await run_in_threadpool(_key_usage.dashboard_for_key, key_hash)
        tier = k.get("tier") or "standard"
        monthly_limit = limits_for_tier(tier)["monthly_quota"]
        used = stats["current_month"]["requests"]
        percent = round(min(100.0, (used / monthly_limit * 100)), 2) if monthly_limit else 0.0
        quota_rows.append({
            "key_prefix": k.get("key_prefix"),
            "label": k.get("label"),
            "tier": tier,
            "monthly_limit": monthly_limit,
            "used_this_month": used,
            "remaining": max(0, monthly_limit - used),
            "percent_used": percent,
        })
    quota_rows.sort(key=lambda r: r["percent_used"], reverse=True)

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "version": settings.app_version,
            "summary": tracker.get_summary(),
            "users": users,
            "keys": keys,
            "top_usage": top_rows,
            "performance": perf_rows,
            "global_stats": global_stats,
            "peak_series": peak_series,
            "peak_max": peak_max,
            "peak_hour": peak_hour,
            "quota_rows": quota_rows,
            "q": q or "",
        },
    )


def _resolve_window_label(hours: int) -> str:
    if hours <= 24:
        return f"Last {hours}h"
    days = hours // 24
    return f"Last {days}d"


@router.get("/admin/users/{user_id}", include_in_schema=False)
async def admin_user_detail(request: Request, user_id: str, hours: int = Query(24, ge=1, le=2160)):
    if not _is_admin_session(request):
        return RedirectResponse("/admin/login", status_code=302)

    user = await run_in_threadpool(_users.find_user_by_id, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    serialized_keys = await run_in_threadpool(_keys.list_all, user_id, 500)

    key_rows = []
    key_hashes = []
    for k in serialized_keys:
        raw = await run_in_threadpool(_keys.find_by_id, k["id"])
        key_hash = raw.get("key_hash") if raw else None
        if not key_hash:
            continue
        per_key = await run_in_threadpool(_key_usage.dashboard_for_key, key_hash)
        key_rows.append({
            "id": k["id"],
            "key_prefix": k.get("key_prefix"),
            "label": k.get("label"),
            "tier": k.get("tier"),
            "revoked_at": k.get("revoked_at"),
            "created_at": k.get("created_at"),
            "last_used_at": k.get("last_used_at"),
            "today": per_key["today"],
            "last_7_days": per_key["last_7_days"],
            "current_month": per_key["current_month"],
        })
        if not k.get("revoked_at"):
            key_hashes.append(key_hash)

    aggregate = await run_in_threadpool(_key_usage.dashboard_for_keys, key_hashes, hours)
    peak_series = await run_in_threadpool(_key_usage.peak_hours_for_keys, key_hashes, 30)
    peak_max = max((r["count"] for r in peak_series), default=0)
    peak_hour = max(peak_series, key=lambda r: r["count"]) if peak_series else {"hour": 0, "count": 0}

    bucket_max = max((b["count"] for b in aggregate.get("buckets", [])), default=0)

    return templates.TemplateResponse(
        request,
        "admin/user_detail.html",
        {
            "version": settings.app_version,
            "user": user,
            "keys": key_rows,
            "aggregate": aggregate,
            "hours": hours,
            "window_label": _resolve_window_label(hours),
            "peak_series": peak_series,
            "peak_max": peak_max,
            "peak_hour": peak_hour,
            "bucket_max": bucket_max,
        },
    )


@router.get("/admin/keys/{key_id}/analytics", include_in_schema=False)
async def admin_key_analytics(request: Request, key_id: str, hours: int = Query(24, ge=1, le=2160)):
    if not _is_admin_session(request):
        return RedirectResponse("/admin/login", status_code=302)

    doc = await run_in_threadpool(_keys.find_by_id, key_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Key not found")
    key_hash = doc["key_hash"]

    dashboard = await run_in_threadpool(_key_usage.dashboard_for_key, key_hash)
    summary = await run_in_threadpool(_key_usage.summary_for_key, key_hash, hours)
    peak_series = await run_in_threadpool(_key_usage.peak_hours_for_key, key_hash, 30)
    owner = await run_in_threadpool(_users.find_user_by_id, str(doc.get("owner_id")))

    tier = doc.get("tier") or "standard"
    monthly_limit = limits_for_tier(tier)["monthly_quota"]
    used = dashboard["current_month"]["requests"]
    percent = round(min(100.0, (used / monthly_limit * 100)), 2) if monthly_limit else 0.0

    peak_max = max((r["count"] for r in peak_series), default=0)
    peak_hour = max(peak_series, key=lambda r: r["count"]) if peak_series else {"hour": 0, "count": 0}
    bucket_max = max((b["count"] for b in summary.get("buckets", [])), default=0)

    return templates.TemplateResponse(
        request,
        "admin/key_analytics.html",
        {
            "version": settings.app_version,
            "key": {
                "id": key_id,
                "key_prefix": doc.get("key_prefix"),
                "label": doc.get("label"),
                "tier": tier,
                "created_at": doc.get("created_at"),
                "last_used_at": doc.get("last_used_at"),
                "revoked_at": doc.get("revoked_at"),
                "owner_id": str(doc.get("owner_id")),
            },
            "owner": owner,
            "dashboard": dashboard,
            "summary": summary,
            "hours": hours,
            "window_label": _resolve_window_label(hours),
            "peak_series": peak_series,
            "peak_max": peak_max,
            "peak_hour": peak_hour,
            "bucket_max": bucket_max,
            "quota": {
                "monthly_limit": monthly_limit,
                "used_this_month": used,
                "remaining": max(0, monthly_limit - used),
                "percent_used": percent,
            },
        },
    )


@router.post("/admin/keys/{key_id}/revoke", include_in_schema=False)
async def admin_ui_revoke(request: Request, key_id: str):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    existing = await run_in_threadpool(_keys.find_by_id, key_id)
    if existing:
        await run_in_threadpool(_keys.revoke, key_id, None)
        if existing.get("key_hash"):
            invalidate_key_cache(existing["key_hash"])
    return RedirectResponse("/admin", status_code=302)


@router.post("/admin/users/{user_id}/promote", include_in_schema=False)
async def admin_ui_promote(request: Request, user_id: str):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    import os
    from bson import ObjectId
    from src.repositories.mongo import get_mongo_client

    db = get_mongo_client()[os.getenv("MONGODB_DATABASE", "T1API_DB")]
    try:
        db["users"].update_one({"_id": ObjectId(user_id)}, {"$set": {"is_admin": True}})
    except Exception:
        pass
    return RedirectResponse("/admin", status_code=302)
