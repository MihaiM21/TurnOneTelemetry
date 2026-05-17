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
    Three-tier lookup: Redis -> MongoDB -> generate.

    Redis is purely a speedup layer. If it's unavailable or disabled,
    behavior degrades to the prior Mongo-or-generate flow without raising.
    A Mongo hit is written back to Redis so the next request is hot.
    """
    try:
        from src.core.observability.monitoring import T1API_CACHE_HIT
    except Exception:
        T1API_CACHE_HIT = None  # metric optional during tests

    def _record(layer: str) -> None:
        if T1API_CACHE_HIT is not None:
            try:
                T1API_CACHE_HIT.labels(layer=layer, data_type=data_type).inc()
            except Exception:
                pass

    # Tier 1: Redis. Sync facade — never raises.
    try:
        from src.core.cache.redis_cache import get_sync_cache, make_plot_key
        cache = get_sync_cache()
        redis_key = make_plot_key(
            year=year, gp_id=str(identifier), session=session, data_type=data_type,
            params={"v": version},
        )
        if cache.enabled:
            hit = cache.get_json(redis_key)
            if hit is not None:
                _record("redis")
                return hit
    except Exception as exc:
        logger.debug("Redis lookup skipped: %s", exc)
        cache = None
        redis_key = None

    # Tier 2: MongoDB
    cached = get_plot_data_from_mongo(year, identifier, session, data_type, version=version)
    if cached:
        data = cached["data"]
        _record("mongo")
        if cache is not None and redis_key is not None and getattr(cache, "enabled", False):
            cache.set_json(redis_key, data)
        return data

    # Tier 3: Generate (callers are responsible for persisting to Mongo).
    _record("miss")
    result = generator()
    if cache is not None and redis_key is not None and getattr(cache, "enabled", False) and result is not None:
        try:
            cache.set_json(redis_key, result)
        except Exception:
            pass
    return result


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
    except Exception as exc:
        # FastF1 can raise DataNotLoadedError *after* session.load() silently fails for
        # a recent session. Classify these as data-unavailability rather than 500s.
        exc_name = type(exc).__name__.lower()
        msg = str(exc).lower()
        if not (
            'datanotloaded' in exc_name
            or 'sessionnotavailable' in exc_name
            or 'has not been loaded' in msg
            or 'no data for this session' in msg
        ):
            raise
        sources_tried.append(primary_source)
        primary_error = exc
        logger.warning(
            "Primary source %r raised unclassified data-availability error "
            "for %s year=%s gp=%s session=%s: %s — trying %s",
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
    except Exception as exc:
        exc_name = type(exc).__name__.lower()
        msg = str(exc).lower()
        if not (
            'datanotloaded' in exc_name
            or 'sessionnotavailable' in exc_name
            or 'has not been loaded' in msg
            or 'no data for this session' in msg
        ):
            raise
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
