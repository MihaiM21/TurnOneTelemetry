"""Server-rendered admin dashboard at ``/admin``.

Authenticated via the same form-cookie flow as ``/docs`` so it works in any
browser without sending an API key. Data is fetched server-side from the
existing repositories — no client-side JS framework, no build step.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.core.config import settings
from src.core.observability.monitoring import get_request_tracker
from src.core.security.api_keys import invalidate_key_cache
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
        "admin/login.html",
        {"request": request, "error": error, "version": settings.app_version},
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
async def admin_dashboard(request: Request):
    if not _is_admin_session(request):
        return RedirectResponse("/admin/login", status_code=302)

    users = await run_in_threadpool(_users.list_users, 100, 0)
    keys = await run_in_threadpool(_keys.list_all, None, 200)
    top = await run_in_threadpool(_key_usage.top_keys, 24, 20)
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

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "version": settings.app_version,
            "summary": tracker.get_summary(),
            "users": users,
            "keys": keys,
            "top_usage": top_rows,
            "performance": perf_rows,
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
