import secrets

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager
import uvicorn
import asyncio
from datetime import datetime

# Import configuration and utilities
from src.utils.config import settings
from src.utils.logger import get_logger, setup_logging
from src.utils.validation import HealthCheckResponse
from src.utils.background_processor import get_processor, start_background_processor, stop_background_processor
from src.utils.rate_limiting import init_limiter

# Import monitoring and observability
from src.utils.monitoring import (
    RequestTracingMiddleware,
    get_request_tracker,
    get_system_monitor,
    set_api_info,
)

# Import session tracker
from src.utils.session_tracker import SessionTracker

#Import docs auth page
from docs_auth import setup_docs_auth

# Setup logging
setup_logging(level=settings.log_level, log_file=settings.log_file)
logger = get_logger(__name__)

# Initialize rate limiter BEFORE importing routers (they use it in decorators)
limiter = init_limiter()

# Import routers (must be after limiter initialization)
from src.api.monitoring import router as monitoring_router
from src.api.admin import router as admin_router
from src.api.v1.analysis import router as analysis_router_v1
from src.api.v1.seasonal import router as seasonal_router_v1
# and v2 routers
from src.api.v2.analysis import router as analysis_router_v2
from src.api.v2.seasonal import router as seasonal_router_v2
# Static data router for drivers and teams
from src.api.static.drivers_api import router as drivers_api_router
from src.api.static.teams_api import router as teams_api_router
from src.api.static.circuits_api import router as circuits_api_router

# Initialize session tracker
try:
    session_tracker = SessionTracker()
    logger.info("Session tracker initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize session tracker: {e}")
    session_tracker = None

# --- Metadata & Tags ---
description = """
# T1API - Formula 1 Telemetry Analysis

This API provides advanced telemetry analysis for Formula 1 sessions. 
It powers the dashboards at **t1f1.com** and **turnonehub.com**.

## Authentication
All endpoints require an API key passed via the `X-API-Key` header.

## Features
* **Daily Data**: High-level daily summary plots
* **Telemetry Comparison**: Throttle, brake, and speed comparisons between drivers
* **Qualifying Analysis**: Lap time distributions and top speed charts
* **Dashboards**: Aggregated data for specific race sessions

## Rate Limits
The API implements tiered rate limiting:
- **Public**: 30 requests/minute (unauthenticated)
- **Standard**: 100 requests/minute (with API key)
- **Premium**: 300 requests/minute (premium API keys)
- **Data endpoints**: 60 requests/minute (separate counter)

## Usage
All endpoints support both plot (PNG) and data (JSON) responses.
"""

tags_metadata = [
    {"name": "General", "description": "System health, welcome messages, and daily summaries"},
    {"name": "Monitoring", "description": "Observability, metrics, request tracing, and system monitoring"},
    {"name": "API v1", "description": "Version 1 API endpoints - Analysis and seasonal data"},
    {"name": "API v2", "description": "Version 2 API endpoints - Analysis and seasonal data"},
    {"name": "Latest Session", "description": "Aggregated data for the main frontend dashboard"},
    {"name": "Seasonal Data", "description": "Season-specific data including drivers, teams, and race schedules"},
    {"name": "Simple Analysis", "description": "Analysis focused on general session stats or single driver metrics"},
    {"name": "Driver Comparison", "description": "Head-to-head driver comparisons"},
    {"name": "Static", "description": "Static data endpoints for drivers, teams, and race schedules"},
]

