from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn
from dotenv import load_dotenv
import os

# Import your custom modules
from src.scripts.simple.top_speed import TopSpeedPlot, TopSpeedData
from src.scripts.simple.throttle_comparison import ThrottleComp, ThrottleCompData
from src.scripts.quali_practice.qulifying_results import QualiResults, QualiResultsData
from src.scripts.quali_practice.track_comparison_2drivers import TrackComparisonPlot, TrackComparisonData
from src.scripts.quali_practice.throttleBrake_comparison_2drivers import throttle_graph, throttle_graph_data
from src.scripts.simple.latimes_distribution import LatimesDistribution
from src.utils.session_tracker import SessionTracker
from src.utils.latest_session import get_latest_finished_session
from src.scripts.complex.latest_session_analised import latest_session_analised

load_dotenv()

DOCKER_EXPOSED_PORT = int(os.getenv('DOCKER_EXPOSED_PORT', 8000))

# Initialize session tracker
session_tracker = SessionTracker()

# --- 1. Define Metadata & Tags ---
description = """
# 🏎️ F1 Telemetry Analysis API

This API provides advanced telemetry analysis for Formula 1 sessions. 
It powers the dashboards at **t1f1.com** and **turnonehub.com**.

## Features
* **Daily Data**: High-level daily summary plots.
* **Telemetry Comparison**: Throttle, brake, and speed comparisons between drivers.
* **Qualifying Analysis**: Lap time distributions and top speed charts.
* **Dashboards**: Aggregated data for specific race sessions.

## Usage
Select a generic endpoint or a driver-specific endpoint to generate plots (PNG) or raw analysis data (JSON).
"""

tags_metadata = [
    {
        "name": "General",
        "description": "System health, welcome messages, and daily summaries.",
    },
    {
        "name": "Latest Session",
        "description": "Aggregated data endpoints for the main frontend dashboard.",
    },
    {
        "name": "Single Driver Analysis",
        "description": "Analysis focused on general session stats or single driver metrics.",
    },
    {
        "name": "Driver Comparison",
        "description": "Head-to-head comparisons (Verstappen vs Hamilton, etc.).",
    },
]

