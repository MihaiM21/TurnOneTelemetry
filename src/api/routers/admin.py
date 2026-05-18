from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.concurrency import run_in_threadpool
from pymongo import MongoClient
from urllib.parse import quote_plus
from typing import Dict, Any, Optional, List
import re

from fastapi.concurrency import run_in_threadpool

from src.core.logging import get_logger
from src.core.security.api_keys import invalidate_key_cache, verify_api_key
from src.core.security.rate_limiting import apply_tiered_limit
from src.workers.processor import get_processor
from src.core.config import settings
from src.core.observability.monitoring import get_request_tracker
from src.repositories import api_key_usage as _key_usage
from src.repositories import api_keys as _keys
from src.repositories import users as _users
import src.repositories.populate_seasons

logger = get_logger(__name__)

router = APIRouter()


async def require_admin_key(api_key: str = Depends(verify_api_key)) -> str:
    """Admin gate.

    Env keys are operator-level (treated as admin). A user-generated key
    is allowed only if it belongs to a user whose ``is_admin`` is true.
    """
    # Env keys always pass.
    if api_key in settings.allowed_api_keys_list or api_key in settings.premium_api_keys_list:
        return api_key
    # Dev bypass keeps working.
    if api_key == "dev-key" and settings.environment == "development":
        return api_key

    def _is_admin_db_key() -> bool:
        doc = _keys.find_active_by_hash(_keys.hash_key(api_key))
        if not doc:
            return False
        owner = _users.find_user_by_id(str(doc.get("owner_id")))
        return bool(owner and owner.get("is_admin"))

    if await run_in_threadpool(_is_admin_db_key):
        return api_key
    raise HTTPException(status_code=403, detail="Admin privileges required")

SUPPORTED_MONGO_VERSIONS = {"v1", "v2"}


def _build_mongodb_uri() -> str:
    """Build MongoDB URI using app settings."""
    encoded_password = quote_plus(settings.mongodb_password)
    return (
        f"mongodb://{settings.mongodb_user}:{encoded_password}@"
        f"{settings.mongodb_host}:{settings.mongodb_port}/?directConnection=true"
    )


def _collection_name_for_year(year: int, version: str) -> str:
    """Map year/version to the expected collection name."""
    suffix = "_processed_data_v2" if version == "v2" else "_processed_data"
    return f"{year}{suffix}"


def _extract_years_from_collections(collection_names: List[str], version: str) -> List[int]:
    """Extract available years from MongoDB collection names."""
    pattern = r"^(\d{4})_processed_data_v2$" if version == "v2" else r"^(\d{4})_processed_data$"
    years: List[int] = []
    for name in collection_names:
        match = re.match(pattern, name)
        if match:
            years.append(int(match.group(1)))
    return sorted(set(years))


def _estimate_payload_size(payload: Any) -> int:
    """Estimate payload record count for quick diagnostics."""
    if payload is None:
        return 0
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        return len(payload.keys())
    return 1


def _query_mongodb_coverage(
    *,
    version: str,
    year: Optional[int] = None,
    gp_id: Optional[str] = None,
    session_type: Optional[str] = None,
    include_empty: bool = False,
    limit_per_year: int = 200,
) -> Dict[str, Any]:
    """Collect session/data coverage from MongoDB for admin inspection."""
    client = None
    try:
        client = MongoClient(
            _build_mongodb_uri(),
            maxPoolSize=settings.mongodb_max_pool_size,
            minPoolSize=settings.mongodb_min_pool_size,
            serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
        )
        client.admin.command("ping")
        db = client[settings.mongodb_database]
        collection_names = db.list_collection_names()

        if year is not None:
            years_to_query = [year]
        else:
            years_to_query = _extract_years_from_collections(collection_names, version)

        all_session_types = set()
        all_plot_types = set()
        gp_results: List[Dict[str, Any]] = []
        total_docs_scanned = 0

        for current_year in years_to_query:
            collection_name = _collection_name_for_year(current_year, version)
            if collection_name not in collection_names:
                continue

            collection = db[collection_name]
            query: Dict[str, Any] = {}
            if gp_id:
                query["gp_id"] = gp_id

            projection = {
                "_id": 0,
                "year": 1,
                "round_nr": 1,
                "name": 1,
                "gp_id": 1,
                "sessions": 1,
            }

            cursor = collection.find(query, projection).sort("round_nr", 1)
            if not gp_id:
                cursor = cursor.limit(limit_per_year)

            for doc in cursor:
                total_docs_scanned += 1
                sessions = doc.get("sessions", []) or []
                session_summaries = []

                for session in sessions:
                    session_name = session.get("session_type", "unknown")
                    if session_type and session_name != session_type:
                        continue

                    raw_data_entries = session.get("data", []) or []
                    plot_details = []
                    for entry in raw_data_entries:
                        if not isinstance(entry, dict):
                            continue
                        data_type = entry.get("data_type", "unknown")
                        data_payload = entry.get("data")
                        plot_details.append(
                            {
                                "plot_type": data_type,
                                "record_estimate": _estimate_payload_size(data_payload),
                            }
                        )

                    plot_types = sorted({detail["plot_type"] for detail in plot_details})
                    has_data = len(plot_types) > 0

                    if not include_empty and not has_data:
                        continue

                    all_session_types.add(session_name)
                    all_plot_types.update(plot_types)

                    session_summaries.append(
                        {
                            "session_type": session_name,
                            "has_data": has_data,
                            "plot_count": len(plot_types),
                            "plot_types": plot_types,
                            "plot_details": plot_details,
                        }
                    )

                if not include_empty and not session_summaries:
                    continue

                gp_results.append(
                    {
                        "year": doc.get("year", current_year),
                        "round_nr": doc.get("round_nr"),
                        "gp_id": doc.get("gp_id"),
                        "event_name": doc.get("name"),
                        "session_count": len(session_summaries),
                        "sessions": session_summaries,
                    }
                )

        return {
            "version": version,
            "database": settings.mongodb_database,
            "years_scanned": years_to_query,
            "total_gp_documents_scanned": total_docs_scanned,
            "total_gp_results": len(gp_results),
            "available_session_types": sorted(all_session_types),
            "available_plot_types": sorted(all_plot_types),
            "grand_prix": gp_results,
        }
    finally:
        if client:
            client.close()


