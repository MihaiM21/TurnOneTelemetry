"""Defensive helpers for the admin dashboard.

Keep scrapers and brute-force attackers out, and prevent the admin UI from
exposing expensive endpoints to anonymous CPU abuse:

- ``NO_INDEX_HEADERS`` — applied to every admin response so search engines
  and well-behaved crawlers skip the surface.
- ``apply_no_index`` — convenience to stamp those headers onto a Response.
- IP allowlist (``settings.admin_ip_allowlist``) — when set, only listed IPs
  may reach any /admin route.
- Per-IP UI rate limit (in-memory token bucket) — caps requests/min to /admin
  pages so unauthenticated probes can't fan out into per-key Mongo queries.
- Login brute-force lockout (per-IP) — N failed attempts → lockout window.
- Session tokens (``create_session_token`` / ``verify_session_token``) — a
  signed, expiring, per-login token (mirrors ``src/api/docs_auth.py``'s
  scheme), so every login gets a distinct cookie and logout/expiry are
  meaningful.
- CSRF token (session-bound) — required on every state-changing admin POST.
- ``compare_cookie`` — constant-time string compare, kept as a general
  utility for callers that need one.

``client_ip`` only honours ``X-Forwarded-For`` when the deployment has
explicitly opted in (``settings.trust_x_forwarded_for``) and, if a trusted
proxy list is configured, only when the direct TCP peer is one of those
proxies. Otherwise it is trivial to spoof the header and walk past the IP
allowlist, the UI rate limit and the login lockout in one shot.
"""
from __future__ import annotations

import hashlib
import hmac
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
    """Resolve the address that gates the IP allowlist, UI rate limit and
    login lockout.

    ``X-Forwarded-For`` is attacker-controlled by default, so it is only
    trusted when the operator has explicitly said a reverse proxy rewrites
    it (``trust_x_forwarded_for``) and, when a trusted-proxy list is also
    configured, only when the direct TCP peer is one of those proxies.
    Otherwise the direct peer address is used, which cannot be spoofed.
    """
    peer = request.client.host if request.client else "unknown"
    if not settings.trust_x_forwarded_for:
        return peer
    fwd = request.headers.get("x-forwarded-for")
    if not fwd:
        return peer
    trusted = settings.trusted_proxy_ips_list
    if trusted and peer not in trusted:
        return peer
    return fwd.split(",")[0].strip()


# ---------------------------------------------------------------------------
# IP allowlist
# ---------------------------------------------------------------------------

def enforce_ip_allowlist(request: Request) -> None:
    allowed = settings.admin_ip_allowlist_list
    if not allowed:
        return
    if client_ip(request) not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


# ---------------------------------------------------------------------------
# Per-IP rate limiting for admin UI (in-memory sliding window)
# ---------------------------------------------------------------------------

_ui_hits: Dict[str, Deque[float]] = {}
_ui_lock = Lock()


def enforce_ui_rate_limit(request: Request) -> None:
    """Cheap per-IP sliding-window limiter to protect expensive admin views."""
    ip = client_ip(request)
    now = time.monotonic()
    cutoff = now - 60.0
    limit = settings.admin_ui_rate_per_minute
    with _ui_lock:
        dq = _ui_hits.setdefault(ip, deque())
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
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
    max_attempts = settings.admin_login_max_attempts
    lockout_seconds = settings.admin_login_lockout_seconds
    window_seconds = settings.admin_login_window_seconds
    with _login_lock:
        count, first_ts, locked_until = _login_attempts.get(ip, (0, now, 0.0))
        # Reset window if too old.
        if now - first_ts > window_seconds:
            count, first_ts = 0, now
        count += 1
        if count >= max_attempts:
            locked_until = now + lockout_seconds
            count, first_ts = 0, now  # reset counter; lockout active
        _login_attempts[ip] = (count, first_ts, locked_until)


def record_login_success(request: Request) -> None:
    ip = client_ip(request)
    with _login_lock:
        _login_attempts.pop(ip, None)


# ---------------------------------------------------------------------------
# Session tokens (signed, expiring, per-login)
# ---------------------------------------------------------------------------
# Mirrors src/api/docs_auth.py's ``ts.nonce.sig`` scheme: a random nonce plus
# an issued-at timestamp, HMAC-SHA256 signed over settings.session_signing_secret
# and rejected once older than SESSION_TOKEN_TTL_SECONDS. Unlike the previous
# implementation (a single sha256 hash of the secret, identical for every
# login forever), every call to create_session_token() returns a distinct
# value: logins are distinguishable, logout is meaningful, and expiry is
# actually enforced rather than being a client-side cookie hint the server
# never checks.

SESSION_TOKEN_TTL_SECONDS = 8 * 3600


def _sign_payload(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def create_session_token() -> str:
    """Mint a fresh, random, expiring admin session token.

    Raises if no signing secret is configured rather than falling back to a
    guessable constant — an admin session must never be issued unsigned.
    """
    secret = settings.session_signing_secret
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin session signing secret is not configured",
        )
    ts = str(int(time.time()))
    nonce = secrets.token_urlsafe(32)
    payload = f"{ts}.{nonce}"
    return f"{payload}.{_sign_payload(payload, secret)}"


def verify_session_token(token: Optional[str]) -> bool:
    """Verify a token minted by ``create_session_token``.

    Fails closed: no configured secret, a missing/malformed token, a bad
    signature, or a token older than ``SESSION_TOKEN_TTL_SECONDS`` are all
    treated as "not authenticated".
    """
    secret = settings.session_signing_secret
    if not secret or not token:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    ts, nonce, sig = parts
    payload = f"{ts}.{nonce}"
    if not hmac.compare_digest(sig, _sign_payload(payload, secret)):
        return False
    try:
        issued_at = int(ts)
    except ValueError:
        return False
    return (time.time() - issued_at) <= SESSION_TOKEN_TTL_SECONDS


# ---------------------------------------------------------------------------
# CSRF token (session-bound, stateless)
# ---------------------------------------------------------------------------

def _csrf_secret() -> bytes:
    secret = settings.session_signing_secret
    if not secret:
        return b""
    return f"admin-csrf::{secret}".encode()


def csrf_token_for(session_cookie: str) -> str:
    """Derive a deterministic CSRF token from the session cookie + secret.

    Stateless (no server-side store); rotates whenever the session does
    since the cookie itself is now per-login. Returns "" when there is no
    cookie or no configured signing secret, which verify_csrf always treats
    as invalid.
    """
    if not session_cookie:
        return ""
    secret = _csrf_secret()
    if not secret:
        return ""
    return hmac.new(secret, session_cookie.encode(), hashlib.sha256).hexdigest()


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
