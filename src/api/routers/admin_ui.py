"""Server-rendered admin dashboard at ``/admin``.

Authenticated via the same form-cookie flow as ``/docs`` so it works in any
browser without sending an API key. Data is fetched server-side from the
existing repositories — no client-side JS framework, no build step.

Hardened against scrapers and brute-force probes via ``src.api.admin_security``
(IP allowlist, per-IP rate limit, login lockout, CSRF, noindex headers).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin_security import (
    apply_no_index,
    check_login_allowed,
    compare_cookie,
    csrf_token_for,
    enforce_ip_allowlist,
    enforce_ui_rate_limit,
    record_login_failure,
    record_login_success,
    verify_csrf,
)
from src.core.config import settings
from src.core.logging import get_logger
from src.core.observability.monitoring import get_request_tracker
from src.core.security.api_keys import invalidate_key_cache
from src.core.security.rate_limiting import limits_for_tier
from src.repositories import api_key_usage as _key_usage
from src.repositories import api_keys as _keys
from src.repositories import raw_stream_cache, session_cache
from src.repositories import users as _users
from src.repositories.mongo import MongoDBManager
from src.services.admin_views import browse_stored_data, cache_inventory
from src.services.analysis.v2 import registry
from src.workers import plot_inventory

logger = get_logger(__name__)

_SESSION_CHOICES = ["FP1", "FP2", "FP3", "Q", "SQ", "S", "R"]


def _admin_gate(request: Request) -> None:
    """Cheap protection applied to every admin route before any DB work."""
    enforce_ip_allowlist(request)
    enforce_ui_rate_limit(request)


router = APIRouter(tags=["Admin UI"], dependencies=[Depends(_admin_gate)])

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_COOKIE = "t1api_admin_session"


def _is_admin_session(request: Request) -> bool:
    return compare_cookie(request.cookies.get(_COOKIE, ""), _expected_token())


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


def _csrf(request: Request) -> str:
    return csrf_token_for(request.cookies.get(_COOKIE, ""))


def _render(request: Request, template: str, context: dict) -> HTMLResponse:
    """Wrap TemplateResponse with no-index + CSRF in context."""
    context.setdefault("csrf_token", _csrf(request))
    resp = templates.TemplateResponse(request, template, context)
    return apply_no_index(resp)


def _is_prod_cookie() -> bool:
    return (settings.environment or "").lower() == "production"


def _set_session_cookie(resp, token: str) -> None:
    resp.set_cookie(
        _COOKIE,
        token,
        max_age=8 * 3600,
        httponly=True,
        samesite="strict",
        secure=_is_prod_cookie(),
        path="/",
    )


@router.get("/admin/login", include_in_schema=False)
async def admin_login_page(request: Request, error: bool = False):
    resp = templates.TemplateResponse(
        request,
        "admin/login.html",
        {"error": error, "version": settings.app_version},
    )
    return apply_no_index(resp)


@router.post("/admin/login", include_in_schema=False)
async def admin_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    check_login_allowed(request)
    if not _check_credentials(username, password):
        record_login_failure(request)
        resp = RedirectResponse("/admin/login?error=true", status_code=302)
        return apply_no_index(resp)
    record_login_success(request)
    resp = RedirectResponse("/admin", status_code=302)
    _set_session_cookie(resp, _expected_token())
    return apply_no_index(resp)


@router.get("/admin/logout", include_in_schema=False)
async def admin_logout():
    resp = RedirectResponse("/admin/login", status_code=302)
    resp.delete_cookie(_COOKIE, path="/")
    return apply_no_index(resp)


@router.get("/admin", include_in_schema=False)
async def admin_dashboard(request: Request, q: str = Query("", description="search users/keys", max_length=80)):
    if not _is_admin_session(request):
        return apply_no_index(RedirectResponse("/admin/login", status_code=302))

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

    # Resolve every key's hash in ONE threadpool hop rather than a round-trip
    # per row. The previous shape issued a find_active_by_hash per top-usage row
    # plus find_by_id + dashboard_for_key per key — up to ~400 sequential calls
    # for a 200-key install, which dominated the page's render time.
    def _key_meta_by_hash() -> dict:
        meta: dict = {}
        for key in keys:
            raw = _keys.find_by_id(key["id"])
            if raw and raw.get("key_hash"):
                meta[raw["key_hash"]] = key
        return meta

    meta_by_hash = await run_in_threadpool(_key_meta_by_hash)

    top_rows = []
    for row in top:
        meta = meta_by_hash.get(row["key_hash"])
        top_rows.append({
            **row,
            "key_prefix": meta.get("key_prefix") if meta else None,
            "label": meta.get("label") if meta else None,
            "tier": meta.get("tier") if meta else None,
        })

    # Quota-usage table: per active key, % of monthly cap used. One batched
    # aggregation instead of a dashboard_for_key call per key.
    active_hashes = [
        key_hash for key_hash, key in meta_by_hash.items() if not key.get("revoked_at")
    ]
    per_key_stats = await run_in_threadpool(
        _key_usage.dashboard_for_each_key, active_hashes
    )

    quota_rows = []
    for key_hash in active_hashes:
        key = meta_by_hash[key_hash]
        stats = per_key_stats.get(key_hash) or {"current_month": {"requests": 0}}
        tier = key.get("tier") or "standard"
        monthly_limit = limits_for_tier(tier)["monthly_quota"]
        used = stats["current_month"]["requests"]
        percent = round(min(100.0, (used / monthly_limit * 100)), 2) if monthly_limit else 0.0
        quota_rows.append({
            "key_prefix": key.get("key_prefix"),
            "label": key.get("label"),
            "tier": tier,
            "monthly_limit": monthly_limit,
            "used_this_month": used,
            "remaining": max(0, monthly_limit - used),
            "percent_used": percent,
        })
    quota_rows.sort(key=lambda r: r["percent_used"], reverse=True)

    return _render(
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
        return apply_no_index(RedirectResponse("/admin/login", status_code=302))

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

    return _render(
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
        return apply_no_index(RedirectResponse("/admin/login", status_code=302))

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

    return _render(
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


@router.get("/admin/plots", include_in_schema=False)
async def admin_plots(
    request: Request,
    year: int = Query(None, ge=2018, le=2030),
    gp: str = Query("", max_length=80),
    session: str = Query("", max_length=20),
):
    """Inventory of ungenerated V2 data, with a scoped, selectable backfill."""
    if not _is_admin_session(request):
        return apply_no_index(RedirectResponse("/admin/login", status_code=302))

    years = plot_inventory.available_years()
    if year is None:
        year = years[0] if years else None

    gp = (gp or "").strip()
    session = (session or "").strip().upper()

    events = await run_in_threadpool(plot_inventory.available_events, year) if year else []
    # Ignore a GP selection that isn't part of the chosen year (e.g. after
    # switching years) so the scope can't point at a stale/foreign event.
    if gp and gp not in {e["name"] for e in events}:
        gp = ""

    report = await run_in_threadpool(
        plot_inventory.compute_missing,
        year=year,
        identifier=gp or None,
        session=session or None,
    )
    season = await run_in_threadpool(plot_inventory.season_inventory, year) if year else None

    # The catalog drives the feature selector, so the UI never hardcodes a
    # feature list. Narrow it to the chosen session when there is one.
    catalog = (
        registry.catalog_for_session(session) if session else list(registry.FEATURE_CATALOG)
    )

    jobs = await run_in_threadpool(plot_inventory.list_jobs, 8, None)
    running = next(
        (j["job_id"] for j in jobs if j.get("status") in ("queued", "running")), None
    )

    return _render(
        request,
        "admin/plots.html",
        {
            "version": settings.app_version,
            "report": report,
            "season": season,
            "years": years,
            "events": events,
            "session_choices": _SESSION_CHOICES,
            "scope": {"year": year, "gp": gp, "session": session},
            "catalog": [entry.as_dict() for entry in catalog],
            "jobs": jobs,
            # Lets the page re-attach its progress bar after a refresh; before
            # this a reload orphaned any running job.
            "running_job_id": running,
        },
    )


def _selection_from_form(
    features: List[str],
    drivers: List[str],
    lap_from: Optional[int],
    lap_to: Optional[int],
) -> plot_inventory.Selection:
    return plot_inventory.Selection(
        features=[f for f in features if f] or None,
        drivers=[d.strip().upper() for d in drivers if d.strip()] or None,
        lap_from=lap_from,
        lap_to=lap_to,
    )


@router.get("/admin/plots/drivers", include_in_schema=False)
async def admin_plots_drivers(
    request: Request,
    year: int = Query(..., ge=2018, le=2030),
    gp: str = Query(..., max_length=80),
    session: str = Query(..., max_length=20),
    include_laps: bool = Query(False),
):
    """Driver list (and lap numbers) for the selector's pickers."""
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        drivers = await run_in_threadpool(registry.session_drivers, year, gp, session.upper())
        payload: dict = {"drivers": drivers}
        if include_laps:
            laps = await run_in_threadpool(
                registry.session_driver_laps, year, gp, session.upper()
            )
            all_laps = [lap for values in laps.values() for lap in values]
            payload["laps"] = laps
            payload["lap_range"] = (
                {"min": min(all_laps), "max": max(all_laps)} if all_laps else None
            )
        return apply_no_index(JSONResponse(payload))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not resolve drivers: {exc}")


