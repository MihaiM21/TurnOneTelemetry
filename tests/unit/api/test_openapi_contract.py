"""Contract tests for the generated OpenAPI document.

These guard the two properties that are easy to regress silently as endpoints
are added: the public URL surface, and the documentation quality of that
surface. They are cheap and run offline.
"""
from __future__ import annotations

import pytest

from src.api.app import TAGS_METADATA, create_app


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(scope="module")
def spec(app):
    return app.openapi()


def _operations(spec):
    """Yield (path, method, operation) for every documented operation."""
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if isinstance(op, dict) and method in {
                "get", "post", "put", "patch", "delete",
            }:
                yield path, method, op


def test_openapi_document_builds(spec):
    assert spec["info"]["title"]
    assert spec["paths"]


def test_every_tag_used_is_declared(spec):
    """An undeclared tag renders with no description and in arbitrary order."""
    declared = {t["name"] for t in TAGS_METADATA}
    used = set()
    for _path, _method, op in _operations(spec):
        used.update(op.get("tags") or [])
    assert not (used - declared), (
        f"tags used by routers but missing from TAGS_METADATA: {sorted(used - declared)}"
    )


def test_no_duplicate_operation_ids(spec):
    """operation_id becomes the method name in generated client SDKs."""
    seen: dict[str, str] = {}
    duplicates = []
    for path, method, op in _operations(spec):
        oid = op.get("operationId")
        if not oid:
            continue
        if oid in seen:
            duplicates.append(f"{oid}: {seen[oid]} and {method.upper()} {path}")
        seen[oid] = f"{method.upper()} {path}"
    assert not duplicates, f"duplicate operationIds: {duplicates}"


def test_admin_endpoints_are_not_tagged_general(spec):
    """Admin operations must not sit beside the public welcome/health routes."""
    misfiled = [
        f"{m.upper()} {p}"
        for p, m, op in _operations(spec)
        if p.startswith("/api/admin") and "General" in (op.get("tags") or [])
    ]
    assert not misfiled, f"admin endpoints tagged 'General': {misfiled}"


def test_servers_are_declared(spec):
    assert spec.get("servers"), "no servers declared; 'Try it out' targets the docs origin"


def _looks_auto_generated(operation: dict) -> bool:
    """True when FastAPI derived the metadata instead of an author writing it.

    FastAPI falls back to the endpoint function name for `summary` (turning
    `get_race_gaps_data` into "Get Race Gaps Data") and appends the path and
    method to build an `operationId`. Both are present either way, so merely
    asserting they exist proves nothing -- this distinguishes real docs from
    the fallback.
    """
    operation_id = operation.get("operationId") or ""
    summary = operation.get("summary") or ""
    # An auto operationId embeds the full path, so it is long and contains the
    # method suffix after a path fragment (e.g. "..._api_v2_dashboard_get").
    if "_api_" in operation_id and operation_id.endswith(
        ("_get", "_post", "_put", "_patch", "_delete")
    ):
        return True
    # An auto summary is exactly the title-cased function name: every word
    # capitalised, no punctuation.
    words = summary.split()
    return bool(words) and all(w[:1].isupper() for w in words) and len(words) > 3


def test_every_operation_is_documented(spec):
    """Each endpoint needs an author-written summary, not FastAPI's fallback."""
    undocumented = [
        f"{m.upper()} {p}"
        for p, m, op in _operations(spec)
        if _looks_auto_generated(op)
    ]
    assert not undocumented, (
        "operations still using FastAPI's auto-derived metadata:\n  "
        + "\n  ".join(undocumented)
    )


def test_error_responses_are_documented(spec):
    """A 200-only operation tells callers nothing about failure modes."""
    thin = [
        f"{m.upper()} {p}"
        for p, m, op in _operations(spec)
        # 422 is added automatically by FastAPI for validated params.
        if len([c for c in op.get("responses", {}) if c not in ("200", "422")]) == 0
    ]
    assert len(thin) <= 2, f"operations with no documented error responses: {thin}"


def test_v1_is_marked_deprecated(spec):
    """v1 is retired-but-served; Swagger must show it struck through."""
    live_v1 = [
        f"{m.upper()} {p}"
        for p, m, op in _operations(spec)
        if p.startswith("/api/v1/") and not op.get("deprecated")
    ]
    assert not live_v1, f"v1 operations not marked deprecated: {live_v1}"