def _query_mongodb_overview(version: str) -> Dict[str, Any]:
    """Provide high-level collection stats for admin monitoring."""
    client = None
    try:
        client = MongoClient(
            _build_mongodb_uri(),
            maxPoolSize=settings.mongodb_max_pool_size,
            minPoolSize=settings.mongodb_min_pool_size,
            serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
        )
        client.admin.command("ping")
        db = client[settings.mongodb_database]
        collection_names = db.list_collection_names()
        years = _extract_years_from_collections(collection_names, version)

        year_stats = []
        total_gp_docs = 0
        for year in years:
            collection_name = _collection_name_for_year(year, version)
            if collection_name not in collection_names:
                continue

            collection = db[collection_name]
            gp_docs = collection.count_documents({})
            sessions_with_data = collection.count_documents({"sessions.data.0": {"$exists": True}})
            total_gp_docs += gp_docs

            year_stats.append(
                {
                    "year": year,
                    "collection": collection_name,
                    "gp_documents": gp_docs,
                    "gp_with_session_data": sessions_with_data,
                }
            )

        return {
            "version": version,
            "database": settings.mongodb_database,
            "collections_matched": len(year_stats),
            "total_gp_documents": total_gp_docs,
            "years": year_stats,
        }
    finally:
        if client:
            client.close()

# ============================================================================
# ADMIN & UTILITY ENDPOINTS
# ============================================================================

@router.post('/api/admin/populate-sessions', tags=["General"])
@apply_tiered_limit("standard")
async def admin_populate_sessions(request: Request, api_key: str = Depends(verify_api_key)):
    """
    Admin endpoint to populate session database from existing telemetry files
    Useful for initial setup or re-populating missing data
    """
    try:
        logger.info("Admin triggered session population from telemetry files")
        result = await run_in_threadpool(src.repositories.populate_seasons.main)
        return result
    except Exception as e:
        logger.error(f"Error populating sessions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to populate sessions")

@router.post('/api/admin/process-latest', tags=["General"])
@apply_tiered_limit("standard")
async def admin_process_latest(request: Request, api_key: str = Depends(verify_api_key)):
    """
    Admin endpoint to manually trigger processing of the latest session
    Useful for testing or forcing immediate processing
    """
    try:
        logger.info("Manual processing triggered for latest session")
        processor = get_processor()
        result = await processor.force_process_latest()
        return result
    except Exception as e:
        logger.error(f"Error in manual processing: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process latest session")

@router.get('/api/admin/processor-status', tags=["General"])
@apply_tiered_limit("standard")
async def admin_processor_status(request: Request, api_key: str = Depends(verify_api_key)):
    """
    Get detailed status of the background processor
    Shows what sessions have been processed and processor state
    """
    try:
        processor = get_processor()
        return {
            "running": processor.running,
            "check_interval": processor.check_interval,
            "processed_sessions_count": len(processor.processed_sessions),
            "processed_sessions": sorted(list(processor.processed_sessions))[-20:],  # Last 20
            "status": "healthy" if processor.running else "stopped"
        }
    except Exception as e:
        logger.error(f"Error getting processor status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get processor status")


@router.get('/api/admin/mongodb/overview', tags=["General"])
@apply_tiered_limit("standard")
async def admin_mongodb_overview(
    request: Request,
    version: str = Query("v2", description="Mongo data source version: v1 or v2"),
    api_key: str = Depends(verify_api_key),
):
    """
    Get high-level MongoDB coverage stats by year/collection for admin visibility.
    """
    try:
        if version not in SUPPORTED_MONGO_VERSIONS:
            raise HTTPException(status_code=400, detail="version must be 'v1' or 'v2'")

        return await run_in_threadpool(_query_mongodb_overview, version)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting MongoDB overview: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch MongoDB overview")


