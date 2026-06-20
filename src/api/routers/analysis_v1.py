from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool

from src.core.logging import get_logger
from src.core.security.api_keys import verify_api_key
from src.core.security.rate_limiting import apply_tiered_limit
from src.services.orchestrator_helpers import get_latest_finished_session
from src.services.orchestrator import latest_session_analised
from src.services.analysis.v1.top_speed import TopSpeedPlot, TopSpeedData
from src.services.analysis.v1.throttle_comparison import ThrottleComp, ThrottleCompData
from src.services.analysis.v1.speed_distribution import SpeedDistributionPlot, SpeedDistributionData
from src.services.analysis.v1.qualifying_results import QualiResults, QualiResultsData
from src.services.analysis.v1.track_comparison import TrackComparisonPlot, TrackComparisonData
from src.services.analysis.v1.throttle_brake_comparison import throttle_graph, throttle_graph_data
from src.services.analysis.v1.lap_time_analysis import LapTimeAnalysisPlot, LapTimeAnalysisData
from src.services.analysis.v1.laptimes_distribution import LatimesDistribution
from src.services.analysis.v1.driver_pace import DriverPacePlot, DriverPaceData
from src.services.analysis.v1.teams_pace import TeamsPacePlot, TeamsPaceData

# V2 siblings for transparent fallback when FastF1 lacks data.
from src.services.analysis.v2.top_speed import (
    TopSpeedPlot_Telemetry as V2_TopSpeedPlot,
    TopSpeedData_Telemetry as V2_TopSpeedData,
)
from src.services.analysis.v2.throttle_comparison import (
    ThrottleComp as V2_ThrottleComp,
    ThrottleCompData as V2_ThrottleCompData,
)
from src.services.analysis.v2.qualifying_results import (
    QualiResultsPlot as V2_QualiResultsPlot,
)
from src.services.analysis.v2.speed_distribution import (
    SpeedDistributionPlot as V2_SpeedDistributionPlot,
    SpeedDistributionData as V2_SpeedDistributionData,
)
from src.services.analysis.v2.driver_pace import (
    DriverPacePlot as V2_DriverPacePlot,
    DriverPaceData as V2_DriverPaceData,
)
from src.services.analysis.v2.teams_pace import (
    TeamsPacePlot as V2_TeamsPacePlot,
    TeamsPaceData as V2_TeamsPaceData,
)
from src.services.analysis.v2.lap_time_analysis import (
    LapTimeAnalysisPlot as V2_LapTimeAnalysisPlot,
    LapTimeAnalysisData as V2_LapTimeAnalysisData,
)
from src.services.analysis.base import with_fallback
from src.core.exceptions import T1APIError

logger = get_logger(__name__)


def _track(event_name, *args):
    """Best-effort session tracking; never blocks the response."""
    try:
        from src.core.observability.analytics import SessionTracker
        SessionTracker().track_session(event_name, *args)
    except Exception:
        pass

router = APIRouter(prefix="/api/v1")

# ============================================================================
# ANALYSIS ENDPOINTS
# ============================================================================

@router.get('/daily-data', tags=["API v1", "General"])
@apply_tiered_limit("standard")
async def daily_data(request: Request, api_key: str = Depends(verify_api_key)):
    """Generate daily data summary plot (Standard rate limit)"""
    try:
        logger.info("Generating daily data plot")
        from src.workers.daily import DailyPlotData
        output = await run_in_threadpool(lambda: DailyPlotData().generate_daily_plot())
        return output
    except Exception as e:
        logger.error(f"Error generating daily data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate daily data")

@router.get('/dashboard', tags=["API v1", "Latest Session"])
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

# --- Simple Analysis ---

