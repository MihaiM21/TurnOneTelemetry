from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool
from typing import Union
from src.utils.logger import get_logger
from src.utils.auth import verify_api_key
from src.utils.rate_limiting import apply_tiered_limit

# Importing Turn One Core files
from src.scripts.simple.v2_top_speed import TopSpeedPlot_Telemetry, TopSpeedData_Telemetry, TopSpeedPlot_SpeedTrap, TopSpeedData_SpeedTrap
from src.scripts.simple.v2_throttle_comparison import ThrottleComp, ThrottleCompData
from src.scripts.simple.v2_speed_distribution import SpeedDistributionPlot, SpeedDistributionData
from src.scripts.simple.v2_laptimes_distribution import LaptimesDistribution
from src.scripts.quali_practice.v2_qualifying_results import QualiResultsPlot, QualiResultsData
from src.scripts.quali_practice.v2_throttleBrake_comparison import ThrottleBrakeComp, ThrottleBrakeCompData
from src.scripts.quali_practice.v2_track_comparison import TrackComparisonPlot, TrackComparisonData

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v2")


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
    try:
        logger.info(f"Generating top speed plot: Y{year} GP{gp} {session}")
        output_path = await run_in_threadpool(TopSpeedPlot_Telemetry, year, gp, session)

        # Track session if tracker is available
        try:
            from src.utils.session_tracker import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('top-speed', year, gp, session)
        except:
            pass

        return FileResponse(output_path, media_type="image/png")
    except FileNotFoundError:
        logger.error(f"Plot file not found: Y{year} GP{gp} {session}")
        raise HTTPException(status_code=404, detail="Plot not found")
    except Exception as e:
        logger.error(f"Error generating top speed plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")
    
@router.get('/top-speed-telemetry-data', tags=["API v2", "Simple Analysis"])
@apply_tiered_limit("data")
async def top_speed_telemetry_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    try:
        logger.info(f"Fetching top speed data: Y{year} GP{gp} {session}")
        result = await run_in_threadpool(TopSpeedData_Telemetry, year, gp, session, True)
        
        # Track session if tracker is available
        try:
            from src.utils.session_tracker import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('top-speed', year, gp, session)
        except:
            pass
        
        # Data functions now always return list directly (from MongoDB or processed)
        return result
    except Exception as e:
        logger.error(f"Error fetching top speed data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")
    
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
            from src.utils.session_tracker import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('top-speed', year, gp, session)
        except:
            pass

        return FileResponse(output_path, media_type="image/png")
    except FileNotFoundError:
        logger.error(f"Plot file not found: Y{year} GP{gp} {session}")
        raise HTTPException(status_code=404, detail="Plot not found")
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
            from src.utils.session_tracker import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('top-speed', year, gp, session)
        except:
            pass
        
        # Data functions now always return list directly (from MongoDB or processed)
        return result
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
    """Generate PNG plot comparing throttle application"""
    try:
        logger.info(f"Generating throttle comparison plot: Y{year} GP{gp} {session}")
        output_path = await run_in_threadpool(ThrottleComp, year, gp, session)
        
        # Track session if tracker is available
        try:
            from src.utils.session_tracker import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('throttle-comparison', year, gp, session)
        except:
            pass
        
        return FileResponse(output_path, media_type='image/png')
    except Exception as e:
        logger.error(f"Error generating throttle plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")

@router.get('/throttle-comparison-data', tags=["API v2", "Simple Analysis"])
@apply_tiered_limit("data")
async def throttle_comparison_data(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """Get raw JSON data for throttle comparison"""
    try:
        logger.info(f"Fetching throttle comparison data: Y{year} GP{gp} {session}")
        result = await run_in_threadpool(ThrottleCompData, year, gp, session)
        
        # Track session if tracker is available
        try:
            from src.utils.session_tracker import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('throttle-comparison', year, gp, session)
        except:
            pass
        
        # Check if result is cached data (dict/list) or file path (str)
        if isinstance(result, (dict, list)):
            return result
        else:
            return FileResponse(result, media_type='application/json')
    except Exception as e:
        logger.error(f"Error fetching throttle data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")

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
            from src.utils.session_tracker import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('speed-distribution', year, gp, session)
        except:
            pass
        
        return FileResponse(output_path, media_type='image/png')
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
            from src.utils.session_tracker import SessionTracker
            session_tracker = SessionTracker()
            session_tracker.track_session('speed-distribution', year, gp, session)
        except:
            pass
        
        if isinstance(result, (dict, list)):
            return result
        else:
            return FileResponse(result, media_type='application/json')
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
    """Generate PNG plot of qualifying results sorted by lap time delta"""
    try:
        logger.info(f"Generating qualifying results plot: Y{year} GP{gp} {session}")
        output_path = await run_in_threadpool(QualiResultsPlot, year, gp, session)
        if not output_path:
            raise HTTPException(status_code=404, detail="No data available for this session")
        return FileResponse(output_path, media_type="image/png")
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")
    except Exception as e:
        logger.error(f"Error generating qualifying results plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")


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
    except Exception as e:
        logger.error(f"Error fetching track comparison data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")