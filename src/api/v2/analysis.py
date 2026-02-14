from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool

from src.utils.logger import get_logger
from src.utils.auth import verify_api_key
from src.utils.rate_limiting import apply_tiered_limit

# Importing Turn One Core files
from src.scripts.simple.v2_top_speed import TopSpeedPlot_Telemetry, TopSpeedData_Telemetry, TopSpeedPlot_SpeedTrap, TopSpeedData_SpeedTrap 

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v2")


# --- Simple Analysis Endpoints ---

@router.get('/top-speed-telemetry-plot', tags=["API v2", "Simple Analysis"])
@apply_tiered_limit("standard")
async def top_speed_telemetry_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: int = Query(1, ge=1, le=24),
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
    gp: int = Query(1, ge=1, le=24),
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
    gp: int = Query(1, ge=1, le=24),
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
    gp: int = Query(1, ge=1, le=24),
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