from fastapi import FastAPI, Query, HTTPException, Request, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager
import uvicorn
import asyncio
from datetime import datetime
import traceback
from typing import Optional

# Import configuration and utilities
from src.utils.config import settings
from src.utils.logger import get_logger, setup_logging
from src.utils.auth import verify_api_key, get_optional_api_key, get_api_key_tier
from src.utils.validation import (
    SessionParams, DriverComparisonParams, DriverSessionParams,
    ErrorResponse, HealthCheckResponse
)
from src.utils.background_processor import get_processor, start_background_processor, stop_background_processor

# Import custom modules
import src.utils.database.populate_seasons
from src.scripts.simple.top_speed import TopSpeedPlot, TopSpeedData
from src.scripts.simple.throttle_comparison import ThrottleComp, ThrottleCompData
from src.scripts.quali_practice.qulifying_results import QualiResults, QualiResultsData
from src.scripts.quali_practice.track_comparison_2drivers import TrackComparisonPlot, TrackComparisonData
from src.scripts.quali_practice.throttleBrake_comparison_2drivers import throttle_graph, throttle_graph_data
from src.scripts.simple.laptimes_distribution import LatimesDistribution
from src.utils.session_tracker import SessionTracker
from src.utils.latest_session import get_latest_finished_session
from src.scripts.complex.latest_session_analised import latest_session_analised

# Setup logging
setup_logging(level=settings.log_level, log_file=settings.log_file)
logger = get_logger(__name__)

# Custom rate limit key function that includes API tier
def get_rate_limit_key(request: Request) -> str:
    """
    Generate rate limit key based on IP and API tier
    This allows different rate limits for different authentication levels
    """
    api_key = request.headers.get("X-API-Key")
    ip_address = get_remote_address(request)
    
    if not api_key:
        return f"public:{ip_address}"
    
    if api_key in settings.premium_api_keys_list:
        return f"premium:{api_key}"
    elif api_key in settings.allowed_api_keys_list:
        return f"standard:{api_key}"
    else:
        return f"public:{ip_address}"

# Initialize rate limiter with no default limits (we'll set per-endpoint)
limiter = Limiter(key_func=get_rate_limit_key)

# Helper function to apply tiered rate limits
def apply_tiered_limit(endpoint_type: str = "standard"):
    """
    Apply tiered rate limiting based on endpoint type
    
    endpoint_type can be:
    - "public": Health checks, docs (30/min, 500/hour for unauthenticated)
    - "standard": Regular API endpoints (100/min standard, 300/min premium)
    - "data": Data-intensive endpoints (60/min for all, but separate counter)
    """
    if endpoint_type == "public":
        return limiter.limit(
            f"{settings.rate_limit_public_per_minute}/minute;"
            f"{settings.rate_limit_public_per_hour}/hour"
        )
    elif endpoint_type == "data":
        return limiter.limit(
            f"{settings.rate_limit_data_per_minute}/minute;"
            f"{settings.rate_limit_data_per_hour}/hour",
            key_func=lambda request: f"data:{get_rate_limit_key(request)}"
        )
    else:  # standard
        # For standard endpoints, we use multiple limits based on tier
        # Premium keys get 300/min, standard gets 100/min, public gets 30/min
        return limiter.limit(
            f"{settings.rate_limit_premium_per_minute}/minute;"
            f"{settings.rate_limit_premium_per_hour}/hour"
        )

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
    {"name": "Latest Session", "description": "Aggregated data for the main frontend dashboard"},
    {"name": "Seasonal Data", "description": "Season-specific data including drivers, teams, and race schedules"},
    {"name": "Simple Analysis", "description": "Analysis focused on general session stats or single driver metrics"},
    {"name": "Driver Comparison", "description": "Head-to-head driver comparisons"},
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
    docs_url='/docs',
    redoc_url='/redoc'
)

# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
    """Handle all unhandled exceptions"""
    logger.error(f"Unhandled exception on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error", "timestamp": datetime.utcnow().isoformat()}
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions with logging"""
    logger.warning(f"HTTP {exc.status_code} on {request.url.path}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "timestamp": datetime.utcnow().isoformat()}
    )

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Handle validation errors"""
    logger.warning(f"Validation error on {request.url.path}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc), "timestamp": datetime.utcnow().isoformat()}
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

@app.post('/api/admin/populate-sessions', tags=["General"])
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

@app.post('/api/admin/process-latest', tags=["General"])
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

@app.get('/api/admin/processor-status', tags=["General"])
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

@app.get('/api/daily-data', tags=["General"])
@apply_tiered_limit("standard")
async def daily_data(request: Request, api_key: str = Depends(verify_api_key)):
    """Generate daily data summary plot (Standard rate limit)"""
    try:
        logger.info("Generating daily data plot")
        from src.utils.daily_plot_data import DailyPlotData
        output = await run_in_threadpool(lambda: DailyPlotData().generate_daily_plot())
        return output
    except Exception as e:
        logger.error(f"Error generating daily data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate daily data")

@app.get('/api/dashboard', tags=["Latest Session"])
@apply_tiered_limit("data")
async def get_dashboard_data(request: Request, api_key: str = Depends(verify_api_key)):
    """
    Get main latest session data.
    Automatically detects the most recent completed session.
    """
    try:
        logger.info("Fetching dashboard data for latest session")
        latest_session = await run_in_threadpool(get_latest_finished_session)
        
        if not latest_session:
            logger.warning("No finished sessions found")
            raise HTTPException(status_code=404, detail="No finished sessions found")
        
        result = await run_in_threadpool(latest_session_analised, latest_session)
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching dashboard data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch dashboard data")

# --- Seasonal Data Endpoints ---

@app.get('/api/seasons', tags=["Seasonal Data"])
@apply_tiered_limit("standard")
async def get_available_seasons(
    request: Request,
    api_key: str = Depends(verify_api_key)
):
    """Get list of all available seasons"""
    try:
        logger.info("Fetching available seasons")
        from src.utils.database.seasonal_data import get_available_seasons
        seasons = await run_in_threadpool(get_available_seasons)
        return {"seasons": seasons}
    except Exception as e:
        logger.error(f"Error fetching available seasons: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch available seasons")

@app.get('/api/season/{year}', tags=["Seasonal Data"])
@apply_tiered_limit("standard")
async def get_season_summary(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key)
):
    """Get complete season data including drivers, teams, and races"""
    try:
        logger.info(f"Fetching complete season data for {year}")
        from src.utils.database.seasonal_data import get_season_summary
        season_data = await run_in_threadpool(lambda: get_season_summary(year))
        
        if not season_data:
            raise HTTPException(status_code=404, detail=f"Season {year} not found")
        
        return season_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching season summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch season summary")

@app.get('/api/season/{year}/drivers', tags=["Seasonal Data"])
@apply_tiered_limit("standard")
async def get_season_drivers(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key)
):
    """Get list of drivers for a specific season"""
    try:
        logger.info(f"Fetching drivers for season {year}")
        from src.utils.database.seasonal_data import get_drivers_for_season
        drivers = await run_in_threadpool(lambda: get_drivers_for_season(year))
        
        if not drivers:
            raise HTTPException(status_code=404, detail=f"No drivers found for season {year}")
        
        return {"year": year, "drivers": drivers}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching season drivers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch season drivers")

@app.get('/api/season/{year}/teams', tags=["Seasonal Data"])
@apply_tiered_limit("standard")
async def get_season_teams(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key)
):
    """Get list of teams for a specific season"""
    try:
        logger.info(f"Fetching teams for season {year}")
        from src.utils.database.seasonal_data import get_teams_for_season
        teams = await run_in_threadpool(lambda: get_teams_for_season(year))
        
        if not teams:
            raise HTTPException(status_code=404, detail=f"No teams found for season {year}")
        
        return {"year": year, "teams": teams}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching season teams: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch season teams")

