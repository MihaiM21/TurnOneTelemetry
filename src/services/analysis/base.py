"""
Shared scaffolding for analysis services.

Today the V1 (FastF1-backed) and V2 (StaticClient-backed) analysis modules
live side-by-side under `v1/` and `v2/`. Each module exposes a `*Plot` and
`*Data` callable. This file is the seam where shared abstractions land when
the V1/V2 pairs are merged into a single source-agnostic service.

Use `cached_or_generate` to wrap MongoDB-cache-then-generate flows so each
analysis stops re-implementing the same try/cache/store dance.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from src.core.exceptions import (
    DataNotAvailableError,
    SessionNotFoundError,
    UpstreamUnavailableError,
)
from src.core.logging import get_logger
from src.repositories.plots import get_plot_data_from_mongo

logger = get_logger(__name__)

# Errors that should trigger fallback to the sibling implementation. A
# SessionNotFoundError is *not* in here on purpose — if the schedule says the
# session doesn't exist, the other source won't have it either; fall through
# to a 404 instead of wasting a second call.
_FALLBACK_TRIGGERS = (DataNotAvailableError, UpstreamUnavailableError)


def cached_or_generate(
    year: int,
    identifier: Any,
    session: str,
    data_type: str,
    generator: Callable[[], Any],
    version: str = "v2",
) -> Any:
    """
    Return cached plot data from MongoDB if present, otherwise call `generator`.

    Each analysis module currently inlines this lookup — moving it here lets
    future modules drop ~15 lines of boilerplate.
    """
    cached = get_plot_data_from_mongo(year, identifier, session, data_type, version=version)
    if cached:
        return cached["data"]
    return generator()


def with_fallback(
    primary: Callable[[], Any],
    secondary: Optional[Callable[[], Any]] = None,
    *,
    primary_source: str = "v1",
    secondary_source: str = "v2",
    year: Optional[int] = None,
    gp: Any = None,
    session: Optional[str] = None,
    data_type: str = "",
) -> Any:
    """
    Run `primary()`; on DataNotAvailableError / UpstreamUnavailableError, run `secondary()`.

    If both fail (or only primary is supplied and it fails), raise a single
    DataNotAvailableError that lists every source that was tried. The FastAPI
    handler in `src/api/app.py` translates this into a 503 with a structured body.

    SessionNotFoundError is NOT a fallback trigger — it propagates immediately
    so the API returns 404 without a wasted round-trip to the sibling source.
    """
    sources_tried = []
    primary_error: Optional[Exception] = None

    try:
        return primary()
    except SessionNotFoundError:
        raise
    except _FALLBACK_TRIGGERS as exc:
        sources_tried.append(primary_source)
        primary_error = exc
        logger.warning(
            "Primary source %r failed for %s year=%s gp=%s session=%s: %s — trying %s",
            primary_source, data_type, year, gp, session, exc, secondary_source,
        )

    if secondary is None:
        raise DataNotAvailableError(
            year=year, gp=gp, session=session,
            sources_tried=sources_tried,
            reason=str(primary_error) if primary_error else "primary source failed",
        )

    try:
        return secondary()
    except SessionNotFoundError:
        raise
    except _FALLBACK_TRIGGERS as exc:
        sources_tried.append(secondary_source)
        logger.error(
            "Both sources failed for %s year=%s gp=%s session=%s. "
            "Primary(%s)=%s; Secondary(%s)=%s",
            data_type, year, gp, session,
            primary_source, primary_error, secondary_source, exc,
        )
        raise DataNotAvailableError(
            year=year, gp=gp, session=session,
            sources_tried=sources_tried,
            reason=(
                f"Both upstream sources failed. "
                f"{primary_source}: {primary_error}. {secondary_source}: {exc}"
            ),
        ) from exc