@router.post("/admin/plots/estimate", include_in_schema=False)
async def admin_plots_estimate(
    request: Request,
    year: int = Form(None),
    gp: str = Form(""),
    session: str = Form(""),
    features: List[str] = Form([]),
    drivers: List[str] = Form([]),
    lap_from: Optional[int] = Form(None),
    lap_to: Optional[int] = Form(None),
):
    """Cost a selection without generating anything, for the live estimate panel."""
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    summary = await run_in_threadpool(
        plot_inventory.estimate_plan,
        year=year,
        identifier=(gp or "").strip() or None,
        session=(session or "").strip().upper() or None,
        selection=_selection_from_form(features, drivers, lap_from, lap_to),
    )
    return apply_no_index(JSONResponse(summary))


@router.post("/admin/plots/generate", include_in_schema=False)
async def admin_plots_generate(
    request: Request,
    year: int = Form(...),
    gp: str = Form(""),
    session: str = Form(""),
    force: bool = Form(False),
    include_comparisons: bool = Form(False),
    features: List[str] = Form([]),
    drivers: List[str] = Form([]),
    lap_from: Optional[int] = Form(None),
    lap_to: Optional[int] = Form(None),
    concurrency: int = Form(1),
    csrf_token: str = Form(...),
):
    """Start a background backfill job; returns JSON for the JS progress poller."""
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)

    identifier = (gp or "").strip() or None
    session_abbrev = (session or "").strip().upper() or None
    scope = {"year": year, "gp": identifier, "session": session_abbrev}

    conflict = await run_in_threadpool(plot_inventory.find_conflicting_job, scope)
    if conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Job {conflict['job_id']} is already running over this scope",
        )

    selection = _selection_from_form(features, drivers, lap_from, lap_to)
    job = plot_inventory.start_generation_job(
        year=year,
        identifier=identifier,
        session=session_abbrev,
        force=force,
        include_comparisons=include_comparisons,
        selection=selection if selection.features else None,
        concurrency=max(1, min(int(concurrency or 1), plot_inventory.MAX_CONCURRENCY)),
    )
    return apply_no_index(JSONResponse(job.as_dict()))


