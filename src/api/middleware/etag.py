"""Weak-ETag middleware for cacheable GET endpoints.

For GET responses under selected path prefixes we compute a SHA-1 of the body,
emit a weak ETag, and short-circuit the next matching request with a 304.

Two things this middleware must get right, both of which it previously got
wrong:

* **Not everything under a prefix is immutable.** It used to stamp
  ``public, max-age=86400, immutable`` on *every* 200, including
  ``/api/v2/dashboard`` — which resolves the *latest* session and changes while
  a race runs. ``immutable`` instructs clients never to revalidate, so the live
  dashboard froze for 24 hours in every browser and CDN. Volatile paths now get
  a short ``must-revalidate`` policy instead.
* **These responses are API-key gated**, so the policy is ``private``: a shared
  proxy must never hand one caller's payload to another.

Binary file responses (the PNG plot endpoints) are passed through untouched —
hashing them would buffer multi-megabyte images into memory for no benefit,
since they are already served from disk with their own validators.
"""
from __future__ import annotations

import hashlib
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.api.http_cache import (
    DEFAULT_MAX_AGE_SECONDS,
    immutable_cache_control,
    is_not_modified,
    is_volatile_path,
    volatile_cache_control,
)

#: Only these content types are worth hashing; everything else (images, octet
#: streams) is streamed through untouched.
_HASHABLE_TYPES = ("application/json", "text/")


class ETagMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        path_prefixes: Iterable[str] = ("/api/v1/", "/api/v2/"),
        cache_control_max_age: int = DEFAULT_MAX_AGE_SECONDS,
    ):
        super().__init__(app)
        self._prefixes = tuple(path_prefixes)
        self._cache_control_max_age = cache_control_max_age

    def _is_hashable(self, response: Response) -> bool:
        content_type = (response.headers.get("content-type") or "").lower()
        return any(content_type.startswith(t) or t in content_type for t in _HASHABLE_TYPES)

    async def dispatch(self, request: Request, call_next):
        if request.method not in ("GET", "HEAD"):
            return await call_next(request)
        if not request.url.path.startswith(self._prefixes):
            return await call_next(request)

        response: Response = await call_next(request)
        if response.status_code != 200:
            return response

        # Leave binary payloads (PNG plots) alone rather than buffering them.
        if not self._is_hashable(response):
            return response
        if not hasattr(response, "body_iterator"):
            return response

        body_chunks: list[bytes] = []
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            body_chunks.append(chunk)
        body = b"".join(body_chunks)

        # Weak validator -- an ETag is a change detector, not a security
        # primitive, so SHA-1 is appropriate here.
        digest = hashlib.sha1(body, usedforsecurity=False).hexdigest()
        etag = 'W/"' + digest + '"'
        if_none_match = request.headers.get("if-none-match")
        if is_not_modified(if_none_match, etag):
            headers = dict(response.headers)
            headers.pop("content-length", None)
            headers["ETag"] = etag
            return Response(status_code=304, headers=headers)

        headers = dict(response.headers)
        headers["ETag"] = etag
        if "cache-control" not in {k.lower() for k in headers}:
            if is_volatile_path(request.url.path):
                headers["Cache-Control"] = volatile_cache_control()
            elif self._cache_control_max_age > 0:
                headers["Cache-Control"] = immutable_cache_control(
                    self._cache_control_max_age
                )
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )
