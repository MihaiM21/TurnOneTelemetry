from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool
from typing import Optional, Union
from src.api.deps import guard_session_params, validate_driver, validate_gp, validate_session
from src.core.logging import get_logger
from src.core.security.api_keys import verify_api_key
from src.core.security.rate_limiting import apply_tiered_limit
from src.services.orchestrator_helpers import get_latest_finished_session
from src.services.orchestrator import latest_session_analised_v2
from src.api.schemas.common import ANALYSIS_ERROR_RESPONSES, COMMON_ERROR_RESPONSES, ErrorEnvelope
from src.api.schemas.analysis import (
    DashboardResponse,
    TopSpeedTelemetryDataResponse,
    TopSpeedSpeedTrapDataResponse,
    ThrottleComparisonDataResponse,
    SpeedDistributionDataResponse,
    LaptimesDistributionDataResponse,
    QualifyingResultsDataResponse,
    ThrottleBrakeComparisonResponse,
    TrackComparisonResponse,
    LapTimeAnalysisResponse,
    DriverPaceDataResponse,
    TeamsPaceDataResponse,
    TyreStintUsageDataResponse,
    PositionChangesDataResponse,
    RaceGapsDataResponse,
    TyreDegradationDataResponse,
    PitStrategyResponse,
    SessionWeatherResponse,
    RacePaceHeatmapResponse,
    TrackEvolutionResponse,
    TheoreticalBestDataResponse,
    RaceStoryResponse,
    TrackMapResponse,
    LapAllDataResponse,
    CornerDuelResponse,
    LapDuelResponse,
    DriverRadarResponse,
    EnergyClippingResponse,
    CornerSpeedProfileResponse,
    EfficiencyScatterResponse,
    FieldDominanceResponse,
    SectorGapResponse,
)

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
from src.services.analysis.v2.lap_duel import LapDuelPlot, LapDuelData
from src.services.analysis.v2.driver_radar import DriverRadarPlot, DriverRadarData
from src.services.analysis.v2.energy_clipping import (
    FIRST_YEAR as ENERGY_CLIPPING_FIRST_YEAR,
    EnergyClippingPlot,
    EnergyClippingData,
)
from src.services.analysis.v2.car_characteristics import (
    CornerSpeedProfilePlot,
    CornerSpeedProfileData,
    EfficiencyScatterPlot,
    EfficiencyScatterData,
)
from src.services.analysis.v2.field_dominance import FieldDominancePlot, FieldDominanceData
from src.services.analysis.v2.sector_gap import SESSIONS as SECTOR_GAP_SESSIONS, SectorGapPlot, SectorGapData

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
from src.services.plotting.canvas import get_format

logger = get_logger(__name__)


def _track(event_name, *args):
    try:
        from src.core.observability.analytics import SessionTracker
        SessionTracker().track_session(event_name, *args)
    except Exception:
        pass


#: Documentation-only shape for every ``-plot`` endpoint: a PNG body, no JSON schema.
_PNG_RESPONSE = {"content": {"image/png": {}}, "description": "PNG plot."}

#: Description of the optional ``format`` query parameter on social-media capable plots.
_FORMAT_DESC = "landscape|square|portrait|story — social-media canvas; omit for the classic image"


def _fmt_kwargs(format: Optional[str]) -> dict:
    """``{"fmt": format}`` when a canvas was requested, else ``{}``.

    Passing nothing (rather than ``fmt=None``) when ``format`` is omitted keeps the service call exactly what
    it was before the parameter existed. An unknown name raises ``ValueError`` here (``canvas.get_format``),
    which the app-level handler turns into a 400 -- validated at the edge so endpoints that catch a bare
    ``Exception`` around the service call cannot turn it into a 500.
    """
    if format is None:
        return {}
    get_format(format)
    return {"fmt": format}

router = APIRouter(
    prefix="/api/v2",
    # Validates `session` / `driver*` from the raw query string. Declares no
    # parameters of its own, so the OpenAPI schema and the frozen public
    # query-parameter contract are untouched.
    dependencies=[Depends(guard_session_params)],
)


@router.get(
    '/dashboard',
    tags=["API v2", "Latest Session"],
    summary="Latest session dashboard",
    operation_id="v2_dashboard_latest_session",
    responses={
        200: {"model": DashboardResponse},
        404: {"model": ErrorEnvelope, "description": "No finished sessions found in the schedule."},
        **COMMON_ERROR_RESPONSES,
    },
)
@apply_tiered_limit("data")
async def get_dashboard_data_v2(request: Request, api_key: str = Depends(verify_api_key)):
    """
    Get main latest session data via the livetiming-only V2 path.
    Automatically detects the most recent completed session from the F1
    static index (no FastF1).

    Unlike the rest of V2, this response is **not** immutable: it targets
    the latest finished session and its content changes as a race weekend
    progresses (each new completed session becomes the new "latest").
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

@router.get(
    '/top-speed-telemetry-plot',
    tags=["API v2", "Simple Analysis"],
    summary="Top speed per team (telemetry) plot",
    operation_id="v2_top_speed_telemetry_plot",
    description=(
        "PNG plot of each team's maximum speed for the session, computed from CarData "
        "telemetry. Falls back to V1/FastF1 if livetiming data is unavailable (the fallback image is always "
        "the classic one, whatever `format` asks for)."
    ),
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def top_speed_telemetry_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    format: Optional[str] = Query(None, description=_FORMAT_DESC),
    api_key: str = Depends(verify_api_key)
):
    logger.info(f"Generating top speed plot: Y{year} GP{gp} {session}")
    fmt_kwargs = _fmt_kwargs(format)
    try:
        # V2 only supports integer round numbers for V1 fallback; skip if string.
        v1_secondary = (lambda: V1_TopSpeedPlot(year, gp, session)) if isinstance(gp, int) else None
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: TopSpeedPlot_Telemetry(year, gp, session, **fmt_kwargs),
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

@router.get(
    '/top-speed-telemetry-data',
    tags=["API v2", "Simple Analysis"],
    summary="Top speed per team (telemetry) data",
    operation_id="v2_top_speed_telemetry_data",
    description=(
        "JSON per-team top speed for the session, computed from CarData telemetry. "
        "Falls back to V1/FastF1 if livetiming data is unavailable."
    ),
    responses={200: {"model": TopSpeedTelemetryDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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

@router.get(
    '/top-speed-st-plot',
    tags=["API v2", "Simple Analysis"],
    summary="Top speed per team (speed trap) plot",
    operation_id="v2_top_speed_speed_trap_plot",
    description=(
        "PNG plot of each team's official Speed Trap top speed for the session (no V1 fallback)."
    ),
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def top_speed_st_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    format: Optional[str] = Query(None, description=_FORMAT_DESC),
    api_key: str = Depends(verify_api_key)
):
    fmt_kwargs = _fmt_kwargs(format)  # outside the try: a bad format is a 400, not the 500 below
    try:
        logger.info(f"Generating top speed plot: Y{year} GP{gp} {session}")
        output_path = await run_in_threadpool(TopSpeedPlot_SpeedTrap, year, gp, session, **fmt_kwargs)

        _track('top-speed', year, gp, session)

        return FileResponse(output_path, media_type="image/png")
    except FileNotFoundError:
        logger.error(f"Plot file not found: Y{year} GP{gp} {session}")
        raise HTTPException(status_code=404, detail="Plot not found")
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error generating top speed plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")
    
@router.get(
    '/top-speed-st-data',
    tags=["API v2", "Simple Analysis"],
    summary="Top speed per team (speed trap) data",
    operation_id="v2_top_speed_speed_trap_data",
    description=(
        "JSON per-team official Speed Trap top speed for the session (no V1 fallback)."
    ),
    responses={200: {"model": TopSpeedSpeedTrapDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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

        _track('top-speed', year, gp, session)

        # Data functions now always return list directly (from MongoDB or processed)
        return result
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error fetching top speed data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch data")
    

# Throttle comparison endpoints
@router.get(
    '/throttle-comparison-plot',
    tags=["API v2", "Simple Analysis"],
    summary="Throttle comparison plot",
    operation_id="v2_throttle_comparison_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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

@router.get(
    '/throttle-comparison-data',
    tags=["API v2", "Simple Analysis"],
    summary="Throttle comparison data",
    operation_id="v2_throttle_comparison_data",
    responses={200: {"model": ThrottleComparisonDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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

@router.get(
    '/speed-distribution-plot',
    tags=["API v2", "Simple Analysis"],
    summary="Speed distribution plot",
    operation_id="v2_speed_distribution_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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
        except Exception as track_err:
            logger.debug(f"Session tracking failed: {track_err}")

        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except Exception as e:
        logger.error(f"Error generating speed distribution plot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate plot")

@router.get(
    '/speed-distribution-data',
    tags=["API v2", "Simple Analysis"],
    summary="Speed distribution data",
    operation_id="v2_speed_distribution_data",
    responses={200: {"model": SpeedDistributionDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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
        except Exception as track_err:
            logger.debug(f"Session tracking failed: {track_err}")

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

@router.get(
    '/laptimes-distribution-data',
    tags=["API v2", "Simple Analysis"],
    summary="Lap times distribution data",
    operation_id="v2_laptimes_distribution_data",
    responses={200: {"model": LaptimesDistributionDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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

@router.get(
    '/qualifying-results-plot',
    tags=["API v2", "Qualifying"],
    summary="Qualifying results plot",
    operation_id="v2_qualifying_results_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def qualifying_results_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    format: Optional[str] = Query(None, description=_FORMAT_DESC),
    api_key: str = Depends(verify_api_key)
):
    """Generate PNG plot of qualifying results sorted by lap time delta. Falls back to V1 if livetiming lacks data.

    The V1 fallback image is always the classic one, whatever `format` asks for.
    """
    logger.info(f"Generating qualifying results plot: Y{year} GP{gp} {session}")
    fmt_kwargs = _fmt_kwargs(format)
    try:
        v1_secondary = (lambda: V1_QualiResults(year, gp, session)) if isinstance(gp, int) else None
        output_path = await run_in_threadpool(
            with_fallback,
            lambda: QualiResultsPlot(year, gp, session, **fmt_kwargs),
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


@router.get(
    '/qualifying-results-data',
    tags=["API v2", "Qualifying"],
    summary="Qualifying results data",
    operation_id="v2_qualifying_results_data",
    responses={200: {"model": QualifyingResultsDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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

@router.get(
    '/throttle-brake-comparison-plot',
    tags=["API v2", "Qualifying"],
    summary="Throttle/brake comparison plot",
    operation_id="v2_throttle_brake_comparison_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/throttle-brake-comparison-data',
    tags=["API v2", "Qualifying"],
    summary="Throttle/brake comparison data",
    operation_id="v2_throttle_brake_comparison_data",
    responses={200: {"model": ThrottleBrakeComparisonResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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

@router.get(
    '/track-comparison-plot',
    tags=["API v2", "Qualifying"],
    summary="Track minisector comparison plot",
    operation_id="v2_track_comparison_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def track_comparison_plot(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    d1: str = Query(..., description="First driver TLA (e.g., VER)"),
    d2: str = Query(..., description="Second driver TLA (e.g., NOR)"),
    format: Optional[str] = Query(None, description=_FORMAT_DESC),
    api_key: str = Depends(verify_api_key)
):
    """Generate color-coded track map showing which driver is faster in each minisector"""
    fmt_kwargs = _fmt_kwargs(format)  # outside the try: a bad format is a 400, not the 500 below
    try:
        logger.info(f"Generating track comparison plot: Y{year} GP{gp} {session} {d1} vs {d2}")
        output_path = await run_in_threadpool(TrackComparisonPlot, year, gp, session, d1, d2, **fmt_kwargs)
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


@router.get(
    '/track-comparison-data',
    tags=["API v2", "Qualifying"],
    summary="Track minisector comparison data",
    operation_id="v2_track_comparison_data",
    responses={200: {"model": TrackComparisonResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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

@router.get(
    '/lap-time-analysis-plot',
    tags=["API v2", "Qualifying"],
    summary="Lap time analysis plot",
    operation_id="v2_lap_time_analysis_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/lap-time-analysis-data',
    tags=["API v2", "Qualifying"],
    summary="Lap time analysis data",
    operation_id="v2_lap_time_analysis_data",
    responses={200: {"model": LapTimeAnalysisResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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

@router.get(
    '/driver-pace-plot',
    tags=["API v2", "Pace Analysis"],
    summary="Driver pace distribution plot",
    operation_id="v2_driver_pace_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/driver-pace-data',
    tags=["API v2", "Pace Analysis"],
    summary="Driver pace distribution data",
    operation_id="v2_driver_pace_data",
    responses={200: {"model": DriverPaceDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/teams-pace-plot',
    tags=["API v2", "Pace Analysis"],
    summary="Team pace distribution plot",
    operation_id="v2_teams_pace_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/teams-pace-data',
    tags=["API v2", "Pace Analysis"],
    summary="Team pace distribution data",
    operation_id="v2_teams_pace_data",
    responses={200: {"model": TeamsPaceDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/tyre-stint-usage-plot',
    tags=["API v2", "Race Analysis"],
    summary="Tyre stint usage plot",
    operation_id="v2_tyre_stint_usage_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/tyre-stint-usage-data',
    tags=["API v2", "Race Analysis"],
    summary="Tyre stint usage data",
    operation_id="v2_tyre_stint_usage_data",
    responses={200: {"model": TyreStintUsageDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/position-changes-plot',
    tags=["API v2", "Race Analysis"],
    summary="Race position changes plot",
    operation_id="v2_position_changes_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def position_changes_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    format: Optional[str] = Query(None, description=_FORMAT_DESC),
    api_key: str = Depends(verify_api_key)
):
    """Classic position chart: per-driver race position across laps. Race/Sprint only."""
    logger.info(f"Generating position changes plot (V2): Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(PositionChangesPlot(), year, gp, session, **_fmt_kwargs(format))
        _track('position-changes', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/position-changes-data',
    tags=["API v2", "Race Analysis"],
    summary="Race position changes data",
    operation_id="v2_position_changes_data",
    responses={200: {"model": PositionChangesDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/race-gaps-plot',
    tags=["API v2", "Race Analysis"],
    summary="Race gaps / race trace plot",
    operation_id="v2_race_gaps_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/race-gaps-data',
    tags=["API v2", "Race Analysis"],
    summary="Race gaps / race trace data",
    operation_id="v2_race_gaps_data",
    responses={200: {"model": RaceGapsDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/tyre-degradation-plot',
    tags=["API v2", "Race Analysis"],
    summary="Tyre degradation plot",
    operation_id="v2_tyre_degradation_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/tyre-degradation-data',
    tags=["API v2", "Race Analysis"],
    summary="Tyre degradation data",
    operation_id="v2_tyre_degradation_data",
    responses={200: {"model": TyreDegradationDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/pit-strategy-plot',
    tags=["API v2", "Race Analysis"],
    summary="Pit strategy & undercuts plot",
    operation_id="v2_pit_strategy_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def pit_strategy_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    format: Optional[str] = Query(None, description=_FORMAT_DESC),
    api_key: str = Depends(verify_api_key)
):
    """Pit strategy & undercut plot: stop timeline + undercut gains. Race/Sprint only."""
    logger.info(f"Generating pit strategy plot (V2): Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(PitStrategyPlot(), year, gp, session, **_fmt_kwargs(format))
        _track('pit-strategy', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/pit-strategy-data',
    tags=["API v2", "Race Analysis"],
    summary="Pit strategy & undercuts data",
    operation_id="v2_pit_strategy_data",
    responses={200: {"model": PitStrategyResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/session-weather-plot',
    tags=["API v2", "Race Analysis"],
    summary="Session weather timeline plot",
    operation_id="v2_session_weather_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/session-weather-data',
    tags=["API v2", "Race Analysis"],
    summary="Session weather timeline data",
    operation_id="v2_session_weather_data",
    responses={200: {"model": SessionWeatherResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/race-pace-heatmap-plot',
    tags=["API v2", "Race Analysis"],
    summary="Race pace heatmap plot",
    operation_id="v2_race_pace_heatmap_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/race-pace-heatmap-data',
    tags=["API v2", "Race Analysis"],
    summary="Race pace heatmap data",
    operation_id="v2_race_pace_heatmap_data",
    responses={200: {"model": RacePaceHeatmapResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/track-evolution-plot',
    tags=["API v2", "Race Analysis"],
    summary="Track evolution plot",
    operation_id="v2_track_evolution_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/track-evolution-data',
    tags=["API v2", "Race Analysis"],
    summary="Track evolution data",
    operation_id="v2_track_evolution_data",
    responses={200: {"model": TrackEvolutionResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/theoretical-best-plot',
    tags=["API v2", "Qualifying"],
    summary="Theoretical best lap plot",
    operation_id="v2_theoretical_best_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def theoretical_best_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    format: Optional[str] = Query(None, description=_FORMAT_DESC),
    api_key: str = Depends(verify_api_key)
):
    """Theoretical best lap dumbbell chart: best sectors combined vs actual best lap. Qualifying only."""
    logger.info(f"Generating theoretical best plot (V2): Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(TheoreticalBestPlot(), year, gp, session, **_fmt_kwargs(format))
        _track('theoretical-best', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/theoretical-best-data',
    tags=["API v2", "Qualifying"],
    summary="Theoretical best lap data",
    operation_id="v2_theoretical_best_data",
    responses={200: {"model": TheoreticalBestDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/race-story-plot',
    tags=["API v2", "Race Analysis"],
    summary="Race story timeline plot",
    operation_id="v2_race_story_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def race_story_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('R'),
    format: Optional[str] = Query(None, description=_FORMAT_DESC),
    api_key: str = Depends(verify_api_key)
):
    """Race story timeline: gap-to-leader traces, pit stops, and annotated key moments. Race/Sprint only."""
    logger.info(f"Generating race story plot (V2): Y{year} GP{gp} {session}")
    try:
        output_path = await run_in_threadpool(RaceStoryPlot(), year, gp, session, **_fmt_kwargs(format))
        _track('race-story', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/race-story-data',
    tags=["API v2", "Race Analysis"],
    summary="Race story timeline data",
    operation_id="v2_race_story_data",
    responses={200: {"model": RaceStoryResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/track-map-plot',
    tags=["API v2", "Telemetry"],
    summary="Telemetry track map plot",
    operation_id="v2_track_map_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/track-map-data',
    tags=["API v2", "Telemetry"],
    summary="Telemetry track map data",
    operation_id="v2_track_map_data",
    responses={200: {"model": TrackMapResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/lap-all-data',
    tags=["API v2", "Telemetry"],
    summary="Full single-lap telemetry & metadata",
    operation_id="v2_lap_all_data",
    responses={200: {"model": LapAllDataResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/corner-duel-plot',
    tags=["API v2", "Telemetry"],
    summary="Corner-by-corner duel plot",
    operation_id="v2_corner_duel_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/corner-duel-data',
    tags=["API v2", "Telemetry"],
    summary="Corner-by-corner duel data",
    operation_id="v2_corner_duel_data",
    responses={200: {"model": CornerDuelResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


_LAP_DUEL_DOC = """
Each side is one lap, chosen one of four ways (independently per side):

- **fastest** (default): the driver's fastest clean lap in the session.
- **lap=N**: that exact lap number, e.g. a race battle or a driver against themselves.
- **segment=Q1|Q2|Q3**: the driver's best lap completed inside that part of qualifying.
- **another session**: side B may live in a different session via `year2`/`gp2`/`session2`
  (2025 pole vs 2026 pole, qualifying vs race pace). Unset `year2`/`gp2`/`session2` default to side A's.

Both laps are placed on one distance grid by lap fraction, so cross-year laps line up corner for corner
and the delta ends at exactly the lap-time difference. **Delta convention: `delta = t_b - t_a`;
positive means driver2 is behind driver1.** `detail=full` adds derived longitudinal/lateral g
(`accelerations`), computed from ~4 Hz telemetry and indicative only. Both sides naming the same lap,
an unknown `segment`, `detail` or `format` are rejected with 400.
"""

_LAP_DUEL_PLOT_DESC = ("Two laps overlaid on one distance axis (speed, cumulative delta, throttle, brake, gear, "
                       "optional g-forces), a track map showing who gained where, and the biggest time swings. "
                       "Any session.\n\n" + _LAP_DUEL_DOC)
_LAP_DUEL_DATA_DESC = ("Both laps resampled onto one distance grid (speed, throttle 0..1, brake 0/1, gear, rpm, DRS), "
                       "the cumulative delta, corners, apexes, per-section time gains, a downsampled track line "
                       "and quotable highlights. Any session.\n\n" + _LAP_DUEL_DOC)


def _lap_duel_params(gp2, session2):
    """Validate the optional side-B addressing the router-level guard does not see."""
    return (validate_gp(gp2) if gp2 is not None and str(gp2).strip() != "" else None,
            validate_session(session2) if session2 is not None and session2.strip() != "" else None)


@router.get(
    '/lap-duel-plot',
    tags=["API v2", "Telemetry"],
    summary="Lap duel plot",
    operation_id="v2_lap_duel_plot",
    description=_LAP_DUEL_PLOT_DESC,
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def lap_duel_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    driver1: str = Query(..., description="First driver TLA (e.g., VER)"),
    driver2: str = Query(..., description="Second driver TLA (e.g., NOR)"),
    lap1: Optional[int] = Query(None, ge=1, description="Lap number for driver1 (default: fastest lap)"),
    lap2: Optional[int] = Query(None, ge=1, description="Lap number for driver2 (default: fastest lap)"),
    segment1: Optional[str] = Query(None, description="Qualifying part for driver1's best lap: Q1|Q2|Q3"),
    segment2: Optional[str] = Query(None, description="Qualifying part for driver2's best lap: Q1|Q2|Q3"),
    year2: Optional[int] = Query(None, ge=2018, le=2030, description="Season for driver2's lap (default: year)"),
    gp2: Optional[Union[int, str]] = Query(None, description="Event for driver2's lap (default: gp)"),
    session2: Optional[str] = Query(None, description="Session for driver2's lap (default: session)"),
    detail: str = Query("standard", description="standard | full (adds long/lat g panels)"),
    format: Optional[str] = Query(None, description="Canvas: landscape (default) | square | portrait | story"),
    hero: bool = Query(False, description="Include driver headshots on the driver plates"),
    api_key: str = Depends(verify_api_key)
):
    """Lap Duel plot: two laps on one distance axis with a track map of who gained where. Any session.

    See the endpoint description for the four lap-selection modes and the delta convention.
    """
    logger.info(f"Generating lap duel plot (V2): Y{year} GP{gp} {session} {driver1} vs {driver2}")
    gp2, session2 = _lap_duel_params(gp2, session2)
    session = validate_session(session)
    try:
        output_path = await run_in_threadpool(
            LapDuelPlot(), year, gp, session, driver1, driver2, lap1, lap2, segment1, segment2,
            year2, gp2, session2, detail, format, hero,
        )
        _track('lap-duel', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/lap-duel-data',
    tags=["API v2", "Telemetry"],
    summary="Lap duel data",
    operation_id="v2_lap_duel_data",
    description=_LAP_DUEL_DATA_DESC,
    responses={200: {"model": LapDuelResponse}, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("data")
async def lap_duel_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    driver1: str = Query(..., description="First driver TLA (e.g., VER)"),
    driver2: str = Query(..., description="Second driver TLA (e.g., NOR)"),
    lap1: Optional[int] = Query(None, ge=1, description="Lap number for driver1 (default: fastest lap)"),
    lap2: Optional[int] = Query(None, ge=1, description="Lap number for driver2 (default: fastest lap)"),
    segment1: Optional[str] = Query(None, description="Qualifying part for driver1's best lap: Q1|Q2|Q3"),
    segment2: Optional[str] = Query(None, description="Qualifying part for driver2's best lap: Q1|Q2|Q3"),
    year2: Optional[int] = Query(None, ge=2018, le=2030, description="Season for driver2's lap (default: year)"),
    gp2: Optional[Union[int, str]] = Query(None, description="Event for driver2's lap (default: gp)"),
    session2: Optional[str] = Query(None, description="Session for driver2's lap (default: session)"),
    detail: str = Query("standard", description="standard | full (adds derived long/lat g)"),
    api_key: str = Depends(verify_api_key)
):
    """JSON Lap Duel: both laps on one distance grid, cumulative delta, sections and highlights. Any session.

    See the endpoint description for the four lap-selection modes and the delta convention.
    """
    logger.info(f"Fetching lap duel data (V2): Y{year} GP{gp} {session} {driver1} vs {driver2}")
    gp2, session2 = _lap_duel_params(gp2, session2)
    session = validate_session(session)
    try:
        result = await run_in_threadpool(
            LapDuelData(), year, gp, session, driver1, driver2, lap1, lap2, segment1, segment2,
            year2, gp2, session2, detail,
        )
        _track('lap-duel', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get(
    '/driver-radar-plot',
    tags=["API v2", "Telemetry"],
    summary="Driver performance radar plot",
    operation_id="v2_driver_radar_plot",
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
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


@router.get(
    '/driver-radar-data',
    tags=["API v2", "Telemetry"],
    summary="Driver performance radar data",
    operation_id="v2_driver_radar_data",
    responses={200: {"model": DriverRadarResponse}, **ANALYSIS_ERROR_RESPONSES},
)
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


# ---------------------------------------------------------------------------
# Field-wide telemetry charts: energy clipping, car characteristics, field dominance
# ---------------------------------------------------------------------------
_ENERGY_CLIPPING_DOC = """
**2026+ only**: requests for an earlier season are rejected with 404, because the pre-2026 hybrid rules make
the measurement meaningless (a 2025 lap reads zero).

