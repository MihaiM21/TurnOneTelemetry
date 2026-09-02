"""HTTP caching helpers for immutable, historical analysis responses.

Past F1 sessions never change once processed, so their analysis responses
(plots and data) can be cached aggressively by clients/CDNs. This module
provides small, dependency-free helpers for:

* building a stable ``Cache-Control`` header for such responses
* computing an ETag from the logical identity of the request
  (year, gp, session, data_type + any extra params)
* checking an incoming ``If-None-Match`` header against that ETag

These are pure functions so they can be reused from middleware or from
individual endpoints without coupling to any particular framework object
beyond plain strings/dicts.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping, Optional

DEFAULT_MAX_AGE_SECONDS = 86400

#: How long a *mutable* response (live/current-season data) may be reused.
#: Short, and always revalidated -- the latest-session dashboard changes while
#: a session is running.
VOLATILE_MAX_AGE_SECONDS = 30


def immutable_cache_control(max_age: int = DEFAULT_MAX_AGE_SECONDS) -> str:
    """``Cache-Control`` for genuinely immutable historical data.

    ``private`` rather than ``public``: these responses are gated by an API key,
    so a shared proxy or CDN must not hand one caller's payload to another.
    ``immutable`` tells clients never to revalidate, which is only safe for a
    completed session whose data can no longer change.
    """
    return f"private, max-age={max_age}, immutable"


def volatile_cache_control(max_age: int = VOLATILE_MAX_AGE_SECONDS) -> str:
    """``Cache-Control`` for responses whose content can still change.

    Used for the latest-session dashboard and current-season data. Without this
    the ETag middleware stamped ``immutable, max-age=86400`` on the live
    dashboard, freezing it for 24h in every browser and CDN.
    """
    return f"private, max-age={max_age}, must-revalidate"


def compute_etag(
    year: Any,
    gp: Any,
    session: Any,
    data_type: str,
    params: Optional[Mapping[str, Any]] = None,
) -> str:
    """Compute a weak ETag identifying a specific analysis response.

    The tag is derived from the logical identity of the request
    (year, gp, session, data_type) plus any additional distinguishing
    query params (e.g. driver TLAs), so identical requests always produce
    the same tag and different requests never collide in practice.
    """
    parts = [str(year), str(gp), str(session), str(data_type)]
    if params:
        for key in sorted(params):
            parts.append(f"{key}={params[key]}")
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return f'W/"{digest}"'


def is_not_modified(if_none_match: Optional[str], etag: str) -> bool:
    """Return True if the client's If-None-Match header matches ``etag``."""
    if not if_none_match:
        return False
    candidates = {tag.strip() for tag in if_none_match.split(",")}
    return etag in candidates


def cache_headers(
    year: Any,
    gp: Any,
    session: Any,
    data_type: str,
    params: Optional[Mapping[str, Any]] = None,
    max_age: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict:
    """Build the full set of caching headers for an immutable analysis response."""
    return {
        "Cache-Control": immutable_cache_control(max_age),
        "ETag": compute_etag(year, gp, session, data_type, params),
    }


#: Path suffixes under ``/api/v2/`` whose content is not immutable.
VOLATILE_PATH_MARKERS = ("/dashboard",)


def is_volatile_path(path: str) -> bool:
    """True when a response body for ``path`` can still change.

    The latest-session dashboard resolves whichever session is most recent, so
    its payload changes as a race unfolds.
    """
    return any(marker in path for marker in VOLATILE_PATH_MARKERS)