@app.get('/api/season/{year}/driver/{driver_code}', tags=["Seasonal Data"])
@apply_tiered_limit("standard")
async def get_driver_info(
    request: Request,
    year: int,
    driver_code: str,
    api_key: str = Depends(verify_api_key)
):
    """Get specific driver information for a season"""
    try:
        logger.info(f"Fetching driver {driver_code} for season {year}")
        from src.utils.database.seasonal_data import get_driver_by_code
        driver = await run_in_threadpool(lambda: get_driver_by_code(year, driver_code.upper()))
        
        if not driver:
            raise HTTPException(status_code=404, detail=f"Driver {driver_code} not found for season {year}")
        
        return driver
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching driver info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch driver info")

@app.get('/api/season/{year}/team/{team_name}', tags=["Seasonal Data"])
@apply_tiered_limit("standard")
async def get_team_info(
    request: Request,
    year: int,
    team_name: str,
    api_key: str = Depends(verify_api_key)
):
    """Get specific team information for a season"""
    try:
        logger.info(f"Fetching team {team_name} for season {year}")
        from src.utils.database.seasonal_data import get_team_by_name
        team = await run_in_threadpool(lambda: get_team_by_name(year, team_name))
        
        if not team:
            raise HTTPException(status_code=404, detail=f"Team {team_name} not found for season {year}")
        
        return team
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching team info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch team info")
    
# --- Simple Analysis ---

@app.get('/api/top-speed-plot', tags=["Simple Analysis"])
@apply_tiered_limit("standard")
async def quali_top_speed_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Generate PNG plot of top speeds"""
    try:
        logger.info(f"Generating top speed plot: Y{year} GP{gp} {session}")
        output_path = await run_in_threadpool(TopSpeedPlot, year, gp, session)
        
        if session_tracker:
            session_tracker.track_session('top-speed', year, gp, session)
        
        return FileResponse(output_path, media_type='image/png')
    except FileNotFoundError:
        logger.error(f"Plot file not found: Y{year} GP{gp} {session}")
        raise HTTPException(status_code=404, detail="Plot not found")
    except Exception as e:
        logger.error(f"Error generating top speed plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")

@app.get('/api/top-speed-data', tags=["Simple Analysis"])
@apply_tiered_limit("data")
async def quali_top_speed_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Get raw JSON data for top speed analysis"""
    try:
        logger.info(f"Fetching top speed data: Y{year} GP{gp} {session}")
        result = await run_in_threadpool(TopSpeedData, year, gp, session)
        
        if session_tracker:
            session_tracker.track_session('top-speed', year, gp, session)
        
        # Check if result is cached data (dict/list) or file path (str)
        if isinstance(result, (dict, list)):
            return result
        else:
            return FileResponse(result, media_type='application/json')
    except FileNotFoundError:
        logger.error(f"Data file not found: Y{year} GP{gp} {session}")
        raise HTTPException(status_code=404, detail="Data not found")
    except Exception as e:
        logger.error(f"Error fetching top speed data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")

@app.get('/api/throttle-comparison-plot', tags=["Simple Analysis"])
@apply_tiered_limit("standard")
async def throttle_comparison_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Generate PNG plot comparing throttle application"""
    try:
        logger.info(f"Generating throttle comparison plot: Y{year} GP{gp} {session}")
        output_path = await run_in_threadpool(ThrottleComp, year, gp, session)
        
        if session_tracker:
            session_tracker.track_session('throttle-comparison', year, gp, session)
        
        return FileResponse(output_path, media_type='image/png')
    except Exception as e:
        logger.error(f"Error generating throttle plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")

@app.get('/api/throttle-comparison-data', tags=["Simple Analysis"])
@apply_tiered_limit("data")
async def throttle_comparison_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Get raw JSON data for throttle comparison"""
    try:
        logger.info(f"Fetching throttle comparison data: Y{year} GP{gp} {session}")
        result = await run_in_threadpool(ThrottleCompData, year, gp, session)
        
        if session_tracker:
            session_tracker.track_session('throttle-comparison', year, gp, session)
        
        # Check if result is cached data (dict/list) or file path (str)
        if isinstance(result, (dict, list)):
            return result
        else:
            return FileResponse(result, media_type='application/json')
    except Exception as e:
        logger.error(f"Error fetching throttle data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")