@router.get("/admin/plots/jobs/{job_id}/status", include_in_schema=False)
async def admin_plots_job_status(request: Request, job_id: str):
    """Session-protected job status for the live progress bar."""
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    job = await run_in_threadpool(plot_inventory.get_job_dict, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return apply_no_index(JSONResponse(job))


@router.post("/admin/plots/jobs/{job_id}/cancel", include_in_schema=False)
async def admin_plots_job_cancel(request: Request, job_id: str, csrf_token: str = Form(...)):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)
    if not await run_in_threadpool(plot_inventory.cancel_job, job_id):
        raise HTTPException(status_code=409, detail="Job is not cancellable")
    return apply_no_index(JSONResponse({"job_id": job_id, "cancel_requested": True}))


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
@router.get("/admin/jobs", include_in_schema=False)
async def admin_jobs_page(request: Request, status_filter: str = Query("", alias="status")):
    if not _is_admin_session(request):
        return apply_no_index(RedirectResponse("/admin/login", status_code=302))

    wanted = (status_filter or "").strip().lower() or None
    jobs = await run_in_threadpool(plot_inventory.list_jobs, 100, wanted)
    return _render(
        request,
        "admin/jobs.html",
        {"version": settings.app_version, "jobs": jobs, "status": wanted or ""},
    )


