from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool
from typing import Union
from src.core.logging import get_logger
from src.core.security.api_keys import verify_api_key
from src.core.security.rate_limiting import apply_tiered_limit
from src.services.orchestrator_helpers import get_latest_finished_session_v2
from src.services.orchestrator import latest_session_analised_v2

# Importing Turn One Core files
from src.services.analysis.v2.top_speed import TopSpeedPlot_Telemetry, TopSpeedData_Telemetry, TopSpeedPlot_SpeedTrap, TopSpeedData_SpeedTrap
from src.services.analysis.v2.throttle_comparison import ThrottleComp, ThrottleCompData
from src.services.analysis.v2.speed_distribution import SpeedDistributionPlot, SpeedDistributionData
from src.services.analysis.v2.laptimes_distribution import LaptimesDistribution
from src.services.analysis.v2.qualifying_results import QualiResultsPlot, QualiResultsData
from src.services.analysis.v2.throttle_brake_comparison import ThrottleBrakeComp, ThrottleBrakeCompData
from src.services.analysis.v2.track_comparison import TrackComparisonPlot, TrackComparisonData

# V1 siblings for transparent fallback when livetiming lacks data.
from src.services.analysis.v1.top_speed import TopSpeedPlot as V1_TopSpeedPlot, TopSpeedData as V1_TopSpeedData
from src.services.analysis.v1.throttle_comparison import ThrottleComp as V1_ThrottleComp, ThrottleCompData as V1_ThrottleCompData
from src.services.analysis.v1.qualifying_results import QualiResults as V1_QualiResults
from src.services.analysis.v1.speed_distribution import SpeedDistributionPlot as V1_SpeedDistributionPlot, SpeedDistributionData as V1_SpeedDistributionData
from src.services.analysis.base import with_fallback
from src.core.exceptions import T1APIError

logger = get_logger(__name__)


def _track(event_name, *args):
    try:
        from src.core.observability.analytics import SessionTracker
        SessionTracker().track_session(event_name, *args)
    except Exception:
        pass

router = APIRouter(prefix="/api/v2")


@router.get('/dashboard', tags=["API v2", "Latest Session"])
@apply_tiered_limit("data")
async def get_dashboard_data_v2(request: Request, api_key: str = Depends(verify_api_key)):
    """
    Get main latest session data via the livetiming-only V2 path.
    Automatically detects the most recent completed session from the F1
    static index (no FastF1).
    """
    try:
        logger.info("Fetching V2 dashboard data for latest session")
        latest_session = await run_in_threadpool(get_latest_finished_session_v2)

        if not latest_session:
            logger.warning("V2: no finished sessions found")
            raise HTTPException(status_code=404, detail="No finished sessions found")

        result = await run_in_threadpool(latest_session_analised_v2, latest_session)
        return result

    except HTTPException:
        raise
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching V2 dashboard data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch dashboard data")


# --- Simple Analysis Endpoints ---

@router.get('/top-speed-telemetry-plot', tags=["API v2", "Simple Analysis"])
@apply_tiered_limit("standard")
async def top_speed_telemetry_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    logger.info(f"Generating top speed plot: Y{year} GP{gp} {session}")
    try:
        # V2 only supports integer round numbers for V1 fallback; skip if string.
        v1_secondary = (lambda: V1_TopSpeedPlot(year, gp, session)) if isinstance(gp, int) else None
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: TopSpeedPlot_Telemetry(year, gp, session),
            v1_secondary,
            primary_source="livetiming", secondary_source="fastf1",
            year=year, gp=gp, session=session, data_type="top_speed",
        )
        _track('top-speed', year, gp, session)
        return FileResponse(output_path, media_type="image/png")
    except T1APIError:
        raise
    except FileNotFoundError:
        logger.error(f"Plot file not found: Y{year} GP{gp} {session}")
        raise HTTPException(status_code=404, detail="Plot not found")

@router.get('/top-speed-telemetry-data', tags=["API v2", "Simple Analysis"])
@apply_tiered_limit("data")
async def top_speed_telemetry_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    logger.info(f"Fetching top speed data: Y{year} GP{gp} {session}")
    try:
        v1_secondary = (lambda: V1_TopSpeedData(year, gp, session)) if isinstance(gp, int) else None
        result = await run_in_threadpool(
            with_fallback,
            lambda: TopSpeedData_Telemetry(year, gp, session, True),
            v1_secondary,
            primary_source="livetiming", secondary_source="fastf1",
            year=year, gp=gp, session=session, data_type="top_speed",
        )
        _track('top-speed', year, gp, session)
        return result
    except T1APIError:
        raise