@app.get('/api/qualifying-results-plot', tags=["Simple Analysis"])
@apply_tiered_limit("standard")
async def qualifying_results_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Generate PNG plot of qualifying results"""
    try:
        logger.info(f"Generating qualifying results plot: Y{year} GP{gp} {session}")
        output_path = await run_in_threadpool(QualiResults, year, gp, session)
        
        if session_tracker:
            session_tracker.track_session('qualifying-results', year, gp, session)
        
        return FileResponse(output_path, media_type='image/png')
    except Exception as e:
        logger.error(f"Error generating qualifying plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")

@app.get('/api/qualifying-results-data', tags=["Simple Analysis"])
@apply_tiered_limit("data")
async def qualifying_results_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Get raw JSON data for qualifying results"""
    try:
        logger.info(f"Fetching qualifying results data: Y{year} GP{gp} {session}")
        result = await run_in_threadpool(QualiResultsData, year, gp, session)
        
        if session_tracker:
            session_tracker.track_session('qualifying-results', year, gp, session)
        
        # Check if result is cached data (dict/list) or file path (str)
        if isinstance(result, (dict, list)):
            return result
        else:
            return FileResponse(result, media_type='application/json')
    except Exception as e:
        logger.error(f"Error fetching qualifying data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")

@app.get('/api/laptimes', tags=["Simple Analysis"])
@apply_tiered_limit("standard")
async def get_laptimes(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    driver: str = Query('VER', min_length=3, max_length=3),
    api_key: str = Depends(verify_api_key)
):
    """Get laptime distribution data for a specific driver"""
    try:
        logger.info(f"Fetching lap times: Y{year} GP{gp} {session} Driver:{driver}")
        result = await run_in_threadpool(LatimesDistribution, year, gp, session, driver)
        
        if session_tracker:
            session_tracker.track_session('laptimes', year, gp, session, driver)
        
        # Handle both cached data (list) and file path (string)
        if isinstance(result, list):
            return JSONResponse(content=result)
        else:
            return FileResponse(result, media_type='application/json')
    except Exception as e:
        logger.error(f"Error fetching lap times: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch lap times")

# --- Driver Comparison ---

@app.get('/api/track-comparison-2drivers-plot', tags=["Driver Comparison"])
@apply_tiered_limit("standard")
async def track_comparison_2drivers_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    driver1: str = Query('VER', min_length=3, max_length=3),
    driver2: str = Query('HAM', min_length=3, max_length=3),
    api_key: str = Depends(verify_api_key)
):
    """Generate track map comparing two drivers"""
    try:
        logger.info(f"Generating track comparison: Y{year} GP{gp} {session} {driver1} vs {driver2}")
        output_path = await run_in_threadpool(TrackComparisonPlot, year, gp, session, driver1, driver2)
        
        if session_tracker:
            session_tracker.track_session('track-comparison-2drivers', year, gp, session, driver1, driver2)
        
        return FileResponse(output_path, media_type='image/png')
    except Exception as e:
        logger.error(f"Error generating track comparison: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")

