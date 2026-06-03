"""Defensive helpers for the admin dashboard.

Keep scrapers and brute-force attackers out, and prevent the admin UI from
exposing expensive endpoints to anonymous CPU abuse:

- ``NO_INDEX_HEADERS`` — applied to every admin response so search engines
  and well-behaved crawlers skip the surface.
- ``apply_no_index`` — convenience to stamp those headers onto a Response.
- IP allowlist (env: ``ADMIN_IP_ALLOWLIST``) — when set, only listed IPs may
  reach any /admin route.
- Per-IP UI rate limit (in-memory token bucket) — caps requests/min to /admin
  pages so unauthenticated probes can't fan out into per-key Mongo queries.
- Login brute-force lockout (per-IP) — N failed attempts → lockout window.
- CSRF token (session-bound) — required on every state-changing admin POST.
- ``compare_cookie`` — constant-time session-cookie compare.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import deque
from threading import Lock
from typing import Deque, Dict, Optional, Tuple

from fastapi import HTTPException, Request, status
from fastapi.responses import Response

from src.core.config import settings


NO_INDEX_HEADERS = {
    "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
    "Cache-Control": "no-store, max-age=0",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
}


def apply_no_index(response: Response) -> Response:
    for k, v in NO_INDEX_HEADERS.items():
        response.headers[k] = v
    return response


def client_ip(request: Request) -> str:
    # X-Forwarded-For first hop wins when present (reverse proxy deployments).
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# IP allowlist
# ---------------------------------------------------------------------------

def _allowlist() -> set[str]:
    raw = os.getenv("ADMIN_IP_ALLOWLIST", "").strip()
    if not raw:
        return set()
    return {p.strip() for p in raw.split(",") if p.strip()}


def enforce_ip_allowlist(request: Request) -> None:
    allowed = _allowlist()
    if not allowed:
        return
    if client_ip(request) not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


# ---------------------------------------------------------------------------
# Per-IP rate limiting for admin UI (in-memory sliding window)
# ---------------------------------------------------------------------------

_UI_RATE_PER_MINUTE = int(os.getenv("ADMIN_UI_RATE_PER_MINUTE", "60"))
_ui_hits: Dict[str, Deque[float]] = {}
_ui_lock = Lock()


def enforce_ui_rate_limit(request: Request) -> None:
    """Cheap per-IP sliding-window limiter to protect expensive admin views."""
    ip = client_ip(request)
    now = time.monotonic()
    cutoff = now - 60.0
    with _ui_lock:
        dq = _ui_hits.setdefault(ip, deque())
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= _UI_RATE_PER_MINUTE:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests",
                headers={"Retry-After": "60"},
            )
        dq.append(now)
        # Opportunistic cleanup so the dict doesn't grow without bound.
        if len(_ui_hits) > 1024:
            for stale_ip in list(_ui_hits.keys())[:256]:
                if not _ui_hits[stale_ip] or _ui_hits[stale_ip][-1] < cutoff:
                    _ui_hits.pop(stale_ip, None)


# ---------------------------------------------------------------------------
# Login brute-force lockout (per-IP)
# ---------------------------------------------------------------------------

_LOGIN_MAX_ATTEMPTS = int(os.getenv("ADMIN_LOGIN_MAX_ATTEMPTS", "5"))
_LOGIN_LOCKOUT_SECONDS = int(os.getenv("ADMIN_LOGIN_LOCKOUT_SECONDS", "900"))
_LOGIN_WINDOW_SECONDS = int(os.getenv("ADMIN_LOGIN_WINDOW_SECONDS", "600"))

_login_attempts: Dict[str, Tuple[int, float, float]] = {}  # ip -> (count, first_ts, locked_until)
_login_lock = Lock()


def check_login_allowed(request: Request) -> None:
    ip = client_ip(request)
    now = time.time()
    with _login_lock:
        rec = _login_attempts.get(ip)
        if rec and rec[2] > now:
            retry = int(rec[2] - now)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed attempts",
                headers={"Retry-After": str(retry)},
            )


def record_login_failure(request: Request) -> None:
    ip = client_ip(request)
    now = time.time()
    with _login_lock:
        count, first_ts, locked_until = _login_attempts.get(ip, (0, now, 0.0))
        # Reset window if too old.
        if now - first_ts > _LOGIN_WINDOW_SECONDS:
            count, first_ts = 0, now
        count += 1
        if count >= _LOGIN_MAX_ATTEMPTS:
            locked_until = now + _LOGIN_LOCKOUT_SECONDS
            count, first_ts = 0, now  # reset counter; lockout active
        _login_attempts[ip] = (count, first_ts, locked_until)


def record_login_success(request: Request) -> None:
    ip = client_ip(request)
    with _login_lock:
        _login_attempts.pop(ip, None)


# ---------------------------------------------------------------------------
# CSRF token (session-bound, stateless)
# ---------------------------------------------------------------------------

def _csrf_secret() -> bytes:
    base = settings.jwt_secret_key or settings.api_secret_key or "dev"
    return f"admin-csrf::{base}".encode()


def csrf_token_for(session_cookie: str) -> str:
    """Derive a deterministic CSRF token from the session cookie + secret.

    Stateless (no server-side store) and rotates whenever the session does.
    """
    if not session_cookie:
        return ""
    return hmac.new(_csrf_secret(), session_cookie.encode(), hashlib.sha256).hexdigest()


def verify_csrf(request: Request, submitted: Optional[str]) -> None:
    cookie = request.cookies.get("t1api_admin_session", "")
    expected = csrf_token_for(cookie)
    if not submitted or not expected or not hmac.compare_digest(submitted, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")


# ---------------------------------------------------------------------------
# Constant-time cookie compare
# ---------------------------------------------------------------------------

def compare_cookie(submitted: str, expected: str) -> bool:
    if not submitted or not expected:
        return False
    return secrets.compare_digest(submitted, expected)