@router.get('/api/admin/mongodb/sessions-with-data', tags=["General"])
@apply_tiered_limit("standard")
async def admin_mongodb_sessions_with_data(
    request: Request,
    version: str = Query("v2", description="Mongo data source version: v1 or v2"),
    year: Optional[int] = Query(None, description="Filter by race year"),
    gp_id: Optional[str] = Query(None, description="Filter by GP ID (example: 2025_AUS)"),
    session_type: Optional[str] = Query(None, description="Filter by session type (FP1, FP2, Q, S, R, etc.)"),
    include_empty: bool = Query(False, description="Include sessions that exist but have no stored plot data"),
    limit_per_year: int = Query(200, ge=1, le=1000, description="Max GP documents to scan per year when gp_id is not provided"),
    api_key: str = Depends(verify_api_key),
):
    """
    Inspect MongoDB and list which sessions contain data, and which plot/data types exist per session.
    """
    try:
        if version not in SUPPORTED_MONGO_VERSIONS:
            raise HTTPException(status_code=400, detail="version must be 'v1' or 'v2'")

        if session_type:
            session_type = session_type.strip().upper()

        return await run_in_threadpool(
            _query_mongodb_coverage,
            version=version,
            year=year,
            gp_id=gp_id,
            session_type=session_type,
            include_empty=include_empty,
            limit_per_year=limit_per_year,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting MongoDB session coverage: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch MongoDB session coverage")


# ============================================================================
# USER / API-KEY MANAGEMENT
# ============================================================================

@router.get('/api/admin/users', tags=["General"])
@apply_tiered_limit("standard")
async def admin_list_users(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
    api_key: str = Depends(require_admin_key),
):
    users = await run_in_threadpool(_users.list_users, limit, skip)
    return {"count": len(users), "users": users}


@router.get('/api/admin/keys', tags=["General"])
@apply_tiered_limit("standard")
async def admin_list_keys(
    request: Request,
    owner_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    api_key: str = Depends(require_admin_key),
):
    keys = await run_in_threadpool(_keys.list_all, owner_id, limit)
    return {"count": len(keys), "keys": keys}


@router.post('/api/admin/keys/{key_id}/revoke', tags=["General"])
@apply_tiered_limit("standard")
async def admin_revoke_key(
    request: Request,
    key_id: str,
    api_key: str = Depends(require_admin_key),
):
    existing = await run_in_threadpool(_keys.find_by_id, key_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Key not found")
    revoked = await run_in_threadpool(_keys.revoke, key_id, None)
    if existing.get("key_hash"):
        invalidate_key_cache(existing["key_hash"])
    return {"id": key_id, "revoked": bool(revoked)}


@router.get('/api/admin/usage/top', tags=["General"])
@apply_tiered_limit("standard")
async def admin_usage_top(
    request: Request,
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(20, ge=1, le=200),
    api_key: str = Depends(require_admin_key),
):
    rows = await run_in_threadpool(_key_usage.top_keys, hours, limit)
    # Attach key metadata (prefix, owner, tier, label) by joining on key_hash.
    enriched: List[Dict[str, Any]] = []
    for row in rows:
        meta = await run_in_threadpool(_keys.find_active_by_hash, row["key_hash"])
        enriched.append({
            **row,
            "key_prefix": meta.get("key_prefix") if meta else None,
            "label": meta.get("label") if meta else None,
            "tier": meta.get("tier") if meta else None,
            "owner_id": str(meta.get("owner_id")) if meta else None,
        })
    return {"window_hours": hours, "count": len(enriched), "rows": enriched}


@router.get('/api/admin/usage/keys/{key_id}', tags=["General"])
@apply_tiered_limit("standard")
async def admin_usage_for_key(
    request: Request,
    key_id: str,
    hours: int = Query(24, ge=1, le=720),
    api_key: str = Depends(require_admin_key),
):
    doc = await run_in_threadpool(_keys.find_by_id, key_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Key not found")
    summary = await run_in_threadpool(_key_usage.summary_for_key, doc["key_hash"], hours)
    return {
        "key_id": key_id,
        "key_prefix": doc.get("key_prefix"),
        "owner_id": str(doc.get("owner_id")),
        "tier": doc.get("tier"),
        "label": doc.get("label"),
        "usage": summary,
    }


@router.get('/api/admin/performance', tags=["General"])
@apply_tiered_limit("standard")
async def admin_performance(
    request: Request,
    api_key: str = Depends(require_admin_key),
):
    tracker = get_request_tracker()
    return {
        "summary": tracker.get_summary(),
        "endpoints": tracker.get_endpoint_stats(),
    }