@router.get('/top-speed-st-plot', tags=["API v2", "Simple Analysis"])
@apply_tiered_limit("standard")
async def top_speed_st_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    try:
        logger.info(f"Generating top speed plot: Y{year} GP{gp} {session}")
        output_path = await run_in_threadpool(TopSpeedPlot_SpeedTrap, year, gp, session)

        # Track session if tracker is available
        try:
            from src.core.observability.analytics import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('top-speed', year, gp, session)
        except:
            pass

        return FileResponse(output_path, media_type="image/png")
    except FileNotFoundError:
        logger.error(f"Plot file not found: Y{year} GP{gp} {session}")
        raise HTTPException(status_code=404, detail="Plot not found")
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error generating top speed plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")
    
@router.get('/top-speed-st-data', tags=["API v2", "Simple Analysis"])
@apply_tiered_limit("data")
async def top_speed_st_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    try:
        logger.info(f"Fetching top speed data: Y{year} GP{gp} {session}")
        result = await run_in_threadpool(TopSpeedData_SpeedTrap, year, gp, session)
        
        # Track session if tracker is available
        try:
            from src.core.observability.analytics import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('top-speed', year, gp, session)
        except:
            pass
        
        # Data functions now always return list directly (from MongoDB or processed)
        return result
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching top speed data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")
    

# Throttle comparison endpoints
@router.get('/throttle-comparison-plot', tags=["API v2", "Simple Analysis"])
@apply_tiered_limit("standard")
async def throttle_comparison_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Generate PNG plot comparing throttle application. Falls back to V1 if livetiming lacks data."""
    logger.info(f"Generating throttle comparison plot: Y{year} GP{gp} {session}")
    try:
        v1_secondary = (lambda: V1_ThrottleComp(year, gp, session)) if isinstance(gp, int) else None
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: ThrottleComp(year, gp, session),
            v1_secondary,
            primary_source="livetiming", secondary_source="fastf1",
            year=year, gp=gp, session=session, data_type="throttle_comparison",
        )
        _track('throttle-comparison', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise

@router.get('/throttle-comparison-data', tags=["API v2", "Simple Analysis"])
@apply_tiered_limit("data")
async def throttle_comparison_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Get raw JSON data for throttle comparison. Falls back to V1 if livetiming lacks data."""
    logger.info(f"Fetching throttle comparison data: Y{year} GP{gp} {session}")
    try:
        v1_secondary = (lambda: V1_ThrottleCompData(year, gp, session)) if isinstance(gp, int) else None
        result = await run_in_threadpool(
            with_fallback,
            lambda: ThrottleCompData(year, gp, session),
            v1_secondary,
            primary_source="livetiming", secondary_source="fastf1",
            year=year, gp=gp, session=session, data_type="throttle_comparison",
        )
        _track('throttle-comparison', year, gp, session)
        if isinstance(result, (dict, list)):
            return result
        return FileResponse(result, media_type='application/json')
    except T1APIError:
        raise

@router.get('/speed-distribution-plot', tags=["API v2", "Simple Analysis"])
@apply_tiered_limit("standard")
async def speed_distribution_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    driver: str = Query(None, description="Optional driver TLA (e.g., VER)"),
    api_key: str = Depends(verify_api_key)
):
    """Generate PNG plot of speed distribution"""
    try:
        logger.info(f"Generating speed distribution plot: Y{year} GP{gp} {session} Driver={driver}")
        output_path = await run_in_threadpool(SpeedDistributionPlot, year, gp, session, driver)
        
        # Track session if tracker is available
        try:
            from src.core.observability.analytics import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('speed-distribution', year, gp, session)
        except:
            pass
        
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error generating speed distribution plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")

@router.get('/speed-distribution-data', tags=["API v2", "Simple Analysis"])
@apply_tiered_limit("data")
async def speed_distribution_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    driver: str = Query(None, description="Optional driver TLA (e.g., VER)"),
    api_key: str = Depends(verify_api_key)
):
    """Get raw JSON data for speed distribution"""
    try:
        logger.info(f"Fetching speed distribution data: Y{year} GP{gp} {session} Driver={driver}")
        result = await run_in_threadpool(SpeedDistributionData, year, gp, session, driver)
        
        # Track session if tracker is available
        try:
            from src.core.observability.analytics import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('speed-distribution', year, gp, session)
        except:
            pass
        
        if isinstance(result, (dict, list)):
            return result
        else:
            return FileResponse(result, media_type='application/json')
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching speed distribution data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")


# --- Lap Times Distribution ---

@router.get('/laptimes-distribution-data', tags=["API v2", "Simple Analysis"])
@apply_tiered_limit("data")
async def laptimes_distribution_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    driver: str = Query(..., description="Driver TLA (e.g., VER)"),
    api_key: str = Depends(verify_api_key)
):
    """Get per-lap lap times and tire compound for a driver"""
    try:
        logger.info(f"Fetching lap times distribution: Y{year} GP{gp} {session} Driver={driver}")
        result = await run_in_threadpool(LaptimesDistribution, year, gp, session, driver)
        return result
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching lap times distribution: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")