def _job_duration(job: dict) -> str:
    started, finished = job.get("started_at"), job.get("finished_at")
    if not started:
        return "—"
    try:
        start = datetime.fromisoformat(started)
        end = datetime.fromisoformat(finished) if finished else datetime.now(timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
    except ValueError:
        return "—"
    seconds = int((end - start).total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


@router.get("/admin/jobs/{job_id}", include_in_schema=False)
async def admin_job_detail(request: Request, job_id: str):
    if not _is_admin_session(request):
        return apply_no_index(RedirectResponse("/admin/login", status_code=302))

    job = await run_in_threadpool(plot_inventory.get_job_dict, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job.setdefault("scope", {})
    job.setdefault("errors", [])
    return _render(
        request,
        "admin/job_detail.html",
        {"version": settings.app_version, "job": job, "duration": _job_duration(job)},
    )


@router.post("/admin/jobs/{job_id}/cancel", include_in_schema=False)
async def admin_job_cancel(request: Request, job_id: str, csrf_token: str = Form(...)):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)
    await run_in_threadpool(plot_inventory.cancel_job, job_id)
    return apply_no_index(RedirectResponse(f"/admin/jobs/{job_id}", status_code=302))


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
@router.get("/admin/cache", include_in_schema=False)
async def admin_cache_page(request: Request, year: int = Query(None, ge=2018, le=2030)):
    if not _is_admin_session(request):
        return apply_no_index(RedirectResponse("/admin/login", status_code=302))

    inventory = await run_in_threadpool(cache_inventory, year)
    return _render(
        request,
        "admin/cache.html",
        {
            "version": settings.app_version,
            "inventory": inventory,
            "year": year,
            "years": plot_inventory.available_years(),
            "session_choices": _SESSION_CHOICES,
        },
    )


@router.post("/admin/cache/purge/raw", include_in_schema=False)
async def admin_cache_purge_raw(request: Request, session_key: str = Form(...),
                                csrf_token: str = Form(...)):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)
    await run_in_threadpool(raw_stream_cache.delete_raw_streams, session_key)
    return apply_no_index(RedirectResponse("/admin/cache", status_code=302))


@router.post("/admin/cache/purge/bundle", include_in_schema=False)
async def admin_cache_purge_bundle(request: Request, doc_id: str = Form(...),
                                   csrf_token: str = Form(...)):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)
    await run_in_threadpool(session_cache.delete_bundle, doc_id)
    return apply_no_index(RedirectResponse("/admin/cache", status_code=302))


@router.post("/admin/cache/prewarm", include_in_schema=False)
async def admin_cache_prewarm(request: Request, year: int = Form(...), gp: str = Form(...),
                              session: str = Form(...), csrf_token: str = Form(...)):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)
    from src.workers.processor import get_processor

    try:
        await run_in_threadpool(
            get_processor().prewarm_session_streams, year, gp, session.upper(), gp
        )
    except Exception as exc:  # surfaced on the page rather than as a 500
        logger.warning("Admin prewarm failed for %s %s %s: %s", year, gp, session, exc)
    return apply_no_index(RedirectResponse(f"/admin/cache?year={year}", status_code=302))


# --------------------------------------------------------------------------- #
# Stored data browser
# --------------------------------------------------------------------------- #
@router.get("/admin/data", include_in_schema=False)
async def admin_data_page(
    request: Request,
    year: int = Query(None, ge=2018, le=2030),
    gp: str = Query("", max_length=80),
    session: str = Query("", max_length=20),
):
    if not _is_admin_session(request):
        return apply_no_index(RedirectResponse("/admin/login", status_code=302))

    years = plot_inventory.available_years()
    if year is None:
        year = years[0] if years else datetime.now(timezone.utc).year

    session = (session or "").strip().upper()
    browse = await run_in_threadpool(
        browse_stored_data, year, gp.strip() or None, session or None
    )
    return _render(
        request,
        "admin/data.html",
        {
            "version": settings.app_version,
            "browse": browse,
            "years": years,
            "year": year,
            "gp": gp,
            "session": session,
            "session_choices": _SESSION_CHOICES,
        },
    )


@router.post("/admin/data/delete", include_in_schema=False)
async def admin_data_delete(
    request: Request,
    year: int = Form(...),
    gp_id: str = Form(...),
    session_type: str = Form(...),
    data_type: str = Form(...),
    csrf_token: str = Form(...),
):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)

    manager = MongoDBManager(year=year, version="v2")
    await run_in_threadpool(
        manager.delete_session_data, gp_id, session_type.upper(), data_type, year
    )
    logger.info("Admin UI deleted %s from %s %s (%s)", data_type, gp_id, session_type, year)
    return apply_no_index(RedirectResponse(f"/admin/data?year={year}", status_code=302))


