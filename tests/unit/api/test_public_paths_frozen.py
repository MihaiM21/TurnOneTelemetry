"""The public URL surface is frozen.

`t1f1.com` and `turnonehub.com` call these paths in production, so a rename or
removal is a live outage even though it looks like a tidy refactor. This test
pins every `/api/v1`, `/api/v2` and `/api/static` route recorded in
`_public_paths.txt`.

Adding new endpoints is fine and deliberately does not fail this test -- only
losing or renaming an existing one does.

If you intentionally retire a path, delete its line from `_public_paths.txt` in
the same commit, so the removal is reviewable rather than silent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.api.app import create_app

MANIFEST = Path(__file__).parent / "_public_paths.txt"

PUBLIC_PREFIXES = ("/api/v1/", "/api/v2/", "/api/static/")


@pytest.fixture(scope="module")
def live_paths() -> set[str]:
    app = create_app()
    return {
        p
        for p in (getattr(r, "path", "") for r in app.routes)
        if p.startswith(PUBLIC_PREFIXES)
    }


def _expected() -> set[str]:
    return {
        line.strip()
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def test_no_public_path_disappeared(live_paths):
    missing = sorted(_expected() - live_paths)
    assert not missing, (
        "public API paths removed or renamed -- this breaks live frontends:\n  "
        + "\n  ".join(missing)
    )


def test_manifest_is_not_stale(live_paths):
    """Nudge to record newly added public paths, without failing the build."""
    added = sorted(live_paths - _expected())
    if added:
        pytest.skip(
            "new public paths not yet in the manifest (add them if intentional): "
            + ", ".join(added)
        )