# --- Qualifying Results ---

@router.get('/qualifying-results-plot', tags=["API v2", "Qualifying"])
@apply_tiered_limit("standard")
async def qualifying_results_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Generate PNG plot of qualifying results sorted by lap time delta. Falls back to V1 if livetiming lacks data."""
    logger.info(f"Generating qualifying results plot: Y{year} GP{gp} {session}")
    try:
        v1_secondary = (lambda: V1_QualiResults(year, gp, session)) if isinstance(gp, int) else None
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: QualiResultsPlot(year, gp, session),
            v1_secondary,
            primary_source="livetiming", secondary_source="fastf1",
            year=year, gp=gp, session=session, data_type="qualifying_results",
        )
        if not output_path:
            raise HTTPException(status_code=404, detail="No data available for this session")
        return FileResponse(output_path, media_type="image/png")
    except T1APIError:
        raise
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/qualifying-results-data', tags=["API v2", "Qualifying"])
@apply_tiered_limit("data")
async def qualifying_results_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Get qualifying results data with lap times and deltas"""
    try:
        logger.info(f"Fetching qualifying results data: Y{year} GP{gp} {session}")
        result = await run_in_threadpool(QualiResultsData, year, gp, session)
        return result
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching qualifying results data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")


# --- Throttle/Brake Comparison (2 drivers) ---

@router.get('/throttle-brake-comparison-plot', tags=["API v2", "Qualifying"])
@apply_tiered_limit("standard")
async def throttle_brake_comparison_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    d1: str = Query(..., description="First driver TLA (e.g., VER)"),
    d2: str = Query(..., description="Second driver TLA (e.g., NOR)"),
    api_key: str = Depends(verify_api_key)
):
    """Generate Speed/Throttle/Brake vs Distance comparison for two drivers' fastest laps"""
    try:
        logger.info(f"Generating throttle/brake comparison plot: Y{year} GP{gp} {session} {d1} vs {d2}")
        output_path = await run_in_threadpool(ThrottleBrakeComp, year, gp, session, d1, d2)
        if not output_path:
            raise HTTPException(status_code=404, detail="No data available for this session/drivers")
        return FileResponse(output_path, media_type="image/png")
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error generating throttle/brake comparison plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")


@router.get('/throttle-brake-comparison-data', tags=["API v2", "Qualifying"])
@apply_tiered_limit("data")
async def throttle_brake_comparison_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    d1: str = Query(..., description="First driver TLA (e.g., VER)"),
    d2: str = Query(..., description="Second driver TLA (e.g., NOR)"),
    api_key: str = Depends(verify_api_key)
):
    """Get telemetry data (Speed/Throttle/Brake/Distance) for two drivers' fastest laps"""
    try:
        logger.info(f"Fetching throttle/brake comparison data: Y{year} GP{gp} {session} {d1} vs {d2}")
        result = await run_in_threadpool(ThrottleBrakeCompData, year, gp, session, d1, d2)
        return result
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching throttle/brake comparison data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")


# --- Track Comparison (2 drivers) ---

@router.get('/track-comparison-plot', tags=["API v2", "Qualifying"])
@apply_tiered_limit("standard")
async def track_comparison_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    d1: str = Query(..., description="First driver TLA (e.g., VER)"),
    d2: str = Query(..., description="Second driver TLA (e.g., NOR)"),
    api_key: str = Depends(verify_api_key)
):
    """Generate color-coded track map showing which driver is faster in each minisector"""
    try:
        logger.info(f"Generating track comparison plot: Y{year} GP{gp} {session} {d1} vs {d2}")
        output_path = await run_in_threadpool(TrackComparisonPlot, year, gp, session, d1, d2)
        if not output_path:
            raise HTTPException(status_code=404, detail="No position data available for this session/drivers")
        return FileResponse(output_path, media_type="image/png")
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error generating track comparison plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")


@router.get('/track-comparison-data', tags=["API v2", "Qualifying"])
@apply_tiered_limit("data")
async def track_comparison_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    d1: str = Query(..., description="First driver TLA (e.g., VER)"),
    d2: str = Query(..., description="Second driver TLA (e.g., NOR)"),
    api_key: str = Depends(verify_api_key)
):
    """Get track map data with minisector fastest driver assignments for two drivers"""
    try:
        logger.info(f"Fetching track comparison data: Y{year} GP{gp} {session} {d1} vs {d2}")
        result = await run_in_threadpool(TrackComparisonData, year, gp, session, d1, d2)
        return result
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching track comparison data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")