"""
FastAPI application factory.

Builds the app, wires middleware, exception handlers, lifespan, and
routers. `server.py` at the repo root is a thin entrypoint that calls
`create_app()` and runs uvicorn.

Importing routers must happen *after* `init_limiter()` since rate-limit
decorators bind to the limiter at import time.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.api.docs_auth import setup_docs_auth
from src.api.schemas.health import HealthCheckResponse
from src.core.config import settings
from src.core.exceptions import (
    DataNotAvailableError,
    SessionNotFoundError,
    UpstreamUnavailableError,
)
from src.core.logging import get_logger, setup_logging
from src.core.observability.monitoring import (
    RequestTracingMiddleware,
    get_request_tracker,
    get_system_monitor,
    set_api_info,
)
from src.core.observability.analytics import SessionTracker
from src.core.security.rate_limiting import init_limiter
from src.workers.processor import (
    get_processor,
    start_background_processor,
    stop_background_processor,
)


SWAGGER_UI_PARAMETERS = {
    "filter": True,
    "persistAuthorization": True,
    "displayRequestDuration": True,
    "docExpansion": "none",
    "defaultModelsExpandDepth": -1,
    "tryItOutEnabled": True,
}

DESCRIPTION = """
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

TAGS_METADATA = [
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


def create_app() -> FastAPI:
    setup_logging(level=settings.log_level, log_file=settings.log_file)
    logger = get_logger(__name__)

    limiter = init_limiter()

    # Routers MUST be imported after init_limiter() (decorators bind at import).
    from src.api.routers.admin import router as admin_router
    from src.api.routers.analysis_v1 import router as analysis_router_v1
    from src.api.routers.analysis_v2 import router as analysis_router_v2
    from src.api.routers.circuits_api import router as circuits_api_router
    from src.api.routers.drivers_api import router as drivers_api_router
    from src.api.routers.monitoring import router as monitoring_router
    from src.api.routers.seasonal_v1 import router as seasonal_router_v1
    from src.api.routers.seasonal_v2 import router as seasonal_router_v2
    from src.api.routers.teams_api import router as teams_api_router

    try:
        session_tracker = SessionTracker()
        logger.info("Session tracker initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize session tracker: {e}")
        session_tracker = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(f"Starting {settings.app_name} v{settings.app_version}")
        logger.info(f"Environment: {settings.environment}")
        logger.info(f"CORS Origins: {settings.cors_origins_list}")
        logger.info(
            "Rate limiting - "
            f"Public: {settings.rate_limit_public_per_minute}/min, "
            f"Standard: {settings.rate_limit_standard_per_minute}/min, "
            f"Premium: {settings.rate_limit_premium_per_minute}/min"
        )

        set_api_info(settings.app_name, settings.app_version, settings.environment)
        get_request_tracker()
        get_system_monitor()

        processor_task = None
        if settings.enable_background_processor:
            logger.info(f"Starting background processor (interval: {settings.processor_check_interval}s)")
            processor_task = asyncio.create_task(start_background_processor())
        else:
            logger.info("Background processor disabled in configuration")

        yield

        logger.info(f"Shutting down {settings.app_name}")
        if processor_task:
            await stop_background_processor()
            processor_task.cancel()
            try:
                await processor_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version=settings.app_version,
        lifespan=lifespan,
        contact={
            "name": "Turn One Hub Support",
            "url": "https://turnonehub.com",
            "email": "contact@t1f1.com",
        },
        license_info={"name": "Proprietary / Internal Use"},
        openapi_tags=TAGS_METADATA,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    setup_docs_auth(app, settings, SWAGGER_UI_PARAMETERS)
    app.state.limiter = limiter
    app.state.session_tracker = session_tracker
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(RequestTracingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_methods_list,
        allow_headers=[settings.cors_allow_headers],
    )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error(
            f"[{request_id}] Unhandled exception on {request.url.path}: {exc}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "Internal server error",
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        request_id = getattr(request.state, "request_id", "unknown")
        logger.warning(f"[{request_id}] HTTP {exc.status_code} on {request.url.path}: {exc.detail}")

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
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    @app.exception_handler(DataNotAvailableError)
    async def data_not_available_handler(request: Request, exc: DataNotAvailableError):
        request_id = getattr(request.state, "request_id", "unknown")
        logger.warning(
            f"[{request_id}] Data not available on {request.url.path}: "
            f"year={exc.year} gp={exc.gp} session={exc.session} "
            f"sources_tried={exc.sources_tried} reason={exc.reason}"
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "300"},
            content={
                "error": "data_not_available",
                "detail": exc.reason or "Data for the requested session is not yet available upstream.",
                "year": exc.year,
                "gp": exc.gp,
                "session": exc.session,
                "sources_tried": exc.sources_tried,
                "retry_after_seconds": 300,
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    @app.exception_handler(UpstreamUnavailableError)
    async def upstream_unavailable_handler(request: Request, exc: UpstreamUnavailableError):
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error(
            f"[{request_id}] Upstream unavailable on {request.url.path}: "
            f"source={exc.source} reason={exc.reason}"
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "60"},
            content={
                "error": "upstream_unavailable",
                "detail": exc.reason or f"Upstream {exc.source} is unavailable.",
                "source": exc.source,
                "retry_after_seconds": 60,
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    @app.exception_handler(SessionNotFoundError)
    async def session_not_found_handler(request: Request, exc: SessionNotFoundError):
        request_id = getattr(request.state, "request_id", "unknown")
        logger.info(
            f"[{request_id}] Session not found on {request.url.path}: "
            f"year={exc.year} gp={exc.gp} session={exc.session}"
        )
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": "session_not_found",
                "detail": exc.reason or "The requested session does not exist.",
                "year": exc.year,
                "gp": exc.gp,
                "session": exc.session,
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        request_id = getattr(request.state, "request_id", "unknown")
        logger.warning(f"[{request_id}] Validation error on {request.url.path}: {exc}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": str(exc),
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    @app.get("/", tags=["General"])
    async def welcome():
        return {
            "message": "Welcome to the T1API",
            "version": settings.app_version,
            "docs": "https://docs.t1f1.com",
            "health": "/api/health",
        }

    @app.get("/api/health", tags=["General"], response_model=HealthCheckResponse)
    @limiter.limit(f"{settings.rate_limit_public_per_minute}/minute")
    async def health_check(request: Request):
        try:
            processor = get_processor()
            checks = {
                "api": "healthy",
                "session_tracker": "healthy" if session_tracker else "unavailable",
                "background_processor": "running" if processor.running else "stopped",
                "processed_sessions": len(processor.processed_sessions),
                "environment": settings.environment,
            }
            return {
                "status": "healthy",
                "version": settings.app_version,
                "environment": settings.environment,
                "checks": checks,
            }
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            raise HTTPException(status_code=503, detail="Service unhealthy")

    app.include_router(monitoring_router)
    app.include_router(admin_router)
    app.include_router(analysis_router_v1)
    app.include_router(seasonal_router_v1)
    app.include_router(analysis_router_v2)
    app.include_router(seasonal_router_v2)
    app.include_router(drivers_api_router)
    app.include_router(teams_api_router)
    app.include_router(circuits_api_router)

    return app