@router.get('/top-speed-plot', tags=["API v1", "Simple Analysis"])
@apply_tiered_limit("standard")
async def quali_top_speed_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Generate PNG plot of top speeds. Falls back to V2 if FastF1 lacks data."""
    logger.info(f"Generating top speed plot: Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: TopSpeedPlot(year, gp, session),
            lambda: V2_TopSpeedPlot(year, gp, session),
            primary_source="fastf1", secondary_source="livetiming",
            year=year, gp=gp, session=session, data_type="top_speed",
        )
        _track('top-speed', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise  # Handled by app-level exception handlers (503/404).
    except FileNotFoundError:
        logger.error(f"Plot file not found: Y{year} GP{gp} {session}")
        raise HTTPException(status_code=404, detail="Plot not found")

@router.get('/top-speed-data', tags=["API v1", "Simple Analysis"])
@apply_tiered_limit("data")
async def quali_top_speed_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Get raw JSON data for top speed analysis. Falls back to V2 if FastF1 lacks data."""
    logger.info(f"Fetching top speed data: Y{year} GP{gp} {session}")
    try:
        result = await run_in_threadpool(
            with_fallback,
            lambda: TopSpeedData(year, gp, session),
            lambda: V2_TopSpeedData(year, gp, session),
            primary_source="fastf1", secondary_source="livetiming",
            year=year, gp=gp, session=session, data_type="top_speed",
        )
        _track('top-speed', year, gp, session)
        if isinstance(result, (dict, list)):
            return result
        return FileResponse(result, media_type='application/json')
    except T1APIError:
        raise
    except FileNotFoundError:
        logger.error(f"Data file not found: Y{year} GP{gp} {session}")
        raise HTTPException(status_code=404, detail="Data not found")

@router.get('/throttle-comparison-plot', tags=["API v1", "Simple Analysis"])
@apply_tiered_limit("standard")
async def throttle_comparison_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Generate PNG plot comparing throttle application. Falls back to V2 if FastF1 lacks data."""
    logger.info(f"Generating throttle comparison plot: Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: ThrottleComp(year, gp, session),
            lambda: V2_ThrottleComp(year, gp, session),
            primary_source="fastf1", secondary_source="livetiming",
            year=year, gp=gp, session=session, data_type="throttle_comparison",
        )
        _track('throttle-comparison', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise

@router.get('/throttle-comparison-data', tags=["API v1", "Simple Analysis"])
@apply_tiered_limit("data")
async def throttle_comparison_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Get raw JSON data for throttle comparison. Falls back to V2 if FastF1 lacks data."""
    logger.info(f"Fetching throttle comparison data: Y{year} GP{gp} {session}")
    try:
        result = await run_in_threadpool(
            with_fallback,
            lambda: ThrottleCompData(year, gp, session),
            lambda: V2_ThrottleCompData(year, gp, session),
            primary_source="fastf1", secondary_source="livetiming",
            year=year, gp=gp, session=session, data_type="throttle_comparison",
        )
        _track('throttle-comparison', year, gp, session)
        if isinstance(result, (dict, list)):
            return result
        return FileResponse(result, media_type='application/json')
    except T1APIError:
        raise