# --- 2. Initialize App with Professional Metadata ---
app = FastAPI(
    title='F1 Telemetry API',
    description=description,
    version='1.0.0',
    contact={
        "name": "Turn One Hub Support",
        "url": "https://turnonehub.com",
        "email": "support@turnonehub.com",
    },
    license_info={
        "name": "Proprietary / Internal Use",
    },
    openapi_tags=tags_metadata,
    docs_url='/docs',
    redoc_url='/redoc'
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- 3. Endpoints with Tags and Summaries ---

@app.get('/', tags=["General"])
def welcome():
    # Fixed: Changed from set {} to dict {} for valid JSON response
    return {
        "message": "Welcome to the F1 Telemetry API. Thank you for using it!",
        "info": "Go to t1f1.com or turnonehub.com for more info."
    }


@app.get('/api/health', tags=["General"])
def health_check():
    """Check if the API is running and responsive."""
    return {"status": "healthy"}


@app.get('/api/daily-data', tags=["General"])
def daily_data():
    """Generate the daily data summary plot."""
    try:
        from src.utils.daily_plot_data import DailyPlotData
        output = DailyPlotData().generate_daily_plot()
        return output
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/dashboard', tags=["Latest Session"])
def get_dashboard_data():
    """
    **Get Main Latest Session Data**

    Automatically detects the most recent completed session and returns appropriate data packages:
    - **Practice:** Top Speed, Throttle Comparison
    - **Qualifying:** Top Speed, Throttle Comparison, Fastest Laps Overall
    - **Race:** Top Speed, Throttle Comparison
    """
    try:
        latest_session = get_latest_finished_session()
        if not latest_session:
            raise HTTPException(status_code=404, detail="No finished sessions found.")


        return latest_session_analised(latest_session)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Single Driver / General Session Analysis ---

@app.get('/api/top-speed-plot', tags=["Single Driver Analysis"])
def quali_top_speed_plot(
        year: int = Query(2025, description='Year of the race'),
        gp: int = Query(15, description='Number of the gp'),
        session: str = Query('Q', description='Session type (Q for qualifying)')
):
    """Generate a PNG plot of top speeds across the grid."""
    try:
        output_path = TopSpeedPlot(year, gp, session)
        session_tracker.track_session('top-speed', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/top-speed-data', tags=["Single Driver Analysis"])
def quali_top_speed_data(
        year: int = Query(2025, description='Year of the race'),
        gp: int = Query(15, description='Number of the gp'),
        session: str = Query('Q', description='Session type (Q for qualifying)')
):
    """Get raw JSON data for top speed analysis."""
    try:
        output_path = TopSpeedData(year, gp, session)
        session_tracker.track_session('top-speed', year, gp, session)
        return FileResponse(output_path, media_type='application/json')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/throttle-comparison-plot', tags=["Single Driver Analysis"])
def throttle_comparison_plot(
        year: int = Query(2025, description='Year of the race'),
        gp: int = Query(15, description='Number of the gp'),
        session: str = Query('Q', description='Session type (Q for qualifying)')
):
    """Generate a PNG plot comparing throttle application."""
    try:
        output_path = ThrottleComp(year, gp, session)
        session_tracker.track_session('throttle-comparison', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/throttle-comparison-data', tags=["Single Driver Analysis"])
def throttle_comparison_data(
        year: int = Query(2025, description='Year of the race'),
        gp: int = Query(15, description='Number of the gp'),
        session: str = Query('Q', description='Session type (Q for qualifying)')
):
    """Get raw JSON data for throttle comparison."""
    try:
        output_path = ThrottleCompData(year, gp, session)
        session_tracker.track_session('throttle-comparison', year, gp, session)
        return FileResponse(output_path, media_type='application/json')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/qualifying-results-plot', tags=["Single Driver Analysis"])
def qualifying_results_plot(
        year: int = Query(2025, description='Year of the race'),
        gp: int = Query(15, description='Number of the gp'),
        session: str = Query('Q', description='Session type (Q for qualifying)')
):
    """Generate a PNG plot of qualifying results."""
    try:
        output_path = QualiResults(year, gp, session)
        session_tracker.track_session('qualifying-results', year, gp, session)
        return FileResponse(output_path, media_type='image/png')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/qualifying-results-data', tags=["Single Driver Analysis"])
def qualifying_results_data(
        year: int = Query(2025, description='Year of the race'),
        gp: int = Query(15, description='Number of the gp'),
        session: str = Query('Q', description='Session type (Q for qualifying)')
):
    """Get raw JSON data for qualifying results."""
    try:
        output_path = QualiResultsData(year, gp, session)
        session_tracker.track_session('qualifying-results', year, gp, session)
        return FileResponse(output_path, media_type='application/json')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/laptimes', tags=["Single Driver Analysis"])
def get_laptimes(
        year: int = Query(2025, description='Year of the race'),
        gp: int = Query(15, description='Number of the gp'),
        session: str = Query('Q', description='Session type (Q for qualifying)'),
        driver: str = Query('VER', description='Driver code')
):
    """Get laptime distribution data for a specific driver."""
    try:
        output_path = LatimesDistribution(year, gp, session, driver)
        session_tracker.track_session('laptimes', year, gp, session, driver)
        return FileResponse(output_path, media_type='application/json')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Driver Comparison (Head-to-Head) ---

@app.get('/api/track-comparison-2drivers-plot', tags=["Driver Comparison"])
def track_comparison_2drivers_plot(
        year: int = Query(2025, description='Year of the race'),
        gp: int = Query(15, description='Number of the gp'),
        session: str = Query('Q', description='Session type'),
        driver1: str = Query('VER', description='First driver code'),
        driver2: str = Query('HAM', description='Second driver code')
):
    """Generate a track map comparing two drivers."""
    try:
        output_path = TrackComparisonPlot(year, gp, session, driver1, driver2)
        session_tracker.track_session('track-comparison-2drivers', year, gp, session, driver1, driver2)
        return FileResponse(output_path, media_type='image/png')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/track-comparison-2drivers-data', tags=["Driver Comparison"])
def track_comparison_2drivers_data(
        year: int = Query(2025, description='Year of the race'),
        gp: int = Query(15, description='Number of the gp'),
        session: str = Query('Q', description='Session type'),
        driver1: str = Query('VER', description='First driver code'),
        driver2: str = Query('HAM', description='Second driver code')
):
    """Get raw data for 2-driver track comparison."""
    try:
        output_path = TrackComparisonData(year, gp, session, driver1, driver2)
        session_tracker.track_session('track-comparison-2drivers', year, gp, session, driver1, driver2)
        return FileResponse(output_path, media_type='application/json')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/throttleBrake-comparison-2drivers-plot', tags=["Driver Comparison"])
def throttle_brake_comparison_2drivers_plot(
        year: int = Query(2025, description='Year of the race'),
        gp: int = Query(15, description='Number of the gp'),
        session: str = Query('Q', description='Session type'),
        driver1: str = Query('VER', description='First driver code'),
        driver2: str = Query('HAM', description='Second driver code')
):
    """Generate throttle/brake telemetry graph for 2 drivers."""
    try:
        output_path = throttle_graph(year, gp, session, driver1, driver2)
        return FileResponse(output_path, media_type='image/png')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/throttleBrake-comparison-2drivers-data', tags=["Driver Comparison"])
def throttle_brake_comparison_2drivers_data(
        year: int = Query(2025, description='Year of the race'),
        gp: int = Query(15, description='Number of the gp'),
        session: str = Query('Q', description='Session type'),
        driver1: str = Query('VER', description='First driver code'),
        driver2: str = Query('HAM', description='Second driver code')
):
    """Get raw data for 2-driver throttle/brake comparison."""
    try:
        output_path = throttle_graph_data(year, gp, session, driver1, driver2)
        session_tracker.track_session('track-comparison-2drivers', year, gp, session, driver1, driver2)
        return FileResponse(output_path, media_type='application/json')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=DOCKER_EXPOSED_PORT)