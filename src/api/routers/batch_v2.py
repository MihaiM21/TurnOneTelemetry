"""Batch endpoint: many V2 features for one session in a single request.

Why this exists: a ``SessionDataStore`` caches parsed livetiming streams *per
instance*, and every V2 analysis module builds its own via
``build_session_store()``. So a frontend asking for N features of one session
previously meant the same multi-megabyte streams were downloaded and parsed N
times, across N HTTP round-trips. ``shared_session_stores()``
(``src/services/analysis/v2/_helpers.py``) memoizes those instances for the
duration of a scope; this endpoint is the one caller that opens it, running
every requested feature's generator inside a single scope so repeated
``build_session_store()`` calls for the same session return one instance.

The scope is opened *inside* the threadpool worker (see ``_run_batch``) rather
than around the ``run_in_threadpool`` call itself. A ``ContextVar`` set on the
event loop thread is copied into an ``anyio`` worker thread's context, so it
would likely propagate either way -- but this endpoint does not lean on that:
opening the scope inside the worker function is unambiguous and is what
``tests/unit/api/test_batch.py`` verifies actually happens.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from src.api.deps import validate_driver, validate_gp, validate_session
from src.api.schemas.batch import (
    BatchFeatureRequest,
    BatchRequestBody,
    BatchResponse,
)
from src.api.schemas.common import COMMON_ERROR_RESPONSES, ErrorEnvelope
from src.core.logging import get_logger
from src.core.security.api_keys import verify_api_key
from src.core.security.rate_limiting import apply_tiered_limit
from src.repositories.plots import get_plot_data_from_mongo
from src.services.analysis.v2._helpers import shared_session_stores
from src.services.analysis.v2.registry import (
    KIND_PER_DRIVER,
    KIND_PER_DRIVER_LAP,
    KIND_PER_PAIR,
    KIND_SINGLETON,
    feature_by_key,
)
from src.services.orchestrator_helpers import simplify_session_name

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v2")

#: A full-catalog request would re-run the exact "generate everything" job the
#: admin backfill already exists for, one request at a time and without its
#: job tracking / concurrency controls. 25 comfortably covers "give me every
#: singleton plus a couple of per-driver views for a dashboard" while keeping
#: one request's worst case bounded.
MAX_BATCH_ITEMS = 25


def _cached_or_run(
    year: int,
    gp: Any,
    session_type: str,
    data_type: Optional[str],
    generator,
) -> Any:
    """Mongo-then-generate, mirroring what the single-feature endpoints do.

    Most registry generators already perform this check internally (directly,
    or via ``cached_or_generate``), so this is frequently a redundant-but-cheap
    point read. It is not redundant for any generator that does not, and it
    keeps the guarantee local to this endpoint rather than depending on every
    current and future catalog entry doing it themselves.
    """
    if data_type:
        cached = get_plot_data_from_mongo(year, gp, session_type, data_type, version="v2")
        if cached:
            return cached["data"]
    return generator()


def _run_one_feature(
    year: int, gp: Any, session_type: str, item: BatchFeatureRequest
) -> Dict[str, Any]:
    """Resolve and run a single requested feature, never raising.

    Every failure mode -- unknown handling aside, since unknown keys are
    rejected before this runs -- becomes a per-item error so one bad feature
    cannot fail the whole batch.
    """
    entry = feature_by_key(item.key)
    if entry is None:  # pragma: no cover - filtered out before _run_batch
        return {"key": item.key, "status": "error", "error": f"Unknown feature key: {item.key!r}"}

    if not entry.session_scoped:
        return {
            "key": item.key,
            "status": "error",
            "error": "Season/career-scope features are not addressed by (year, gp, session) "
                     "and are not supported in a session batch.",
        }

    if not entry.applies(session_type):
        return {
            "key": item.key,
            "status": "error",
            "error": f"Feature '{item.key}' does not apply to session type '{session_type}'.",
        }

    try:
        if entry.kind == KIND_SINGLETON:
            data_type = entry.spec.data_type
            data = _cached_or_run(
                year, gp, session_type, data_type,
                lambda: entry.spec.generate(year, gp, session_type),
            )
        elif entry.kind == KIND_PER_DRIVER:
            driver = validate_driver(item.driver)
            if not driver:
                return {"key": item.key, "status": "error", "error": "'driver' is required for this feature."}
            data_type = entry.spec.key_for(driver)
            data = _cached_or_run(
                year, gp, session_type, data_type,
                lambda: entry.spec.generate(year, gp, session_type, driver),
            )
        elif entry.kind == KIND_PER_PAIR:
            d1 = validate_driver(item.driver1)
            d2 = validate_driver(item.driver2)
            if not d1 or not d2:
                return {
                    "key": item.key, "status": "error",
                    "error": "'driver1' and 'driver2' are both required for this feature.",
                }
            data_type = entry.spec.key_for(d1, d2)
            data = _cached_or_run(
                year, gp, session_type, data_type,
                lambda: entry.spec.generate(year, gp, session_type, d1, d2),
            )
        elif entry.kind == KIND_PER_DRIVER_LAP:
            driver = validate_driver(item.driver)
            if not driver or item.lap is None:
                return {
                    "key": item.key, "status": "error",
                    "error": "'driver' and 'lap' are both required for this feature "
                             "(per-lap features are never generated implicitly).",
                }
            data_type = entry.spec.key_for(driver, item.lap)
            data = _cached_or_run(
                year, gp, session_type, data_type,
                lambda: entry.spec.generate(year, gp, session_type, driver, item.lap),
            )
        else:  # pragma: no cover - catalog only defines the kinds above
            return {"key": item.key, "status": "error", "error": f"Unsupported feature kind: {entry.kind}"}
    except Exception as exc:
        logger.warning(
            "Batch feature %r failed for %s gp=%s session=%s: %s",
            item.key, year, gp, session_type, exc,
        )
        return {"key": item.key, "status": "error", "error": str(exc)}

    if not data:
        return {"key": item.key, "status": "error", "error": "No data available for this session."}

    return {"key": item.key, "status": "ok", "data": data}


@router.post(
    "/batch",
    tags=["API v2"],
    summary="Batch: multiple V2 features for one session",
    operation_id="v2_batch_features",
    description=(
        "Fetch JSON data for several V2 features of one session in a single request. "
        "Every uncached feature in the batch is generated inside one shared session-store "
        "scope, so N features share one set of parsed livetiming streams instead of each "
        "re-downloading and re-parsing them. Only JSON data features are supported (no PNG "
        "plots). A failing feature is reported with its own error rather than failing the "
        "whole batch -- check each result's `status`."
    ),
    responses={
        200: {"model": BatchResponse},
        400: {
            "model": ErrorEnvelope,
            "description": (
                "Malformed request: bad session/gp, empty or oversized feature list, "
                "or an unknown feature key."
            ),
        },
        **COMMON_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("data")
async def batch_v2(
    request: Request,
    body: BatchRequestBody,
    api_key: str = Depends(verify_api_key),
):
    if not body.features:
        raise HTTPException(status_code=400, detail="'features' must be a non-empty list.")
    if len(body.features) > MAX_BATCH_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many features requested ({len(body.features)}); the limit is {MAX_BATCH_ITEMS}.",
        )

    gp = validate_gp(body.gp)
    session_canonical = validate_session(body.session)
    session_type = simplify_session_name(session_canonical).strip().upper()

    # Reject the whole request for a structurally bad key rather than burying
    # a typo'd feature name inside a per-item error the caller has to notice.
    for item in body.features:
        if feature_by_key(item.key) is None:
            raise HTTPException(status_code=400, detail=f"Unknown feature key: {item.key!r}")

    def _run_batch() -> List[Dict[str, Any]]:
        # Opened here, inside the threadpool worker: shared_session_stores()
        # sets a ContextVar, and every build_session_store() call this batch
        # makes -- from whichever module each feature's generate() delegates
        # to -- happens on this same thread for the duration of the scope.
        results: List[Dict[str, Any]] = []
        with shared_session_stores():
            for item in body.features:
                results.append(_run_one_feature(body.year, gp, session_type, item))
        return results

    results = await run_in_threadpool(_run_batch)

    return {
        "year": body.year,
        "gp": body.gp,
        "session": session_type,
        "results": results,
    }
