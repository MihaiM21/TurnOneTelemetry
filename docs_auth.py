"""
docs_auth.py - Custom form-based authentication for /docs, /redoc, /openapi.json
Drop-in replacement for HTTP Basic Auth. Works in all modern browsers including Chrome 146+.

Usage in main.py:
    from docs_auth import setup_docs_auth
    setup_docs_auth(app, settings, SWAGGER_UI_PARAMETERS)

Remove the old Basic Auth code:
  - docs_basic_auth, docs_auth_required(), verify_docs_access()
  - The /docs, /redoc, /openapi.json endpoints
"""

import hashlib
import hmac
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html

COOKIE_NAME = "t1api_docs_session"
SESSION_TTL = 8 * 3600  # 8 hours
FAVICON_PATH = Path(__file__).resolve().parent / "lib" / "logo mic.png"


# ── Token helpers ─────────────────────────────────────────────────────────────

def _sign(value: str, secret: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def _make_token(secret: str) -> str:
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    payload = f"{ts}.{nonce}"
    return f"{payload}.{_sign(payload, secret)}"


def _verify_token(token: str, secret: str) -> bool:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False
        ts, nonce, sig = parts
        payload = f"{ts}.{nonce}"
        if not hmac.compare_digest(sig, _sign(payload, secret)):
            return False
        if time.time() - int(ts) > SESSION_TTL:
            return False
        return True
    except Exception:
        return False


def _is_authenticated(request: Request, secret: str) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    return bool(token and _verify_token(token, secret))


# ── Login page ────────────────────────────────────────────────────────────────

def _login_page(error: bool = False, app_version: str = "2.0.0") -> str:
    error_block = ""
    if error:
        error_block = """
        <div class="error-box">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="8" x2="12" y2="12"/>
            <line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
          Invalid credentials — access denied
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>T1API — Internal Access</title>
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="shortcut icon" href="/favicon.ico">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:ital,wght@0,400;0,600;0,700;1,700&family=Barlow:wght@400;500&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
  --red: #E8002D;
  --red-dim: rgba(232,0,45,0.15);
  --red-glow: rgba(232,0,45,0.06);
  --bg: #0C0C0E;
  --bg2: #131316;
  --bg3: #1A1A1E;
  --border: rgba(255,255,255,0.06);
  --border-hover: rgba(255,255,255,0.12);
  --text: #F0F0F2;
  --muted: rgba(240,240,242,0.4);
  --font-hero: 'Barlow Condensed', sans-serif;
  --font-body: 'Barlow', sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
}}
html, body {{ min-height: 100vh; background: var(--bg); color: var(--text); font-family: var(--font-body); overflow-x: hidden; }}
body::before {{
  content: '';
  position: fixed; inset: 0;
  background:
    repeating-linear-gradient(90deg, transparent, transparent 119px, rgba(255,255,255,0.018) 119px, rgba(255,255,255,0.018) 120px),
    repeating-linear-gradient(0deg, transparent, transparent 79px, rgba(255,255,255,0.018) 79px, rgba(255,255,255,0.018) 80px);
  pointer-events: none; z-index: 0;
}}
.sector-lines {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 0; overflow: hidden; }}
.sector-lines::before, .sector-lines::after {{
  content: ''; position: absolute; width: 1px; height: 100vh;
  background: linear-gradient(to bottom, transparent 0%, var(--red) 40%, var(--red) 60%, transparent 100%);
  opacity: 0.2; animation: sectorScan 8s ease-in-out infinite;
}}
.sector-lines::before {{ left: 15%; animation-delay: 0s; }}
.sector-lines::after  {{ right: 15%; animation-delay: 4s; }}
@keyframes sectorScan {{
  0%,100% {{ opacity: 0.08; transform: scaleY(0.4) translateY(-30%); }}
  50%      {{ opacity: 0.25; transform: scaleY(1) translateY(0); }}
}}
.page {{
  position: relative; z-index: 1; min-height: 100vh;
  display: grid; grid-template-columns: 1fr 460px 1fr;
  align-items: center;
}}
.telem-panel {{
  padding: 60px 40px; display: flex; flex-direction: column; gap: 32px;
  animation: fadeInLeft 0.8s cubic-bezier(0.16,1,0.3,1) both;
}}
@keyframes fadeInLeft {{
  from {{ opacity: 0; transform: translateX(-20px); }}
  to   {{ opacity: 1; transform: translateX(0); }}
}}
.telem-label {{ font-family: var(--font-mono); font-size: 9px; letter-spacing: 0.2em; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }}
.telem-value {{ font-family: var(--font-hero); font-size: 42px; font-weight: 700; letter-spacing: -0.01em; line-height: 1; color: var(--text); }}
.telem-value.accent {{ color: var(--red); }}
.telem-bar-wrap {{ height: 3px; background: var(--border); border-radius: 2px; margin-top: 8px; overflow: hidden; }}
.telem-bar {{ height: 100%; background: var(--red); border-radius: 2px; animation: barLoad 2s cubic-bezier(0.16,1,0.3,1) 0.5s both; }}
@keyframes barLoad {{ from {{ width: 0%; }} }}
.telem-sparkline {{ display: flex; align-items: flex-end; gap: 2px; height: 32px; margin-top: 6px; }}
.telem-sparkline span {{ flex: 1; background: var(--red); border-radius: 1px; opacity: 0.7; animation: sparkPop 0.4s cubic-bezier(0.34,1.56,0.64,1) both; }}
.telem-sparkline span:nth-child(1)  {{ height: 40%; animation-delay: 0.60s; }}
.telem-sparkline span:nth-child(2)  {{ height: 70%; animation-delay: 0.65s; }}
.telem-sparkline span:nth-child(3)  {{ height: 55%; animation-delay: 0.70s; }}
.telem-sparkline span:nth-child(4)  {{ height: 90%; animation-delay: 0.75s; }}
.telem-sparkline span:nth-child(5)  {{ height: 65%; animation-delay: 0.80s; }}
.telem-sparkline span:nth-child(6)  {{ height: 100%; animation-delay: 0.85s; }}
.telem-sparkline span:nth-child(7)  {{ height: 80%; animation-delay: 0.90s; }}
.telem-sparkline span:nth-child(8)  {{ height: 45%; animation-delay: 0.95s; }}
.telem-sparkline span:nth-child(9)  {{ height: 72%; animation-delay: 1.00s; }}
.telem-sparkline span:nth-child(10) {{ height: 88%; animation-delay: 1.05s; }}
.telem-sparkline span:nth-child(11) {{ height: 60%; animation-delay: 1.10s; }}
.telem-sparkline span:nth-child(12) {{ height: 95%; animation-delay: 1.15s; }}
@keyframes sparkPop {{
  from {{ transform: scaleY(0); opacity: 0; }}
  to   {{ transform: scaleY(1); opacity: 0.7; }}
}}
.telem-divider {{ width: 40px; height: 1px; background: var(--border); }}
.info-panel {{
  padding: 60px 40px; display: flex; flex-direction: column; gap: 24px; align-items: flex-start;
  animation: fadeInRight 0.8s cubic-bezier(0.16,1,0.3,1) 0.1s both;
}}
@keyframes fadeInRight {{
  from {{ opacity: 0; transform: translateX(20px); }}
  to   {{ opacity: 1; transform: translateX(0); }}
}}
.info-badge {{ font-family: var(--font-mono); font-size: 9px; letter-spacing: 0.18em; text-transform: uppercase; color: var(--red); background: var(--red-dim); border: 1px solid rgba(232,0,45,0.2); padding: 5px 10px; }}
.info-title {{ font-family: var(--font-hero); font-size: 13px; font-weight: 600; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); margin-bottom: 8px; }}
.endpoint-list {{ display: flex; flex-direction: column; gap: 8px; }}
.endpoint {{ display: flex; align-items: center; gap: 10px; }}
.method {{ font-family: var(--font-mono); font-size: 9px; letter-spacing: 0.1em; padding: 2px 6px; background: var(--red-dim); color: var(--red); border-radius: 2px; flex-shrink: 0; }}
.path {{ font-family: var(--font-mono); font-size: 11px; color: var(--muted); }}
.status-row {{ display: flex; align-items: center; gap: 8px; font-family: var(--font-mono); font-size: 10px; color: var(--muted); }}
.status-dot {{ width: 6px; height: 6px; border-radius: 50%; background: #00C853; flex-shrink: 0; box-shadow: 0 0 8px #00C853; animation: statusPulse 2s ease-in-out infinite; }}
@keyframes statusPulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} }}
.card-wrap {{ position: relative; z-index: 2; padding: 20px 0; }}
.card-wrap::before {{
  content: ''; position: absolute; left: 50%; top: -60px; bottom: -60px; width: 1px;
  background: linear-gradient(to bottom, transparent, var(--red) 30%, var(--red) 70%, transparent);
  opacity: 0.3; transform: translateX(-50%); pointer-events: none;
}}
.card {{ background: var(--bg2); border: 1px solid var(--border); position: relative; overflow: hidden; animation: cardReveal 0.6s cubic-bezier(0.16,1,0.3,1) 0.15s both; }}
@keyframes cardReveal {{
  from {{ opacity: 0; transform: translateY(16px) scale(0.98); }}
  to   {{ opacity: 1; transform: translateY(0) scale(1); }}
}}
.card::before, .card::after {{ content: ''; position: absolute; width: 12px; height: 12px; border-color: var(--red); border-style: solid; opacity: 0.6; z-index: 1; }}
.card::before {{ top: -1px; left: -1px; border-width: 2px 0 0 2px; }}
.card::after  {{ top: -1px; right: -1px; border-width: 2px 2px 0 0; }}
.card-inner {{ position: relative; }}
.card-inner::before, .card-inner::after {{ content: ''; position: absolute; width: 12px; height: 12px; border-color: var(--red); border-style: solid; opacity: 0.6; z-index: 1; }}
.card-inner::before {{ bottom: -1px; left: -1px; border-width: 0 0 2px 2px; }}
.card-inner::after  {{ bottom: -1px; right: -1px; border-width: 0 2px 2px 0; }}
.card-topbar {{ display: flex; align-items: center; justify-content: space-between; padding: 12px 24px; border-bottom: 1px solid var(--border); background: var(--bg3); }}
.card-topbar-label {{ font-family: var(--font-mono); font-size: 9px; letter-spacing: 0.2em; text-transform: uppercase; color: var(--muted); }}
.card-topbar-dots {{ display: flex; gap: 5px; }}
.card-topbar-dots span {{ width: 5px; height: 5px; border-radius: 50%; background: var(--border-hover); }}
.card-topbar-dots span:first-child {{ background: var(--red); opacity: 0.6; }}
.card-body {{ padding: 32px 32px 28px; }}
.logo {{ display: flex; align-items: center; gap: 14px; margin-bottom: 32px; }}
.logo-t1 {{ position: relative; width: 44px; height: 44px; background: var(--red); display: flex; align-items: center; justify-content: center; flex-shrink: 0; }}
.logo-t1::after {{ content: ''; position: absolute; bottom: -4px; right: -4px; width: 8px; height: 8px; background: var(--red); opacity: 0.3; }}
.logo-t1 span {{ font-family: var(--font-hero); font-weight: 700; font-size: 20px; font-style: italic; color: #fff; letter-spacing: -0.02em; }}
.logo-name {{ font-family: var(--font-hero); font-weight: 700; font-size: 20px; letter-spacing: 0.04em; text-transform: uppercase; line-height: 1; }}
.logo-sub {{ font-family: var(--font-mono); font-size: 9px; letter-spacing: 0.2em; text-transform: uppercase; color: var(--muted); margin-top: 3px; }}
.heading {{ font-family: var(--font-hero); font-size: 32px; font-weight: 700; font-style: italic; letter-spacing: -0.01em; text-transform: uppercase; line-height: 1; margin-bottom: 4px; }}
.subheading {{ font-family: var(--font-mono); font-size: 10px; letter-spacing: 0.15em; text-transform: uppercase; color: var(--muted); margin-bottom: 28px; }}
.error-box {{ display: flex; align-items: center; gap: 10px; background: rgba(232,0,45,0.08); border: 1px solid rgba(232,0,45,0.25); padding: 10px 14px; margin-bottom: 20px; font-family: var(--font-mono); font-size: 11px; color: #FF4D6D; letter-spacing: 0.05em; }}
.error-box svg {{ flex-shrink: 0; }}
.field {{ margin-bottom: 18px; }}
.field-label {{ font-family: var(--font-mono); font-size: 9px; letter-spacing: 0.2em; text-transform: uppercase; color: var(--muted); display: block; margin-bottom: 8px; }}
.field-input {{ width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text); font-family: var(--font-mono); font-size: 13px; padding: 12px 14px; outline: none; transition: border-color 0.15s, box-shadow 0.15s; border-radius: 0; -webkit-appearance: none; }}
.field-input::placeholder {{ color: rgba(240,240,242,0.2); }}
.field-input:focus {{ border-color: var(--red); box-shadow: 0 0 0 3px var(--red-glow); }}
.submit-btn {{ width: 100%; background: var(--red); color: #fff; border: none; font-family: var(--font-hero); font-weight: 700; font-style: italic; font-size: 16px; letter-spacing: 0.1em; text-transform: uppercase; padding: 14px 24px; cursor: pointer; margin-top: 8px; position: relative; overflow: hidden; transition: opacity 0.15s, transform 0.1s; display: flex; align-items: center; justify-content: center; gap: 10px; border-radius: 0; }}
.submit-btn::before {{ content: ''; position: absolute; top: 0; left: -100%; width: 60%; height: 100%; background: linear-gradient(90deg, transparent, rgba(255,255,255,0.08), transparent); transition: left 0.4s; }}
.submit-btn:hover::before {{ left: 160%; }}
.submit-btn:hover {{ opacity: 0.92; }}
.submit-btn:active {{ transform: scale(0.99); }}
.submit-arrow {{ font-style: normal; letter-spacing: 0; font-size: 14px; }}
.card-footer {{ padding: 12px 32px 20px; display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--border); }}
.footer-link {{ font-family: var(--font-mono); font-size: 9px; letter-spacing: 0.15em; text-transform: uppercase; color: var(--muted); text-decoration: none; transition: color 0.15s; }}
.footer-link:hover {{ color: var(--red); }}
.footer-version {{ font-family: var(--font-mono); font-size: 9px; letter-spacing: 0.1em; color: rgba(240,240,242,0.2); }}
@media (max-width: 900px) {{
  .page {{ grid-template-columns: 1fr; justify-items: center; padding: 40px 20px; }}
  .telem-panel, .info-panel {{ display: none; }}
  .card-wrap {{ width: 100%; max-width: 420px; }}
  .card-wrap::before {{ display: none; }}
}}
</style>
</head>
<body>
<div class="sector-lines"></div>
<div class="page">
  <div class="telem-panel">
    <div>
      <div class="telem-label">Endpoints active</div>
      <div class="telem-value accent">247</div>
      <div class="telem-bar-wrap"><div class="telem-bar" style="width:82%"></div></div>
    </div>
    <div class="telem-divider"></div>
    <div>
      <div class="telem-label">Avg response</div>
      <div class="telem-value">84<span style="font-size:20px;opacity:0.5">ms</span></div>
    </div>
    <div>
      <div class="telem-label">Throttle data / 24h</div>
      <div class="telem-sparkline">
        <span></span><span></span><span></span><span></span><span></span><span></span>
        <span></span><span></span><span></span><span></span><span></span><span></span>
      </div>
    </div>
    <div class="telem-divider"></div>
    <div>
      <div class="telem-label">Season</div>
      <div class="telem-value">2026</div>
    </div>
  </div>

  <div class="card-wrap">
    <div class="card">
      <div class="card-inner">
        <div class="card-topbar">
          <span class="card-topbar-label">T1API // Internal Portal</span>
          <div class="card-topbar-dots"><span></span><span></span><span></span></div>
        </div>
        <div class="card-body">
          <div class="logo">
            <div class="logo-t1"><span>T1</span></div>
            <div>
              <div class="logo-name">Turn One</div>
              <div class="logo-sub">Telemetry API</div>
            </div>
          </div>
          <div class="heading">Internal Access</div>
          <div class="subheading">Authorized personnel only</div>
          {error_block}
          <form method="post" action="/docs/login">
            <div class="field">
              <label class="field-label" for="username">Username</label>
              <input class="field-input" id="username" name="username"
                     type="text" autocomplete="username" autofocus required placeholder="your_username">
            </div>
            <div class="field">
              <label class="field-label" for="password">Password</label>
              <input class="field-input" id="password" name="password"
                     type="password" autocomplete="current-password" required placeholder="••••••••••">
            </div>
            <button class="submit-btn" type="submit">
              Authenticate <span class="submit-arrow">&rarr;</span>
            </button>
          </form>
        </div>
        <div class="card-footer">
          <a class="footer-link" href="https://turnonehub.com" target="_blank">turnonehub.com</a>
          <span class="footer-version">v{app_version}</span>
        </div>
      </div>
    </div>
  </div>

  <div class="info-panel">
    <div class="info-badge">Live API</div>
    <div>
      <div class="info-title">Rate limits</div>
      <div class="endpoint-list">
        <div class="endpoint"><span class="method">PUB</span><span class="path">30 req / min</span></div>
        <div class="endpoint"><span class="method">STD</span><span class="path">100 req / min</span></div>
        <div class="endpoint"><span class="method">PRO</span><span class="path">300 req / min</span></div>
      </div>
    </div>
    <div>
      <div class="info-title">Endpoints</div>
      <div class="endpoint-list">
        <div class="endpoint"><span class="method">GET</span><span class="path">/api/v2/analysis</span></div>
        <div class="endpoint"><span class="method">GET</span><span class="path">/api/v2/seasonal</span></div>
        <div class="endpoint"><span class="method">GET</span><span class="path">/api/health</span></div>
      </div>
    </div>
    <div class="status-row">
      <span class="status-dot"></span>
      <span>All systems operational</span>
    </div>
  </div>
</div>
</body>
</html>"""


# ── Setup function ────────────────────────────────────────────────────────────

def setup_docs_auth(app: FastAPI, settings, swagger_ui_parameters: dict):
    """
    Register /docs, /redoc, /openapi.json, /docs/login, /docs/logout
    with custom form-based authentication (no HTTP Basic Auth).

    settings must expose:
        settings.docs_username: str
        settings.docs_password: str
        settings.environment: str
        settings.app_version: str
    """
    _secret = secrets.token_hex(32)

    def _auth_required() -> bool:
        return settings.environment.lower() == "production" or getattr(settings, "docs_auth_always", False)

    @app.get("/favicon.png", include_in_schema=False)
    async def docs_favicon_png():
      if not FAVICON_PATH.exists():
        return JSONResponse({"detail": "Favicon image not found"}, status_code=404)
      return FileResponse(FAVICON_PATH, media_type="image/png")

    @app.get("/favicon.ico", include_in_schema=False)
    async def docs_favicon_ico():
      return RedirectResponse("/favicon.png", status_code=307)

    @app.get("/docs/login", include_in_schema=False)
    async def docs_login_page(request: Request):
        if _is_authenticated(request, _secret):
            return RedirectResponse("/docs", status_code=302)
        return HTMLResponse(_login_page(app_version=settings.app_version))

    @app.post("/docs/login", include_in_schema=False)
    async def docs_login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        valid_user = secrets.compare_digest(username, settings.docs_username or "")
        valid_pass = secrets.compare_digest(password, settings.docs_password or "")

        if not (valid_user and valid_pass):
            return HTMLResponse(
                _login_page(error=True, app_version=settings.app_version),
                status_code=401,
            )

        token = _make_token(_secret)
        response = RedirectResponse("/docs", status_code=302)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_TTL,
            httponly=True,
            samesite="lax",
            secure=False,  # Set True if using HTTPS
        )
        return response

    @app.get("/docs/logout", include_in_schema=False)
    async def docs_logout():
        response = RedirectResponse("/docs/login", status_code=302)
        response.delete_cookie(COOKIE_NAME)
        return response

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui(request: Request):
        if _auth_required() and not _is_authenticated(request, _secret):
            return RedirectResponse("/docs/login", status_code=302)
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} - Swagger UI",
            swagger_ui_parameters=swagger_ui_parameters,
          swagger_favicon_url="/favicon.png",
        )

    @app.get("/redoc", include_in_schema=False)
    async def redoc_ui(request: Request):
        if _auth_required() and not _is_authenticated(request, _secret):
            return RedirectResponse("/docs/login", status_code=302)
        return get_redoc_html(
            openapi_url="/openapi.json",
            title=f"{app.title} - ReDoc",
          redoc_favicon_url="/favicon.png",
        )

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi_schema(request: Request):
        if _auth_required() and not _is_authenticated(request, _secret):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return JSONResponse(content=app.openapi())