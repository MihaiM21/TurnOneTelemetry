from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool
from typing import Optional, Union
from src.core.logging import get_logger
from src.core.security.api_keys import verify_api_key
from src.core.security.rate_limiting import apply_tiered_limit
from src.services.orchestrator_helpers import get_latest_finished_session
from src.services.orchestrator import latest_session_analised_v2

# Importing Turn One Core files
from src.services.analysis.v2.top_speed import TopSpeedPlot_Telemetry, TopSpeedData_Telemetry, TopSpeedPlot_SpeedTrap, TopSpeedData_SpeedTrap
from src.services.analysis.v2.throttle_comparison import ThrottleComp, ThrottleCompData
from src.services.analysis.v2.speed_distribution import SpeedDistributionPlot, SpeedDistributionData
from src.services.analysis.v2.laptimes_distribution import LaptimesDistribution
from src.services.analysis.v2.qualifying_results import QualiResultsPlot, QualiResultsData
from src.services.analysis.v2.throttle_brake_comparison import ThrottleBrakeComp, ThrottleBrakeCompData
from src.services.analysis.v2.lap_time_analysis import LapTimeAnalysisPlot, LapTimeAnalysisData
from src.services.analysis.v2.track_comparison import TrackComparisonPlot, TrackComparisonData
from src.services.analysis.v2.driver_pace import DriverPacePlot, DriverPaceData
from src.services.analysis.v2.teams_pace import TeamsPacePlot, TeamsPaceData
from src.services.analysis.v2.tyre_stint_usage import TyreStintUsagePlot, TyreStintUsageData
from src.services.analysis.v2.position_changes import PositionChangesPlot, PositionChangesData
from src.services.analysis.v2.race_gaps import RaceGapsPlot, RaceGapsData
from src.services.analysis.v2.tyre_degradation import TyreDegradationPlot, TyreDegradationData
from src.services.analysis.v2.pit_strategy import PitStrategyPlot, PitStrategyData
from src.services.analysis.v2.session_weather import SessionWeatherPlot, SessionWeatherData
from src.services.analysis.v2.race_pace_heatmap import RacePaceHeatmapPlot, RacePaceHeatmapData
from src.services.analysis.v2.track_evolution import TrackEvolutionPlot, TrackEvolutionData
from src.services.analysis.v2.theoretical_best import TheoreticalBestPlot, TheoreticalBestData
from src.services.analysis.v2.race_story import RaceStoryPlot, RaceStoryData
from src.services.analysis.v2.telemetry_track_map import TrackMapPlot, TrackMapData
from src.services.analysis.v2.lap_all_data import LapAllData
from src.services.analysis.v2.corner_duel import CornerDuelPlot, CornerDuelData
from src.services.analysis.v2.driver_radar import DriverRadarPlot, DriverRadarData

# V1 siblings for transparent fallback when livetiming lacks data.
from src.services.analysis.v1.top_speed import TopSpeedPlot as V1_TopSpeedPlot, TopSpeedData as V1_TopSpeedData
from src.services.analysis.v1.throttle_comparison import ThrottleComp as V1_ThrottleComp, ThrottleCompData as V1_ThrottleCompData
from src.services.analysis.v1.qualifying_results import QualiResults as V1_QualiResults
from src.services.analysis.v1.speed_distribution import SpeedDistributionPlot as V1_SpeedDistributionPlot, SpeedDistributionData as V1_SpeedDistributionData
from src.services.analysis.v1.driver_pace import DriverPacePlot as V1_DriverPacePlot, DriverPaceData as V1_DriverPaceData
from src.services.analysis.v1.teams_pace import TeamsPacePlot as V1_TeamsPacePlot, TeamsPaceData as V1_TeamsPaceData
from src.services.analysis.v1.lap_time_analysis import LapTimeAnalysisPlot as V1_LapTimeAnalysisPlot, LapTimeAnalysisData as V1_LapTimeAnalysisData
from src.services.analysis.v1.tyre_stint_usage import TyreStintUsagePlot as V1_TyreStintUsagePlot, TyreStintUsageData as V1_TyreStintUsageData
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
        latest_session = await run_in_threadpool(get_latest_finished_session)

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


# --- Lap Time Analysis (2 drivers) ---

@router.get('/lap-time-analysis-plot', tags=["API v2", "Qualifying"])
@apply_tiered_limit("standard")
async def lap_time_analysis_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    d1: str = Query(..., description="First driver TLA (e.g., VER)"),
    d2: str = Query(..., description="Second driver TLA (e.g., NOR)"),
    api_key: str = Depends(verify_api_key)
):
    """Generate Speed / Delta time / Throttle comparison for two drivers' fastest laps. Falls back to V1."""
    logger.info(f"Generating lap time analysis plot: Y{year} GP{gp} {session} {d1} vs {d2}")
    try:
        v1_secondary = (lambda: V1_LapTimeAnalysisPlot(year, gp, session, d1, d2)) if isinstance(gp, int) else None
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: LapTimeAnalysisPlot(year, gp, session, d1, d2),
            v1_secondary,
            primary_source="livetiming", secondary_source="fastf1",
            year=year, gp=gp, session=session, data_type="lap_time_analysis",
        )
        if not output_path:
            raise HTTPException(status_code=404, detail="No data available for this session/drivers")
        _track('lap-time-analysis', year, gp, session, d1, d2)
        return FileResponse(output_path, media_type="image/png")
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")
    except T1APIError:
        raise


@router.get('/lap-time-analysis-data', tags=["API v2", "Qualifying"])
@apply_tiered_limit("data")
async def lap_time_analysis_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    d1: str = Query(..., description="First driver TLA (e.g., VER)"),
    d2: str = Query(..., description="Second driver TLA (e.g., NOR)"),
    api_key: str = Depends(verify_api_key)
):
    """Get Speed/Throttle telemetry + delta-time series for two drivers' fastest laps. Falls back to V1."""
    logger.info(f"Fetching lap time analysis data: Y{year} GP{gp} {session} {d1} vs {d2}")
    try:
        v1_secondary = (lambda: V1_LapTimeAnalysisData(year, gp, session, d1, d2)) if isinstance(gp, int) else None
        result = await run_in_threadpool(
            with_fallback,
            lambda: LapTimeAnalysisData(year, gp, session, d1, d2),
            v1_secondary,
            primary_source="livetiming", secondary_source="fastf1",
            year=year, gp=gp, session=session, data_type="lap_time_analysis",
        )
        _track('lap-time-analysis', year, gp, session, d1, d2)
        return result
    except T1APIError:
        raise


# --- Pace Analysis ---

@router.get('/driver-pace-plot', tags=["API v2", "Pace Analysis"])
@apply_tiered_limit("standard")
async def driver_pace_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """Box-and-whisker pace plot per driver (107% quicklap filter). Falls back to V1 if available."""
    logger.info(f"Generating driver pace plot: Y{year} GP{gp} {session}")
    try:
        v1_secondary = (lambda: V1_DriverPacePlot(year, gp, session)) if isinstance(gp, int) else None
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: DriverPacePlot(year, gp, session),
            v1_secondary,
            primary_source="livetiming", secondary_source="fastf1",
            year=year, gp=gp, session=session, data_type="driver_pace",
        )
        _track('driver-pace', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/driver-pace-data', tags=["API v2", "Pace Analysis"])
@apply_tiered_limit("data")
async def driver_pace_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """JSON pace distribution per driver: lap times + min/q1/median/q3/max. Falls back to V1."""
    logger.info(f"Fetching driver pace data: Y{year} GP{gp} {session}")
    try:
        v1_secondary = (lambda: V1_DriverPaceData(year, gp, session)) if isinstance(gp, int) else None
        result = await run_in_threadpool(
            with_fallback,
            lambda: DriverPaceData(year, gp, session, True),
            v1_secondary,
            primary_source="livetiming", secondary_source="fastf1",
            year=year, gp=gp, session=session, data_type="driver_pace",
        )
        _track('driver-pace', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/teams-pace-plot', tags=["API v2", "Pace Analysis"])
@apply_tiered_limit("standard")
async def teams_pace_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """Box-and-whisker pace plot per team (laps from both drivers aggregated). Falls back to V1."""
    logger.info(f"Generating teams pace plot: Y{year} GP{gp} {session}")
    try:
        v1_secondary = (lambda: V1_TeamsPacePlot(year, gp, session)) if isinstance(gp, int) else None
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: TeamsPacePlot(year, gp, session),
            v1_secondary,
            primary_source="livetiming", secondary_source="fastf1",
            year=year, gp=gp, session=session, data_type="teams_pace",
        )
        _track('teams-pace', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/teams-pace-data', tags=["API v2", "Pace Analysis"])
@apply_tiered_limit("data")
async def teams_pace_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """JSON pace distribution per team. Falls back to V1."""
    logger.info(f"Fetching teams pace data: Y{year} GP{gp} {session}")
    try:
        v1_secondary = (lambda: V1_TeamsPaceData(year, gp, session)) if isinstance(gp, int) else None
        result = await run_in_threadpool(
            with_fallback,
            lambda: TeamsPaceData(year, gp, session, True),
            v1_secondary,
            primary_source="livetiming", secondary_source="fastf1",
            year=year, gp=gp, session=session, data_type="teams_pace",
        )
        _track('teams-pace', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/tyre-stint-usage-plot', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("standard")
async def tyre_stint_usage_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """Stint strategy timeline: per-driver compound stints across the race. Falls back to V1."""
    logger.info(f"Generating tyre stint usage plot (V2): Y{year} GP{gp} {session}")
    try:
        v1_secondary = (lambda: V1_TyreStintUsagePlot(year, gp, session)) if isinstance(gp, int) else None
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: TyreStintUsagePlot(year, gp, session),
            v1_secondary,
            primary_source="livetiming", secondary_source="fastf1",
            year=year, gp=gp, session=session, data_type="tyre_stint_usage",
        )
        _track('tyre-stint-usage', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/tyre-stint-usage-data', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("data")
async def tyre_stint_usage_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """JSON per-stint records (driver, compound, start_lap, end_lap, lap_count). Falls back to V1."""
    logger.info(f"Fetching tyre stint usage data (V2): Y{year} GP{gp} {session}")
    try:
        v1_secondary = (lambda: V1_TyreStintUsageData(year, gp, session)) if isinstance(gp, int) else None
        result = await run_in_threadpool(
            with_fallback,
            lambda: TyreStintUsageData(year, gp, session, True),
            v1_secondary,
            primary_source="livetiming", secondary_source="fastf1",
            year=year, gp=gp, session=session, data_type="tyre_stint_usage",
        )
        _track('tyre-stint-usage', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/position-changes-plot', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("standard")
async def position_changes_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """Classic position chart: per-driver race position across laps. Race/Sprint only."""
    logger.info(f"Generating position changes plot (V2): Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(PositionChangesPlot(), year, gp, session)
        _track('position-changes', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/position-changes-data', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("data")
async def position_changes_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """JSON per-driver position series (lap, position), ordered by finishing position. Race/Sprint only."""
    logger.info(f"Fetching position changes data (V2): Y{year} GP{gp} {session}")
    try:
        result = await run_in_threadpool(PositionChangesData(), year, gp, session)
        _track('position-changes', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/race-gaps-plot', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("standard")
async def race_gaps_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    reference: str = Query('leader', pattern='^(leader|average)$'),
    drivers: Optional[str] = Query(None, description="Comma-separated TLAs"),
    api_key: str = Depends(verify_api_key)
):
    """Race gaps / race trace: per-driver gap to leader or vs average pace, per lap. Race/Sprint only."""
    logger.info(f"Generating race gaps plot (V2): Y{year} GP{gp} {session} ref={reference}")
    try:
        output_path = await run_in_threadpool(
            RaceGapsPlot(), year, gp, session, reference, drivers
        )
        _track('race-gaps', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/race-gaps-data', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("data")
async def race_gaps_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    reference: str = Query('leader', pattern='^(leader|average)$'),
    drivers: Optional[str] = Query(None, description="Comma-separated TLAs"),
    api_key: str = Depends(verify_api_key)
):
    """JSON per-driver gap series (lap, gap_s), ordered by finishing order. Race/Sprint only."""
    logger.info(f"Fetching race gaps data (V2): Y{year} GP{gp} {session} ref={reference}")
    try:
        result = await run_in_threadpool(
            RaceGapsData(), year, gp, session, reference, drivers
        )
        _track('race-gaps', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/tyre-degradation-plot', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("standard")
async def tyre_degradation_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    driver: Optional[str] = Query(None, description="Optional driver TLA filter (e.g. VER)"),
    fuel_corrected: bool = Query(False, description="Apply fuel-burn correction to lap times"),
    api_key: str = Depends(verify_api_key)
):
    """Per-compound tyre degradation scatter + trendlines. Race/Sprint only."""
    logger.info(
        f"Generating tyre degradation plot (V2): Y{year} GP{gp} {session} "
        f"driver={driver} fuel_corrected={fuel_corrected}"
    )
    try:
        output_path = await run_in_threadpool(
            TyreDegradationPlot(), year, gp, session, driver, fuel_corrected
        )
        _track('tyre-degradation', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/tyre-degradation-data', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("data")
async def tyre_degradation_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    driver: Optional[str] = Query(None, description="Optional driver TLA filter (e.g. VER)"),
    fuel_corrected: bool = Query(False, description="Apply fuel-burn correction to lap times"),
    api_key: str = Depends(verify_api_key)
):
    """JSON per-compound degradation: points, deg_rate_s_per_lap, r_squared. Race/Sprint only."""
    logger.info(
        f"Fetching tyre degradation data (V2): Y{year} GP{gp} {session} "
        f"driver={driver} fuel_corrected={fuel_corrected}"
    )
    try:
        result = await run_in_threadpool(
            TyreDegradationData(), year, gp, session, driver, fuel_corrected
        )
        _track('tyre-degradation', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/pit-strategy-plot', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("standard")
async def pit_strategy_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """Pit strategy & undercut plot: stop timeline + undercut gains. Race/Sprint only."""
    logger.info(f"Generating pit strategy plot (V2): Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(PitStrategyPlot(), year, gp, session)
        _track('pit-strategy', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/pit-strategy-data', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("data")
async def pit_strategy_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """JSON pit strategy: stops (compound in/out, under_sc), undercuts, summary, free_changes. Race/Sprint only."""
    logger.info(f"Fetching pit strategy data (V2): Y{year} GP{gp} {session}")
    try:
        result = await run_in_threadpool(PitStrategyData(), year, gp, session)
        _track('pit-strategy', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/session-weather-plot', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("standard")
async def session_weather_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """Stacked weather / humidity / track-status timeline plot. All session types."""
    logger.info(f"Generating session weather plot (V2): Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(SessionWeatherPlot(), year, gp, session)
        _track('session-weather', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/session-weather-data', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("data")
async def session_weather_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """JSON weather series + track-status periods + race-control messages. All session types."""
    logger.info(f"Fetching session weather data (V2): Y{year} GP{gp} {session}")
    try:
        result = await run_in_threadpool(SessionWeatherData(), year, gp, session)
        _track('session-weather', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/race-pace-heatmap-plot', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("standard")
async def race_pace_heatmap_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """Driver x lap heatmap of delta to field median lap time. Race/Sprint only."""
    logger.info(f"Generating race pace heatmap plot (V2): Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(RacePaceHeatmapPlot(), year, gp, session)
        _track('race-pace-heatmap', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/race-pace-heatmap-data', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("data")
async def race_pace_heatmap_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """JSON driver x lap grid of delta to field median lap time, pit laps, SC laps. Race/Sprint only."""
    logger.info(f"Fetching race pace heatmap data (V2): Y{year} GP{gp} {session}")
    try:
        result = await run_in_threadpool(RacePaceHeatmapData(), year, gp, session)
        _track('race-pace-heatmap', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/track-evolution-plot', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("standard")
async def track_evolution_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    drivers: Optional[str] = Query(None, description="Comma-separated TLAs"),
    api_key: str = Depends(verify_api_key)
):
    """Session-best lap time evolution vs track temperature. Practice/Qualifying only."""
    logger.info(f"Generating track evolution plot (V2): Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(
            TrackEvolutionPlot(), year, gp, session, drivers
        )
        _track('track-evolution', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/track-evolution-data', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("data")
async def track_evolution_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    drivers: Optional[str] = Query(None, description="Comma-separated TLAs"),
    api_key: str = Depends(verify_api_key)
):
    """JSON overall + per-driver running-best lap series, plus track-temp series. Practice/Qualifying only."""
    logger.info(f"Fetching track evolution data (V2): Y{year} GP{gp} {session}")
    try:
        result = await run_in_threadpool(
            TrackEvolutionData(), year, gp, session, drivers
        )
        _track('track-evolution', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/theoretical-best-plot', tags=["API v2", "Qualifying Analysis"])
@apply_tiered_limit("standard")
async def theoretical_best_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Theoretical best lap dumbbell chart: best sectors combined vs actual best lap. Qualifying only."""
    logger.info(f"Generating theoretical best plot (V2): Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(TheoreticalBestPlot(), year, gp, session)
        _track('theoretical-best', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/theoretical-best-data', tags=["API v2", "Qualifying Analysis"])
@apply_tiered_limit("data")
async def theoretical_best_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """JSON per-driver theoretical_s, actual_s, delta_s sorted by fastest theoretical lap. Qualifying only."""
    logger.info(f"Fetching theoretical best data (V2): Y{year} GP{gp} {session}")
    try:
        result = await run_in_threadpool(TheoreticalBestData(), year, gp, session)
        _track('theoretical-best', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/race-story-plot', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("standard")
async def race_story_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """Race story timeline: gap-to-leader traces, pit stops, and annotated key moments. Race/Sprint only."""
    logger.info(f"Generating race story plot (V2): Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(RaceStoryPlot(), year, gp, session)
        _track('race-story', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/race-story-data', tags=["API v2", "Race Analysis"])
@apply_tiered_limit("data")
async def race_story_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    api_key: str = Depends(verify_api_key)
):
    """JSON per-driver gap-to-leader series + pit stops, plus numbered/captioned key moments. Race/Sprint only."""
    logger.info(f"Fetching race story data (V2): Y{year} GP{gp} {session}")
    try:
        result = await run_in_threadpool(RaceStoryData(), year, gp, session)
        _track('race-story', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/track-map-plot', tags=["API v2", "Telemetry"])
@apply_tiered_limit("standard")
async def track_map_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    driver: str = Query(..., description="Driver TLA (e.g., VER)"),
    color_by: str = Query('speed', pattern='^(speed|gear)$'),
    api_key: str = Depends(verify_api_key)
):
    """Fastest-lap telemetry track map colored by speed or gear, with braking zones. Any session."""
    logger.info(f"Generating track map plot (V2): Y{year} GP{gp} {session} driver={driver} color_by={color_by}")
    try:
        output_path = await run_in_threadpool(TrackMapPlot(), year, gp, session, driver, color_by)
        _track('track-map', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/track-map-data', tags=["API v2", "Telemetry"])
@apply_tiered_limit("data")
async def track_map_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    driver: str = Query(..., description="Driver TLA (e.g., VER)"),
    color_by: str = Query('speed', pattern='^(speed|gear)$'),
    api_key: str = Depends(verify_api_key)
):
    """JSON fastest-lap telemetry track map points, braking zones, and callouts. Any session."""
    logger.info(f"Fetching track map data (V2): Y{year} GP{gp} {session} driver={driver} color_by={color_by}")
    try:
        result = await run_in_threadpool(TrackMapData(), year, gp, session, driver, color_by)
        _track('track-map', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/lap-all-data', tags=["API v2", "Telemetry"])
@apply_tiered_limit("data")
async def lap_all_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    driver: str = Query(..., description="Driver TLA (e.g., VER)"),
    lap: int = Query(..., ge=1, description="Lap number (required)"),
    api_key: str = Depends(verify_api_key)
):
    """Everything for one driver's single lap: full telemetry time-series (speed/rpm/throttle/
    brake/gear/drs + X/Y/Z + distance), lap/tyre/sector/pit metadata, nearest weather sample,
    track status, and driver/session context. Any session."""
    logger.info(f"Fetching lap-all-data (V2): Y{year} GP{gp} {session} driver={driver} lap={lap}")
    try:
        result = await run_in_threadpool(LapAllData(), year, gp, session, driver, lap)
        _track('lap-all-data', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/corner-duel-plot', tags=["API v2", "Telemetry"])
@apply_tiered_limit("standard")
async def corner_duel_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    driver1: str = Query(..., description="First driver TLA (e.g., VER)"),
    driver2: str = Query(..., description="Second driver TLA (e.g., NOR)"),
    api_key: str = Depends(verify_api_key)
):
    """Corner-by-corner duel: apex speeds, cumulative delta, and per-corner delta gain. Any session."""
    logger.info(f"Generating corner duel plot (V2): Y{year} GP{gp} {session} {driver1} vs {driver2}")
    try:
        output_path = await run_in_threadpool(CornerDuelPlot(), year, gp, session, driver1, driver2)
        _track('corner-duel', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/corner-duel-data', tags=["API v2", "Telemetry"])
@apply_tiered_limit("data")
async def corner_duel_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    driver1: str = Query(..., description="First driver TLA (e.g., VER)"),
    driver2: str = Query(..., description="Second driver TLA (e.g., NOR)"),
    api_key: str = Depends(verify_api_key)
):
    """JSON corner-by-corner duel data: delta series, apex speeds, braking points, delta gain. Any session."""
    logger.info(f"Fetching corner duel data (V2): Y{year} GP{gp} {session} {driver1} vs {driver2}")
    try:
        result = await run_in_threadpool(CornerDuelData(), year, gp, session, driver1, driver2)
        _track('corner-duel', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get('/driver-radar-plot', tags=["API v2", "Telemetry"])
@apply_tiered_limit("standard")
async def driver_radar_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    drivers: Optional[str] = Query(None, description="Comma-separated TLAs (max 3); defaults to fastest 3"),
    portrait: bool = Query(False, description="Portrait 4:5 crop for social media"),
    api_key: str = Depends(verify_api_key)
):
    """Single-session driver performance radar (top speed, cornering, race/quali pace, consistency, braveness)."""
    logger.info(f"Generating driver radar plot (V2): Y{year} GP{gp} {session} drivers={drivers}")
    try:
        output_path = await run_in_threadpool(DriverRadarPlot(), year, gp, session, drivers, portrait)
        _track('driver-radar', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get('/driver-radar-data', tags=["API v2", "Telemetry"])
@apply_tiered_limit("data")
async def driver_radar_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    drivers: Optional[str] = Query(None, description="Comma-separated TLAs (max 3); defaults to fastest 3"),
    api_key: str = Depends(verify_api_key)
):
    """JSON single-session driver radar: 0-100 axis values + raw metrics per driver. Any session."""
    logger.info(f"Fetching driver radar data (V2): Y{year} GP{gp} {session} drivers={drivers}")
    try:
        result = await run_in_threadpool(DriverRadarData(), year, gp, session, drivers)
        _track('driver-radar', year, gp, session)
        return result
    except T1APIError:
        raise