@router.get('/qualifying-results-plot', tags=["API v1", "Simple Analysis"])
@apply_tiered_limit("standard")
async def qualifying_results_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Generate PNG plot of qualifying results. Falls back to V2 if FastF1 lacks data."""
    logger.info(f"Generating qualifying results plot: Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: QualiResults(year, gp, session),
            lambda: V2_QualiResultsPlot(year, gp, session),
            primary_source="fastf1", secondary_source="livetiming",
            year=year, gp=gp, session=session, data_type="qualifying_results",
        )
        _track('qualifying-results', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise

@router.get('/qualifying-results-data', tags=["API v1", "Simple Analysis"])
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
        
        # Track session if tracker is available
        try:
            from src.core.observability.analytics import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('qualifying-results', year, gp, session)
        except:
            pass
        
        # Check if result is cached data (dict/list) or file path (str)
        if isinstance(result, (dict, list)):
            return result
        else:
            return FileResponse(result, media_type='application/json')
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching qualifying data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")

@router.get('/laptimes', tags=["API v1", "Simple Analysis"])
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
        
        # Track session if tracker is available
        try:
            from src.core.observability.analytics import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('laptimes', year, gp, session, driver)
        except:
            pass
        
        # Handle both cached data (list) and file path (string)
        if isinstance(result, list):
            return JSONResponse(content=result)
        else:
            return FileResponse(result, media_type='application/json')
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching lap times: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch lap times")

@router.get('/speed-distribution-plot', tags=["API v1", "Simple Analysis"])
@apply_tiered_limit("standard")
async def speed_distribution_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
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
    except ValueError as ve:
        logger.warning(f"Data error in speed distribution plot: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error generating speed distribution plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")

@router.get('/speed-distribution-data', tags=["API v1", "Simple Analysis"])
@apply_tiered_limit("data")
async def speed_distribution_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
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
    except ValueError as ve:
        logger.warning(f"Data error in speed distribution data: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching speed distribution data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")

# --- Driver Comparison ---

@router.get('/track-comparison-2drivers-plot', tags=["API v1", "Driver Comparison"])
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
        
        # Track session if tracker is available
        try:
            from src.core.observability.analytics import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('track-comparison-2drivers', year, gp, session, driver1, driver2)
        except:
            pass
        
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error generating track comparison: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")

@router.get('/track-comparison-2drivers-data', tags=["API v1", "Driver Comparison"])
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
        
        # Track session if tracker is available
        try:
            from src.core.observability.analytics import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('track-comparison-2drivers', year, gp, session, driver1, driver2)
        except:
            pass
        
        # Check if result is cached data (dict/list) or file path (str)
        if isinstance(result, (dict, list)):
            return result
        else:
            return FileResponse(result, media_type='application/json')
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching track comparison data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")

@router.get('/throttleBrake-comparison-2drivers-plot', tags=["API v1", "Driver Comparison"])
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
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error generating throttle/brake plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")

@router.get('/throttleBrake-comparison-2drivers-data', tags=["API v1", "Driver Comparison"])
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
        
        # Track session if tracker is available
        try:
            from src.core.observability.analytics import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('track-comparison-2drivers', year, gp, session, driver1, driver2)
        except:
            pass
        
        # Check if result is cached data (dict/list) or file path (str)
        if isinstance(result, (dict, list)):
            return result
        else:
            return FileResponse(result, media_type='application/json')
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching throttle/brake data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")

@router.get('/lap-time-analysis-plot', tags=["API v1", "Driver Comparison"])
@apply_tiered_limit("standard")
async def lap_time_analysis_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    driver1: str = Query('VER', min_length=3, max_length=3),
    driver2: str = Query('HAM', min_length=3, max_length=3),
    api_key: str = Depends(verify_api_key)
):
    """Speed / Delta time / Throttle comparison for two drivers' fastest laps. Falls back to V2."""
    logger.info(f"Generating lap time analysis plot: Y{year} GP{gp} {session} {driver1} vs {driver2}")
    try:
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: LapTimeAnalysisPlot(year, gp, session, driver1, driver2),
            lambda: V2_LapTimeAnalysisPlot(year, gp, session, driver1, driver2),
            primary_source="fastf1", secondary_source="livetiming",
            year=year, gp=gp, session=session, data_type="lap_time_analysis",
        )
        _track('lap-time-analysis', year, gp, session, driver1, driver2)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/lap-time-analysis-data', tags=["API v1", "Driver Comparison"])
@apply_tiered_limit("data")
async def lap_time_analysis_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('Q'),
    driver1: str = Query('VER', min_length=3, max_length=3),
    driver2: str = Query('HAM', min_length=3, max_length=3),
    api_key: str = Depends(verify_api_key)
):
    """Speed/Throttle telemetry + delta-time series for two drivers' fastest laps. Falls back to V2."""
    logger.info(f"Fetching lap time analysis data: Y{year} GP{gp} {session} {driver1} vs {driver2}")
    try:
        result = await run_in_threadpool(
            with_fallback,
            lambda: LapTimeAnalysisData(year, gp, session, driver1, driver2),
            lambda: V2_LapTimeAnalysisData(year, gp, session, driver1, driver2),
            primary_source="fastf1", secondary_source="livetiming",
            year=year, gp=gp, session=session, data_type="lap_time_analysis",
        )
        _track('lap-time-analysis', year, gp, session, driver1, driver2)
        if isinstance(result, (dict, list)):
            return result
        return FileResponse(result, media_type='application/json')
    except T1APIError:
        raise


@router.get('/analytics/daily', tags=["API v1", "General"])
@apply_tiered_limit("standard")
async def get_daily_analytics(
    request: Request,
    date_str: str = Query(None, description='Date in YYYY-MM-DD format'),
    api_key: str = Depends(verify_api_key)
):
    """Get daily session analytics"""
    try:
        from src.core.observability.analytics import SessionTracker
        from datetime import datetime
        
        if date_str:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            target_date = None
        
        try:
            session_tracker = SessionTracker()
        except:
            raise HTTPException(status_code=503, detail="Session tracker unavailable")
        
        stats = session_tracker.get_daily_stats(target_date)
        return stats
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching daily analytics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch analytics")

@router.get('/analytics/total', tags=["API v1", "General"])
@apply_tiered_limit("standard")
async def get_total_analytics(
    request: Request,
    api_key: str = Depends(verify_api_key)
):
    """Get total session analytics"""
    try:
        from src.core.observability.analytics import SessionTracker
        
        try:
            session_tracker = SessionTracker()
        except:
            raise HTTPException(status_code=503, detail="Session tracker unavailable")
        
        stats = session_tracker.get_total_stats()
        return stats
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching total analytics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch analytics")


# --- Pace Analysis ---

@router.get('/driver-pace-plot', tags=["API v1", "Pace Analysis"])
@apply_tiered_limit("standard")
async def driver_pace_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """Box-and-whisker pace plot per driver (107% quicklap filter). Falls back to V2."""
    logger.info(f"Generating driver pace plot: Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: DriverPacePlot(year, gp, session),
            lambda: V2_DriverPacePlot(year, gp, session),
            primary_source="fastf1", secondary_source="livetiming",
            year=year, gp=gp, session=session, data_type="driver_pace",
        )
        _track('driver-pace', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/driver-pace-data', tags=["API v1", "Pace Analysis"])
@apply_tiered_limit("data")
async def driver_pace_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """JSON pace distribution per driver: lap times + min/q1/median/q3/max. Falls back to V2."""
    logger.info(f"Fetching driver pace data: Y{year} GP{gp} {session}")
    try:
        result = await run_in_threadpool(
            with_fallback,
            lambda: DriverPaceData(year, gp, session),
            lambda: V2_DriverPaceData(year, gp, session),
            primary_source="fastf1", secondary_source="livetiming",
            year=year, gp=gp, session=session, data_type="driver_pace",
        )
        _track('driver-pace', year, gp, session)
        if isinstance(result, (dict, list)):
            return result
        return FileResponse(result, media_type='application/json')
    except T1APIError:
        raise


@router.get('/teams-pace-plot', tags=["API v1", "Pace Analysis"])
@apply_tiered_limit("standard")
async def teams_pace_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """Box-and-whisker pace plot per team (laps from both drivers aggregated). Falls back to V2."""
    logger.info(f"Generating teams pace plot: Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: TeamsPacePlot(year, gp, session),
            lambda: V2_TeamsPacePlot(year, gp, session),
            primary_source="fastf1", secondary_source="livetiming",
            year=year, gp=gp, session=session, data_type="teams_pace",
        )
        _track('teams-pace', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/teams-pace-data', tags=["API v1", "Pace Analysis"])
@apply_tiered_limit("data")
async def teams_pace_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """JSON pace distribution per team. Falls back to V2."""
    logger.info(f"Fetching teams pace data: Y{year} GP{gp} {session}")
    try:
        result = await run_in_threadpool(
            with_fallback,
            lambda: TeamsPaceData(year, gp, session),
            lambda: V2_TeamsPaceData(year, gp, session),
            primary_source="fastf1", secondary_source="livetiming",
            year=year, gp=gp, session=session, data_type="teams_pace",
        )
        _track('teams-pace', year, gp, session)
        if isinstance(result, (dict, list)):
            return result
        return FileResponse(result, media_type='application/json')
    except T1APIError:
        raise
