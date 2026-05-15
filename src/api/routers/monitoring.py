from fastapi import APIRouter, Request, HTTPException, Depends, Query, status
from fastapi.responses import Response
from datetime import datetime

from src.core.logging import get_logger
from src.core.security.api_keys import verify_api_key
from src.core.security.rate_limiting import apply_tiered_limit, get_limiter
from src.core.observability.monitoring import (
    get_request_tracker,
    get_system_monitor,
    get_prometheus_metrics,
    get_prometheus_content_type,
    ACTIVE_REQUESTS
)
from src.core.config import settings

logger = get_logger(__name__)

router = APIRouter()

# ============================================================================
# MONITORING & OBSERVABILITY ENDPOINTS
# ============================================================================

@router.get('/metrics', tags=["Monitoring"], include_in_schema=True)
async def metrics():
    """
    Prometheus metrics endpoint
    Returns metrics in Prometheus text format for scraping
    """
    return Response(
        content=get_prometheus_metrics(),
        media_type=get_prometheus_content_type()
    )

@router.get('/api/monitoring/system', tags=["Monitoring"])
@apply_tiered_limit("standard")
async def get_system_metrics(request: Request, api_key: str = Depends(verify_api_key)):
    """
    Get real-time system metrics
    Shows CPU, memory, disk, network usage and uptime
    """
    try:
        monitor = get_system_monitor()
        metrics = monitor.get_metrics()
        return metrics
    except Exception as e:
        logger.error(f"Error getting system metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get system metrics")

@router.get('/api/monitoring/requests', tags=["Monitoring"])
@apply_tiered_limit("standard")
async def get_recent_requests(
    request: Request,
    limit: int = Query(100, ge=1, le=1000, description="Number of recent requests to return"),
    api_key: str = Depends(verify_api_key)
):
    """
    Get recent requests with full traceability
    Each request includes: ID, timestamp, endpoint, status, duration, client info
    """
    try:
        tracker = get_request_tracker()
        requests = tracker.get_recent_requests(limit)
        return {
            "count": len(requests),
            "limit": limit,
            "requests": requests
        }
    except Exception as e:
        logger.error(f"Error getting recent requests: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get recent requests")

@router.get('/api/monitoring/errors', tags=["Monitoring"])
@apply_tiered_limit("standard")
async def get_recent_errors(
    request: Request,
    limit: int = Query(50, ge=1, le=500, description="Number of recent errors to return"),
    api_key: str = Depends(verify_api_key)
):
    """
    Get recent errors with full details
    Includes stack traces, request context, and error types
    """
    try:
        tracker = get_request_tracker()
        errors = tracker.get_recent_errors(limit)
        return {
            "count": len(errors),
            "limit": limit,
            "errors": errors
        }
    except Exception as e:
        logger.error(f"Error getting recent errors: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get recent errors")

@router.get('/api/monitoring/stats', tags=["Monitoring"])
@apply_tiered_limit("standard")
async def get_endpoint_stats(request: Request, api_key: str = Depends(verify_api_key)):
    """
    Get statistics per endpoint
    Shows request counts, error rates, and performance metrics for each endpoint
    """
    try:
        tracker = get_request_tracker()
        stats = tracker.get_endpoint_stats()
        summary = tracker.get_summary()
        
        return {
            "summary": summary,
            "endpoints": stats
        }
    except Exception as e:
        logger.error(f"Error getting endpoint stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get endpoint stats")

@router.get('/api/monitoring/load', tags=["Monitoring"])
@apply_tiered_limit("standard")
async def get_current_load(request: Request, api_key: str = Depends(verify_api_key)):
    """
    Get current load and active requests
    Real-time view of system load and active API requests
    """
    try:
        monitor = get_system_monitor()
        tracker = get_request_tracker()
        
        # Get system metrics
        system_metrics = monitor.get_metrics()
        
        # Get request statistics
        summary = tracker.get_summary()
        
        # Get active requests count from Prometheus gauge
        active_count = ACTIVE_REQUESTS._value.get()
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "active_requests": active_count,
            "system": {
                "cpu_percent": system_metrics.get("cpu", {}).get("process_percent", 0),
                "memory_percent": system_metrics.get("memory", {}).get("process_percent", 0),
                "memory_mb": system_metrics.get("memory", {}).get("process_mb", 0),
            },
            "requests": {
                "total": summary.get("total_requests", 0),
                "total_errors": summary.get("total_errors", 0),
                "error_rate_percent": summary.get("error_rate_percent", 0),
            },
            "uptime": system_metrics.get("uptime_human", "unknown")
        }
    except Exception as e:
        logger.error(f"Error getting current load: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get current load")

@router.get('/api/monitoring/health-detailed', tags=["Monitoring"])
@apply_tiered_limit("standard")
async def get_detailed_health(request: Request, api_key: str = Depends(verify_api_key)):
    """
    Comprehensive health check with full observability
    Combines system metrics, request stats, and service health in one endpoint
    """
    try:
        from src.workers.processor import get_processor
        from src.core.observability.analytics import SessionTracker
        
        # Get processor status
        processor = get_processor()
        
        # Get system metrics
        monitor = get_system_monitor()
        system_metrics = monitor.get_metrics()
        
        # Get request tracker stats
        tracker = get_request_tracker()
        summary = tracker.get_summary()
        
        # Get active requests
        active_count = ACTIVE_REQUESTS._value.get()
        
        # Check session tracker
        try:
            session_tracker = SessionTracker()
            session_tracker_status = "healthy"
        except:
            session_tracker_status = "unavailable"
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "version": settings.app_version,
            "environment": settings.environment,
            "services": {
                "api": "healthy",
                "session_tracker": session_tracker_status,
                "background_processor": "running" if processor.running else "stopped",
                "monitoring": "active",
                "request_tracking": "active"
            },
            "system": system_metrics,
            "requests": {
                "active": active_count,
                "total": summary.get("total_requests", 0),
                "total_errors": summary.get("total_errors", 0),
                "error_rate_percent": summary.get("error_rate_percent", 0),
                "tracked_in_memory": summary.get("tracked_requests_in_memory", 0),
                "unique_endpoints": summary.get("unique_endpoints", 0)
            },
            "background_processor": {
                "running": processor.running,
                "processed_sessions": len(processor.processed_sessions)
            }
        }
    except Exception as e:
        logger.error(f"Detailed health check failed: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Service unhealthy")