For every driver's fastest clean lap this finds the stretches where speed **falls while the driver is flat out**
(throttle >= 98 %, brake off, sustained for >= 40 m and >= 3 km/h): the car has run out of deployable electrical
energy, or is harvesting on the straight. Drag alone cannot slow a car at full throttle, so a normal drag-limited
plateau is never flagged. That also makes the result deliberately conservative, hence **estimated**: clipping that
merely flattens the speed curve is not counted. `time_lost_s` is the time over the zone compared with holding
the zone's entry speed.
"""


def _require_energy_clipping_year(year: int) -> None:
    """404 for seasons before the 2026 power units.

    The service refuses these with ``DataNotAvailableError``, which the app maps to a *retryable* 503; a
    season that can never have the measurement deserves a plain 404 instead.
    """
    if year < ENERGY_CLIPPING_FIRST_YEAR:
        raise HTTPException(
            status_code=404,
            detail=f"Energy clipping is measured for the {ENERGY_CLIPPING_FIRST_YEAR}+ power units only",
        )


def _require_sector_gap_session(session: str) -> None:
    """404 for sessions that are not Q / SQ (a permanent condition, not the retryable 503 the service maps to)."""
    if session not in SECTOR_GAP_SESSIONS:
        raise HTTPException(
            status_code=404, detail=f"sector gap exists for {' and '.join(SECTOR_GAP_SESSIONS)} sessions only"
        )


_CORNER_SPEED_DOC = """
Corners are classed by the field-median apex speed there (slow < 120 km/h, medium 120-200, fast > 200; flat-out
kinks above 280 km/h are dropped and chicanes collapse to their slowest piece). Each team's best lap contributes
its minimum speed within 50 m of every corner, averaged per class; `delta_kmh` is the gap to the class best.
"""


@router.get(
    '/energy-clipping-plot',
    tags=["API v2", "Telemetry"],
    summary="Energy clipping plot",
    operation_id="v2_energy_clipping_plot",
    description=(
        "Per-driver estimated energy-clipping time loss with the speed trace and clipping zones on a track map. "
        "`driver` picks whose zones are highlighted (default: the pole lap).\n\n" + _ENERGY_CLIPPING_DOC
    ),
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def energy_clipping_plot_v2(
    request: Request,
    year: int = Query(2026, ge=2018, le=2030, description="Season; 2026 or later (earlier years return 404)"),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    driver: Optional[str] = Query(None, description="Driver TLA whose zones are highlighted (default: pole lap)"),
    format: Optional[str] = Query(None, description=_FORMAT_DESC),
    api_key: str = Depends(verify_api_key)
):
    """Energy clipping plot: where each car's speed falls at full throttle. 2026+ power units only."""
    logger.info(f"Generating energy clipping plot (V2): Y{year} GP{gp} {session} driver={driver}")
    session = validate_session(session)
    driver = validate_driver(driver)
    _require_energy_clipping_year(year)
    try:
        output_path = await run_in_threadpool(EnergyClippingPlot(), year, gp, session, driver, format)
        _track('energy-clipping', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/energy-clipping-data',
    tags=["API v2", "Telemetry"],
    summary="Energy clipping data",
    operation_id="v2_energy_clipping_data",
    description=(
        "Per-driver leaderboard (`time_lost_s`, `clip_m`, `kmh_lost_max`, the individual zones), each driver's "
        "downsampled speed trace and the pole lap's racing line so zones can be drawn on a map.\n\n"
        + _ENERGY_CLIPPING_DOC
    ),
    responses={200: {"model": EnergyClippingResponse}, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("data")
async def energy_clipping_data_v2(
    request: Request,
    year: int = Query(2026, ge=2018, le=2030, description="Season; 2026 or later (earlier years return 404)"),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """JSON estimated energy-clipping analysis per driver. 2026+ power units only."""
    logger.info(f"Fetching energy clipping data (V2): Y{year} GP{gp} {session}")
    session = validate_session(session)
    _require_energy_clipping_year(year)
    try:
        result = await run_in_threadpool(EnergyClippingData(), year, gp, session)
        _track('energy-clipping', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get(
    '/corner-speed-profile-plot',
    tags=["API v2", "Telemetry"],
    summary="Corner speed profile plot",
    operation_id="v2_corner_speed_profile_plot",
    description=(
        "How fast every team takes the slow, medium and fast corners of the circuit (mean apex speed per "
        "class, gap to the class best). Any session.\n\n" + _CORNER_SPEED_DOC
    ),
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def corner_speed_profile_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    format: Optional[str] = Query(None, description=_FORMAT_DESC),
    api_key: str = Depends(verify_api_key)
):
    """Corner speed profile plot: team apex speeds by corner class. Any session."""
    logger.info(f"Generating corner speed profile plot (V2): Y{year} GP{gp} {session}")
    session = validate_session(session)
    try:
        output_path = await run_in_threadpool(CornerSpeedProfilePlot(), year, gp, session, format)
        _track('corner-speed-profile', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/corner-speed-profile-data',
    tags=["API v2", "Telemetry"],
    summary="Corner speed profile data",
    operation_id="v2_corner_speed_profile_data",
    description=(
        "Per corner class (slow / medium / fast): the corner numbers and every team's mean apex speed and gap "
        "to the class best, plus the classified corner list. Any session.\n\n" + _CORNER_SPEED_DOC
    ),
    responses={200: {"model": CornerSpeedProfileResponse}, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("data")
async def corner_speed_profile_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """JSON corner speed profile: mean apex speed per team and corner class. Any session."""
    logger.info(f"Fetching corner speed profile data (V2): Y{year} GP{gp} {session}")
    session = validate_session(session)
    try:
        result = await run_in_threadpool(CornerSpeedProfileData(), year, gp, session)
        _track('corner-speed-profile', year, gp, session)
        return result
    except T1APIError:
        raise


@router.get(
    '/efficiency-scatter-plot',
    tags=["API v2", "Telemetry"],
    summary="Efficiency scatter plot",
    operation_id="v2_efficiency_scatter_plot",
    description=(
        "Top speed against mean corner apex speed for every team's best lap: the drag-versus-downforce "
        "picture. Any session.\n\n" + _CORNER_SPEED_DOC
    ),
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def efficiency_scatter_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    format: Optional[str] = Query(None, description=_FORMAT_DESC),
    api_key: str = Depends(verify_api_key)
):
    """Efficiency scatter plot: top speed vs mean apex speed per team. Any session."""
    logger.info(f"Generating efficiency scatter plot (V2): Y{year} GP{gp} {session}")
    session = validate_session(session)
    try:
        output_path = await run_in_threadpool(EfficiencyScatterPlot(), year, gp, session, format)
        _track('efficiency-scatter', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/efficiency-scatter-data',
    tags=["API v2", "Telemetry"],
    summary="Efficiency scatter data",
    operation_id="v2_efficiency_scatter_data",
    description=(
        "Per team: top speed, mean apex speed (over the same classified corners as the corner speed profile) "
        "and lap time, plus the field median and highlights. Any session.\n\n" + _CORNER_SPEED_DOC
    ),
    responses={200: {"model": EfficiencyScatterResponse}, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("data")
async def efficiency_scatter_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    api_key: str = Depends(verify_api_key)
):
    """JSON efficiency scatter: top speed vs mean apex speed per team. Any session."""
    logger.info(f"Fetching efficiency scatter data (V2): Y{year} GP{gp} {session}")
    session = validate_session(session)
    try:
        result = await run_in_threadpool(EfficiencyScatterData(), year, gp, session)
        _track('efficiency-scatter', year, gp, session)
        return result
    except T1APIError:
        raise


_FIELD_DOMINANCE_DOC = """
Every driver's fastest clean lap is cut into 25 equal minisectors on the pole lap's racing line; the candidate
with the least time spent in a minisector owns it, and `margin_s` is how much the runner-up lost there.
`mode=team` (default) ranks each team's faster driver, `mode=driver` ranks every driver; `top_n` (2-10) keeps only
the fastest N candidates. An unknown `mode`, `format`, or a `top_n` outside 2-10 is rejected.
"""


@router.get(
    '/field-dominance-plot',
    tags=["API v2", "Telemetry"],
    summary="Field dominance map plot",
    operation_id="v2_field_dominance_plot",
    description="Track map coloured by who owns each part of the lap, with an owner tally. Any session.\n\n"
                + _FIELD_DOMINANCE_DOC,
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def field_dominance_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    mode: str = Query("team", description="team | driver"),
    top_n: Optional[int] = Query(None, ge=2, le=10, description="Keep only the fastest N candidates (2-10)"),
    format: Optional[str] = Query(None, description=_FORMAT_DESC),
    api_key: str = Depends(verify_api_key)
):
    """Field dominance map: who owns each minisector of the lap. Any session."""
    logger.info(f"Generating field dominance plot (V2): Y{year} GP{gp} {session} mode={mode} top_n={top_n}")
    session = validate_session(session)
    try:
        output_path = await run_in_threadpool(FieldDominancePlot(), year, gp, session, mode, top_n, format)
        _track('field-dominance', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/field-dominance-data',
    tags=["API v2", "Telemetry"],
    summary="Field dominance map data",
    operation_id="v2_field_dominance_data",
    description="Per-minisector owner and margin, owner tally, candidates and the pole lap's track line. "
                "Any session.\n\n" + _FIELD_DOMINANCE_DOC,
    responses={200: {"model": FieldDominanceResponse}, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("data")
async def field_dominance_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q'),
    mode: str = Query("team", description="team | driver"),
    top_n: Optional[int] = Query(None, ge=2, le=10, description="Keep only the fastest N candidates (2-10)"),
    api_key: str = Depends(verify_api_key)
):
    """JSON field dominance: minisector owners and margins. Any session."""
    logger.info(f"Fetching field dominance data (V2): Y{year} GP{gp} {session} mode={mode} top_n={top_n}")
    session = validate_session(session)
    try:
        result = await run_in_threadpool(FieldDominanceData(), year, gp, session, mode, top_n)
        _track('field-dominance', year, gp, session)
        return result
    except T1APIError:
        raise


_SECTOR_GAP_DOC = """
**Qualifying and Sprint Qualifying only** (`session=Q|SQ`; anything else is rejected with 404). For P2..P10 of
the classification, the gap to pole split into S1 / S2 / S3, all measured on each driver's own fastest lap so
the three segments add up to the lap-time gap. A negative segment means the driver beat pole in that sector.
Drivers whose lap cannot be matched to a sector triple are listed in `unmatched`.
"""


@router.get(
    '/sector-gap-plot',
    tags=["API v2", "Qualifying"],
    summary="Sector gap to pole plot",
    operation_id="v2_sector_gap_plot",
    description="Stacked S1 / S2 / S3 gap to pole for the top ten qualifiers.\n\n" + _SECTOR_GAP_DOC,
    responses={200: _PNG_RESPONSE, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("standard")
async def sector_gap_plot_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q', description="Q or SQ"),
    format: Optional[str] = Query(None, description=_FORMAT_DESC),
    api_key: str = Depends(verify_api_key)
):
    """Sector gap to pole plot. Qualifying / Sprint Qualifying only."""
    logger.info(f"Generating sector gap plot (V2): Y{year} GP{gp} {session}")
    session = validate_session(session)
    _require_sector_gap_session(session)
    try:
        output_path = await run_in_threadpool(SectorGapPlot(), year, gp, session, format)
        _track('sector-gap', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except T1APIError:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Plot not found")


@router.get(
    '/sector-gap-data',
    tags=["API v2", "Qualifying"],
    summary="Sector gap to pole data",
    operation_id="v2_sector_gap_data",
    description="Pole's sectors and, per driver P2..P10, lap gap and per-sector gaps to pole.\n\n" + _SECTOR_GAP_DOC,
    responses={200: {"model": SectorGapResponse}, **ANALYSIS_ERROR_RESPONSES},
)
@apply_tiered_limit("data")
async def sector_gap_data_v2(
    request: Request,
    year: int = Query(2025, ge=2018, le=2030),
    gp: Union[int, str] = Query(1, description="Round number, Event Key, or Official Name"),
    session: str = Query('Q', description="Q or SQ"),
    api_key: str = Depends(verify_api_key)
):
    """JSON sector gap to pole. Qualifying / Sprint Qualifying only."""
    logger.info(f"Fetching sector gap data (V2): Y{year} GP{gp} {session}")
    session = validate_session(session)
    _require_sector_gap_session(session)
    try:
        result = await run_in_threadpool(SectorGapData(), year, gp, session)
        _track('sector-gap', year, gp, session)
        return result
    except T1APIError:
        raise