@app.get('/api/track-comparison-2drivers-data', tags=["Driver Comparison"])
@apply_tiered_limit("data")
async def track_comparison_2drivers_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    driver1: str = Query('VER', min_length=3, max_length=3),
    driver2: str = Query('HAM', min_length=3, max_length=3),
    api_key: str = Depends(verify_api_key)
):
    """Get raw data for 2-driver track comparison"""
    try:
        logger.info(f"Fetching track comparison data: Y{year} GP{gp} {session} {driver1} vs {driver2}")
        result = await run_in_threadpool(TrackComparisonData, year, gp, session, driver1, driver2)
        
        if session_tracker:
            session_tracker.track_session('track-comparison-2drivers', year, gp, session, driver1, driver2)
        
        # Check if result is cached data (dict/list) or file path (str)
        if isinstance(result, (dict, list)):
            return result
        else:
            return FileResponse(result, media_type='application/json')
    except Exception as e:
        logger.error(f"Error fetching track comparison data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")

@app.get('/api/throttleBrake-comparison-2drivers-plot', tags=["Driver Comparison"])
@apply_tiered_limit("standard")
async def throttle_brake_comparison_2drivers_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    driver1: str = Query('VER', min_length=3, max_length=3),
    driver2: str = Query('HAM', min_length=3, max_length=3),
    api_key: str = Depends(verify_api_key)
):
    """Generate throttle/brake telemetry graph for 2 drivers"""
    try:
        logger.info(f"Generating throttle/brake comparison: Y{year} GP{gp} {session} {driver1} vs {driver2}")
        output_path = await run_in_threadpool(throttle_graph, year, gp, session, driver1, driver2)
        return FileResponse(output_path, media_type='image/png')
    except Exception as e:
        logger.error(f"Error generating throttle/brake plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")

@app.get('/api/throttleBrake-comparison-2drivers-data', tags=["Driver Comparison"])
@apply_tiered_limit("data")
async def throttle_brake_comparison_2drivers_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    driver1: str = Query('VER', min_length=3, max_length=3),
    driver2: str = Query('HAM', min_length=3, max_length=3),
    api_key: str = Depends(verify_api_key)
):
    """Get raw data for 2-driver throttle/brake comparison"""
    try:
        logger.info(f"Fetching throttle/brake data: Y{year} GP{gp} {session} {driver1} vs {driver2}")
        result = await run_in_threadpool(throttle_graph_data, year, gp, session, driver1, driver2)
        
        if session_tracker:
            session_tracker.track_session('track-comparison-2drivers', year, gp, session, driver1, driver2)
        
        # Check if result is cached data (dict/list) or file path (str)
        if isinstance(result, (dict, list)):
            return result
        else:
            return FileResponse(result, media_type='application/json')
    except Exception as e:
        logger.error(f"Error fetching throttle/brake data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")

@app.get('/api/analytics/daily', tags=["General"])
@apply_tiered_limit("standard")
async def get_daily_analytics(
    request: Request,
    date_str: str = Query(None, description='Date in YYYY-MM-DD format'),
    api_key: str = Depends(verify_api_key)
):
    """Get daily session analytics"""
    try:
        if date_str:
            from datetime import datetime
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            target_date = None
        
        if not session_tracker:
            raise HTTPException(status_code=503, detail="Session tracker unavailable")
        
        stats = session_tracker.get_daily_stats(target_date)
        return stats
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    except Exception as e:
        logger.error(f"Error fetching daily analytics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch analytics")

@app.get('/api/analytics/total', tags=["General"])
@apply_tiered_limit("standard")
async def get_total_analytics(
    request: Request,
    api_key: str = Depends(verify_api_key)
):
    """Get total session analytics"""
    try:
        if not session_tracker:
            raise HTTPException(status_code=503, detail="Session tracker unavailable")
        
        stats = session_tracker.get_total_stats()
        return stats
    except Exception as e:
        logger.error(f"Error fetching total analytics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch analytics")

if __name__ == '__main__':
    logger.info(f"Starting server on {settings.host}:{settings.docker_exposed_port}")
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.docker_exposed_port,
        log_level=settings.log_level.lower()
    )
