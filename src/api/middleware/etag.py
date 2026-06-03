"""Weak-ETag middleware for cacheable GET endpoints.

For GET responses under selected path prefixes (default ``/api/v2/``) we
compute a SHA-1 of the body and emit a weak ETag header. On the next
request, if the client sends ``If-None-Match`` with the same tag, we
short-circuit with a 304 and an empty body.

Non-breaking: clients that don't send ``If-None-Match`` see the same
response shape as before. Conditional GET is an enhancement, not a change.
"""
from __future__ import annotations

import hashlib
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class ETagMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        path_prefixes: Iterable[str] = ("/api/v2/",),
        cache_control_max_age: int = 0,
    ):
        super().__init__(app)
        self._prefixes = tuple(path_prefixes)
        self._cache_control_max_age = cache_control_max_age

    async def dispatch(self, request: Request, call_next):
        if request.method not in ("GET", "HEAD"):
            return await call_next(request)
        if not request.url.path.startswith(self._prefixes):
            return await call_next(request)

        response: Response = await call_next(request)
        if response.status_code != 200:
            return response

        # Consume the streaming body so we can hash it.
        body_chunks: list[bytes] = []
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            body_chunks.append(chunk)
        body = b"".join(body_chunks)

        etag = 'W/"' + hashlib.sha1(body).hexdigest() + '"'
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and etag in {tag.strip() for tag in if_none_match.split(",")}:
            headers = dict(response.headers)
            headers.pop("content-length", None)
            headers["ETag"] = etag
            return Response(status_code=304, headers=headers)

        headers = dict(response.headers)
        headers["ETag"] = etag
        if self._cache_control_max_age > 0 and "cache-control" not in {k.lower() for k in headers}:
            headers["Cache-Control"] = f"public, max-age={self._cache_control_max_age}"
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )
