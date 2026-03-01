from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.concurrency import run_in_threadpool

from src.utils.logger import get_logger
from src.utils.auth import verify_api_key
from src.utils.rate_limiting import apply_tiered_limit
from src.utils.background_processor import get_processor
import src.utils.database.populate_seasons

logger = get_logger(__name__)

router = APIRouter()

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
        result = await run_in_threadpool(src.utils.database.populate_seasons.main)
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