# ============================================================================
# TEST & DIAGNOSTICS ENDPOINTS
# ============================================================================

@router.get('/api/test/ping', tags=["Monitoring"])
@apply_tiered_limit("public")
async def test_ping(request: Request):
    """
    Simple ping endpoint for testing
    Returns request ID and timing info - useful for load testing
    """
    request_id = getattr(request.state, 'request_id', 'unknown')
    return {
        "status": "pong",
        "request_id": request_id,
        "timestamp": datetime.utcnow().isoformat(),
        "message": "API is responding"
    }

@router.get('/api/test/error', tags=["Monitoring"])
@apply_tiered_limit("standard")
async def test_error(
    request: Request,
    error_type: str = Query("500", description="Error type: 400, 404, 500, or exception"),
    api_key: str = Depends(verify_api_key)
):
    """
    Test error handling and tracing
    Useful for testing error logging and monitoring systems
    """
    request_id = getattr(request.state, 'request_id', 'unknown')
    
    if error_type == "400":
        raise HTTPException(status_code=400, detail=f"[{request_id}] Test bad request error")
    elif error_type == "404":
        raise HTTPException(status_code=404, detail=f"[{request_id}] Test not found error")
    elif error_type == "500":
        raise HTTPException(status_code=500, detail=f"[{request_id}] Test internal server error")
    elif error_type == "exception":
        raise ValueError(f"[{request_id}] Test exception for monitoring")
    else:
        raise HTTPException(status_code=400, detail="Invalid error_type. Use: 400, 404, 500, or exception")

@router.get('/api/diagnostics', tags=["Monitoring"])
@apply_tiered_limit("standard")
async def get_diagnostics(request: Request, api_key: str = Depends(verify_api_key)):
    """
    Complete diagnostics overview
    Everything you need to know about the API status in one endpoint
    """
    try:
        from src.workers.processor import get_processor
        from src.core.observability.analytics import SessionTracker
        
        request_id = getattr(request.state, 'request_id', 'unknown')
        
        # Get all monitoring data
        monitor = get_system_monitor()
        tracker = get_request_tracker()
        processor = get_processor()
        
        system_metrics = monitor.get_metrics()
        request_summary = tracker.get_summary()
        endpoint_stats = tracker.get_endpoint_stats()
        recent_errors = tracker.get_recent_errors(10)
        active_count = ACTIVE_REQUESTS._value.get()
        
        # Get top endpoints by request count
        top_endpoints = sorted(
            [(k, v['total_requests']) for k, v in endpoint_stats.items()],
            key=lambda x: x[1],
            reverse=True
        )[:10]
        
        # Get endpoints with highest error rates
        error_endpoints = sorted(
            [(k, v['error_rate']) for k, v in endpoint_stats.items() if v['error_rate'] > 0],
            key=lambda x: x[1],
            reverse=True
        )[:10]
        
        # Check session tracker
        try:
            session_tracker = SessionTracker()
            session_tracker_status = "healthy"
        except:
            session_tracker_status = "unavailable"
        
        return {
            "request_id": request_id,
            "timestamp": datetime.utcnow().isoformat(),
            "status": "healthy",
            "version": settings.app_version,
            "environment": settings.environment,
            
            "system": {
                "uptime": system_metrics.get("uptime_human", "unknown"),
                "cpu_percent": system_metrics.get("cpu", {}).get("process_percent", 0),
                "memory_mb": system_metrics.get("memory", {}).get("process_mb", 0),
                "memory_percent": system_metrics.get("memory", {}).get("process_percent", 0),
            },
            
            "requests": {
                "active": active_count,
                "total": request_summary.get("total_requests", 0),
                "total_errors": request_summary.get("total_errors", 0),
                "error_rate_percent": request_summary.get("error_rate_percent", 0),
                "unique_endpoints": request_summary.get("unique_endpoints", 0),
                "tracked_in_memory": request_summary.get("tracked_requests_in_memory", 0),
            },
            
            "top_endpoints": [
                {"endpoint": endpoint, "requests": count}
                for endpoint, count in top_endpoints
            ],
            
            "error_prone_endpoints": [
                {"endpoint": endpoint, "error_rate_percent": rate}
                for endpoint, rate in error_endpoints
            ],
            
            "recent_errors_sample": [
                {
                    "request_id": err.get("request_id"),
                    "endpoint": err.get("endpoint"),
                    "status_code": err.get("status_code"),
                    "timestamp": err.get("timestamp"),
                    "error_type": err.get("error", {}).get("type") if err.get("error") else None
                }
                for err in recent_errors
            ],
            
            "background_processor": {
                "running": processor.running,
                "processed_sessions": len(processor.processed_sessions),
            },
            
            "services": {
                "api": "healthy",
                "monitoring": "active",
                "request_tracking": "active",
                "session_tracker": session_tracker_status,
                "background_processor": "running" if processor.running else "stopped",
            }
        }
    except Exception as e:
        logger.error(f"Error getting diagnostics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get diagnostics")