# --------------------------------------------------------------------------- #
# Backups
# --------------------------------------------------------------------------- #
@router.get("/admin/backups", include_in_schema=False)
async def admin_backups_page(request: Request, tier: str = Query("daily", max_length=16)):
    if not _is_admin_session(request):
        return apply_no_index(RedirectResponse("/admin/login", status_code=302))

    tier = tier if tier in ("daily", "weekly", "monthly") else "daily"
    status_payload, backup_ids = await run_in_threadpool(_backup_overview, tier)
    return _render(
        request,
        "admin/backups.html",
        {
            "version": settings.app_version,
            "status": status_payload,
            "tier": tier,
            "backup_ids": backup_ids,
            "verify_report": None,
            "restore_report": None,
        },
    )


def _backup_overview(tier: str):
    """Scheduler/S3 status plus the backup ids in one tier. Fails soft: the page
    must still render when the subsystem is off or S3 is unreachable.
    """
    from src.core.config import settings as _settings

    status_payload = {
        "enabled": bool(_settings.backup_enabled),
        "scheduler_running": False,
        "manual_in_progress": False,
        "s3_ok": None,
    }
    backup_ids: List[str] = []
    if not _settings.backup_enabled:
        return status_payload, backup_ids

    try:
        from src.services.backup.scheduler import get_scheduler

        sched = get_scheduler()
        status_payload["scheduler_running"] = bool(sched.running)
        status_payload["manual_in_progress"] = bool(sched._manual_in_progress)
    except Exception as exc:
        logger.warning("Backup scheduler unavailable: %s", exc)

    try:
        from src.services.backup.storage import S3Storage

        storage = S3Storage()
        status_payload["s3_ok"] = bool(storage.ping())
        backup_ids = list(storage.list_backups(tier))
    except Exception as exc:
        logger.warning("Backup storage unavailable: %s", exc)
        status_payload["s3_ok"] = False

    return status_payload, backup_ids


@router.post("/admin/backups/run", include_in_schema=False)
async def admin_backups_run(request: Request, csrf_token: str = Form(...)):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)
    try:
        from src.services.backup.scheduler import get_scheduler

        get_scheduler().trigger_now()
    except Exception as exc:
        logger.error("Could not trigger backup: %s", exc)
    return apply_no_index(RedirectResponse("/admin/backups", status_code=302))


@router.post("/admin/backups/verify", include_in_schema=False)
async def admin_backups_verify(request: Request, backup_id: str = Form(...),
                               csrf_token: str = Form(...)):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)

    report = None
    try:
        from src.services.backup.restore import RestoreRunner

        result = await run_in_threadpool(lambda: RestoreRunner().verify(backup_id))
        report = {"backup_id": result.backup_id, "tier": result.tier,
                  "verified": list(result.verified)}
    except Exception as exc:
        logger.error("Backup verify failed for %s: %s", backup_id, exc)

    status_payload, backup_ids = await run_in_threadpool(_backup_overview, "daily")
    return _render(
        request,
        "admin/backups.html",
        {
            "version": settings.app_version,
            "status": status_payload,
            "tier": "daily",
            "backup_ids": backup_ids,
            "verify_report": report,
            "restore_report": None,
        },
    )


@router.post("/admin/backups/restore", include_in_schema=False)
async def admin_backups_restore(
    request: Request,
    backup_id: str = Form(...),
    mongo_target_db: str = Form(""),
    confirm_text: str = Form(""),
    csrf_token: str = Form(...),
):
    """Restore, with a drill target strongly preferred.

    Without a target database this overwrites live data, so it additionally
    requires the operator to type RESTORE — matching the API's
    ``confirm=true`` + ``i_understand=true`` double gate.
    """
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)

    target = (mongo_target_db or "").strip() or None
    if not target and confirm_text.strip().upper() != "RESTORE":
        raise HTTPException(
            status_code=400,
            detail="A destructive restore requires typing RESTORE, or set a drill target database.",
        )

    report = None
    try:
        from src.services.backup.restore import RestoreRunner

        result = await run_in_threadpool(
            lambda: RestoreRunner().restore(backup_id, mongo_target_db=target)
        )
        report = {
            "backup_id": result.backup_id,
            "restored": list(result.restored),
            "skipped": list(result.skipped),
            "mongo_target_db": target,
        }
    except Exception as exc:
        logger.error("Backup restore failed for %s: %s", backup_id, exc)
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}")

    status_payload, backup_ids = await run_in_threadpool(_backup_overview, "daily")
    return _render(
        request,
        "admin/backups.html",
        {
            "version": settings.app_version,
            "status": status_payload,
            "tier": "daily",
            "backup_ids": backup_ids,
            "verify_report": None,
            "restore_report": report,
        },
    )


