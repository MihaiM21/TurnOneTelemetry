"""Opt-in CSV rendering for JSON analysis responses.

Analysts consuming this API mostly want the `-data` payloads in a spreadsheet
or a dataframe, and were previously left to convert JSON themselves. Adding a
``format`` parameter to ~50 endpoint signatures would have churned every one of
them (and the frozen public parameter list), so the conversion lives here
instead: append ``?format=csv`` to any JSON endpoint under a configured prefix
and the same payload is rendered as CSV.

Design notes:

* **Opt-in only.** Without ``format=csv`` the response is byte-identical to
  before, so no existing client can be affected.
* **Honest failure.** Not every payload is tabular -- some are nested objects
  with no natural row shape. Rather than emit a misleading half-flattened file
  or silently fall back to JSON (which would break a client that asked for CSV
  and got something else), those return **400** explaining that the endpoint is
  not tabular.
* Placed *inside* the ETag middleware so the validator is computed over the CSV
  body actually served, not the JSON it was derived from.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable, List, Optional, Sequence

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

#: Scalars that can sit in a CSV cell as-is.
_SCALARS = (str, int, float, bool, type(None))


def _is_flat_row(item: Any) -> bool:
    return isinstance(item, dict) and all(
        isinstance(v, _SCALARS) for v in item.values()
    )


def _explode(rows: Sequence[dict]) -> Optional[List[dict]]:
    """Flatten one level of nesting into long format.

    Several V2 payloads are shaped as one object per driver carrying a series,
    e.g. ``{"driver": "VER", "team": "...", "laps": [{"lap": 1, "gap_s": 2.1}]}``.
    The natural tabular form repeats the parent columns against each element of
    the series, which is exactly what a dataframe user wants.
    """
    exploded: List[dict] = []
    for row in rows:
        scalars = {k: v for k, v in row.items() if isinstance(v, _SCALARS)}
        series = [
            (k, v) for k, v in row.items()
            if isinstance(v, list) and v and all(isinstance(e, dict) for e in v)
        ]
        if len(series) != 1:
            return None
        name, entries = series[0]
        for entry in entries:
            if not _is_flat_row(entry):
                return None
            merged = dict(scalars)
            for k, v in entry.items():
                # Prefix on collision so a parent column is never overwritten.
                merged[f"{name}_{k}" if k in scalars else k] = v
            exploded.append(merged)
    return exploded or None


def _to_rows(payload: Any) -> Optional[List[dict]]:
    """Coerce a decoded JSON payload into a list of flat dict rows."""
    if isinstance(payload, list):
        if not payload:
            return []
        if all(_is_flat_row(i) for i in payload):
            return list(payload)
        if all(isinstance(i, dict) for i in payload):
            return _explode(payload)
        return None
    if isinstance(payload, dict):
        # A wrapper object with exactly one list-of-objects value (e.g.
        # {"data": [...]}) is unambiguous; anything else is not.
        lists = [
            v for v in payload.values()
            if isinstance(v, list) and v and all(isinstance(e, dict) for e in v)
        ]
        if len(lists) == 1:
            return _to_rows(lists[0])
    return None


def _render_csv(rows: Iterable[dict]) -> str:
    rows = list(rows)
    if not rows:
        return ""
    # Union of keys, preserving first-seen order so columns stay stable.
    columns: List[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


class CSVExportMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, path_prefixes: Iterable[str] = ("/api/v1/", "/api/v2/")):
        super().__init__(app)
        self._prefixes = tuple(path_prefixes)

    async def dispatch(self, request: Request, call_next):
        wants_csv = request.query_params.get("format", "").lower() == "csv"
        if not wants_csv or request.method not in ("GET", "HEAD"):
            return await call_next(request)
        if not request.url.path.startswith(self._prefixes):
            return await call_next(request)

        response = await call_next(request)
        content_type = (response.headers.get("content-type") or "").lower()
        if response.status_code != 200 or "json" not in content_type:
            return response
        if not hasattr(response, "body_iterator"):
            return response

        body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return Response(
                content=body, status_code=response.status_code,
                headers=dict(response.headers), media_type=response.media_type,
            )

        rows = _to_rows(payload)
        if rows is None:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": (
                        "This endpoint's payload is not tabular, so it cannot be "
                        "rendered as CSV. Request it without ?format=csv."
                    ),
                    "path": request.url.path,
                },
            )

        text = _render_csv(rows)
        headers = {
            k: v for k, v in response.headers.items()
            if k.lower() not in ("content-length", "content-type")
        }
        name = request.url.path.rstrip("/").split("/")[-1] or "export"
        headers["Content-Disposition"] = f'attachment; filename="{name}.csv"'
        return Response(content=text, status_code=200,
                        headers=headers, media_type="text/csv; charset=utf-8")