# --- Lifespan Context Manager ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events
    Handles background tasks like the automatic data processor
    """
    # Startup
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"CORS Origins: {settings.cors_origins_list}")
    logger.info(f"Rate limiting - Public: {settings.rate_limit_public_per_minute}/min, Standard: {settings.rate_limit_standard_per_minute}/min, Premium: {settings.rate_limit_premium_per_minute}/min")
    
    # Initialize monitoring
    logger.info("🔍 Initializing observability and monitoring...")
    set_api_info(settings.app_name, settings.app_version, settings.environment)
    get_request_tracker()  # Initialize request tracker
    get_system_monitor()  # Initialize system monitor
    logger.info("✅ Monitoring initialized - full traceability enabled")
    
    # Start background processor
    processor_task = None
    if settings.enable_background_processor:
        logger.info(f"🚀 Starting background data processor (check interval: {settings.processor_check_interval}s)...")
        processor_task = asyncio.create_task(start_background_processor())
        logger.info("✅ Background processor started - will auto-process completed sessions")
    else:
        logger.info("ℹ️  Background processor disabled in configuration")
    
    yield  # Server runs
    
    # Shutdown
    logger.info(f"Shutting down {settings.app_name}")
    
    # Stop background processor
    if processor_task:
        logger.info("⏹️  Stopping background processor...")
        await stop_background_processor()
        processor_task.cancel()
        try:
            await processor_task
        except asyncio.CancelledError:
            pass
        logger.info("✅ Background processor stopped")


SWAGGER_UI_PARAMETERS = {
    "filter": True,
    "persistAuthorization": True,
    "displayRequestDuration": True,
    "docExpansion": "none",
    "defaultModelsExpandDepth": -1,
    "tryItOutEnabled": True,
}

# --- Initialize App ---
app = FastAPI(
    title=settings.app_name,
    description=description,
    version=settings.app_version,
    lifespan=lifespan,  # Use lifespan instead of on_event
    contact={
        "name": "Turn One Hub Support",
        "url": "https://turnonehub.com",
        "email": "contact@t1f1.com",
    },
    license_info={"name": "Proprietary / Internal Use"},
    openapi_tags=tags_metadata,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
setup_docs_auth(app, settings, SWAGGER_UI_PARAMETERS)
# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Add monitoring middleware (MUST be added BEFORE CORS for accurate metrics)
app.add_middleware(RequestTracingMiddleware)

# Add CORS middleware with proper configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_methods_list,
    allow_headers=[settings.cors_allow_headers],
)

# --- Exception Handlers ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle all unhandled exceptions with full traceability"""
    request_id = getattr(request.state, 'request_id', 'unknown')
    logger.error(f"[{request_id}] Unhandled exception on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "request_id": request_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions with logging and request ID"""
    request_id = getattr(request.state, 'request_id', 'unknown')
    logger.warning(f"[{request_id}] HTTP {exc.status_code} on {request.url.path}: {exc.detail}")

    # Preserve browser Basic Auth challenge behavior for docs endpoints.
    from starlette.responses import Response
    if (
        request.url.path in {"/docs", "/openapi.json", "/redoc"}
        and exc.status_code == status.HTTP_401_UNAUTHORIZED
    ):
        return Response(
            content="Unauthorized",
            status_code=401,
            media_type="text/plain",
            headers={"WWW-Authenticate": "Basic", "Cache-Control": "no-store"},
    )

    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "detail": exc.detail,
            "request_id": request_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    )

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Handle validation errors with request ID"""
    request_id = getattr(request.state, 'request_id', 'unknown')
    logger.warning(f"[{request_id}] Validation error on {request.url.path}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "detail": str(exc),
            "request_id": request_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    )

# --- Endpoints ---

@app.get('/', tags=["General"])
async def welcome():
    """Welcome message"""
    return {
        "message": "Welcome to the T1API",
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/api/health"
    }

@app.get('/api/health', tags=["General"], response_model=HealthCheckResponse)
@limiter.limit(f"{settings.rate_limit_public_per_minute}/minute")
async def health_check(request: Request):
    """Comprehensive health check - Public endpoint with generous rate limit"""
    try:
        processor = get_processor()
        checks = {
            "api": "healthy",
            "session_tracker": "healthy" if session_tracker else "unavailable",
            "background_processor": "running" if processor.running else "stopped",
            "processed_sessions": len(processor.processed_sessions),
            "environment": settings.environment
        }
        
        return {
            "status": "healthy",
            "version": settings.app_version,
            "environment": settings.environment,
            "checks": checks
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service unhealthy")

# ============================================================================
# INCLUDE API ROUTERS
# ============================================================================

# Include monitoring router (paths unchanged: /metrics, /api/monitoring/*, /api/test/*, /api/diagnostics)
app.include_router(monitoring_router)

# Include admin router (paths unchanged: /api/admin/*)
app.include_router(admin_router)

# Include v1 analysis router (paths prefixed with /api/v1)
app.include_router(analysis_router_v1)

# Include v1 seasonal router (paths prefixed with /api/v1)
app.include_router(seasonal_router_v1)

# Include v2 analysis router (paths prefixed with /api/v2)
app.include_router(analysis_router_v2)

# Include v2 seasonal router (paths prefixed with /api/v2)
app.include_router(seasonal_router_v2)

# Include static data router (paths prefixed with /api/static)
app.include_router(drivers_api_router)
app.include_router(teams_api_router)
app.include_router(circuits_api_router)

if __name__ == '__main__':
    logger.info(f"Starting server on {settings.host}:{settings.docker_exposed_port}")
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.docker_exposed_port,
        log_level=settings.log_level.lower()
    )