# --------------------------------------------------------------------------- #
# Ops
# --------------------------------------------------------------------------- #
def _ops_health() -> dict:
    """Mongo + Redis reachability. Both checks fail soft to a boolean."""
    health = {"mongo": False, "redis": None}
    try:
        from src.repositories.mongo import test_connection

        health["mongo"] = bool(test_connection())
    except Exception as exc:
        logger.debug("Mongo health check failed: %s", exc)
    try:
        from src.core.cache.redis_cache import get_sync_cache

        cache = get_sync_cache()
        health["redis"] = bool(cache.enabled) if cache.enabled else None
    except Exception as exc:
        logger.debug("Redis health check failed: %s", exc)
        health["redis"] = False
    return health


def _ops_context(action_result: Optional[dict] = None) -> dict:
    from src.core.observability.monitoring import get_system_monitor
    from src.workers.processor import get_processor

    processor = get_processor()
    tracker = get_request_tracker()
    return {
        "version": settings.app_version,
        "environment": settings.environment,
        "processor": {
            "running": bool(getattr(processor, "running", False)),
            "check_interval": getattr(processor, "check_interval", 0),
            "processed_sessions_count": len(getattr(processor, "processed_sessions", []) or []),
            "processed_sessions": list(getattr(processor, "processed_sessions", []) or [])[-20:],
        },
        "system": get_system_monitor().get_metrics(),
        "health": _ops_health(),
        "summary": tracker.get_summary(),
        "recent_errors": tracker.get_recent_errors(20),
        "action_result": action_result,
    }


@router.get("/admin/ops", include_in_schema=False)
async def admin_ops_page(request: Request):
    if not _is_admin_session(request):
        return apply_no_index(RedirectResponse("/admin/login", status_code=302))
    context = await run_in_threadpool(_ops_context, None)
    return _render(request, "admin/ops.html", context)


@router.post("/admin/ops/process-latest", include_in_schema=False)
async def admin_ops_process_latest(request: Request, csrf_token: str = Form(...)):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)

    from src.workers.processor import get_processor

    try:
        result = await get_processor().force_process_latest()
    except Exception as exc:
        logger.error("Force-process failed: %s", exc)
        result = {"status": "error", "error": str(exc)}
    context = await run_in_threadpool(_ops_context, result)
    return _render(request, "admin/ops.html", context)


@router.post("/admin/ops/ensure-indexes", include_in_schema=False)
async def admin_ops_ensure_indexes(request: Request, csrf_token: str = Form(...)):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)

    from src.repositories.mongo import ensure_indexes

    try:
        created = await run_in_threadpool(ensure_indexes)
    except Exception as exc:
        logger.error("ensure_indexes failed: %s", exc)
        created = {"error": str(exc)}
    context = await run_in_threadpool(_ops_context, created)
    return _render(request, "admin/ops.html", context)


@router.post("/admin/keys/{key_id}/revoke", include_in_schema=False)
async def admin_ui_revoke(request: Request, key_id: str, csrf_token: str = Form(...)):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)
    existing = await run_in_threadpool(_keys.find_by_id, key_id)
    if existing:
        await run_in_threadpool(_keys.revoke, key_id, None)
        if existing.get("key_hash"):
            invalidate_key_cache(existing["key_hash"])
    return apply_no_index(RedirectResponse("/admin", status_code=302))


@router.post("/admin/users/{user_id}/promote", include_in_schema=False)
async def admin_ui_promote(request: Request, user_id: str, csrf_token: str = Form(...)):
    if not _is_admin_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    verify_csrf(request, csrf_token)
    try:
        await run_in_threadpool(_users.set_admin, user_id, True)
    except Exception as exc:
        # A failed promotion must be visible, not silently swallowed.
        logger.error("Could not promote user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Could not promote user")
    return apply_no_index(RedirectResponse("/admin", status_code=302))
