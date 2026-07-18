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


def immutable_cache_control(max_age: int = DEFAULT_MAX_AGE_SECONDS) -> str:
    """Return a ``Cache-Control`` header value for immutable historical data."""
    return f"public, max-age={max_age}, immutable"


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
