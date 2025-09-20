from fastapi import FastAPI, Query, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn


from src.scripts.simple.top_speed import TopSpeedPlot, TopSpeedData
from src.scripts.simple.throttle_comparison import ThrottleComp, ThrottleCompData
from src.scripts.quali_practice.qulifying_results import QualiResults, QualiResultsData
from src.scripts.quali_practice.track_comparison_2drivers import TrackComparisonPlot, TrackComparisonData
from src.scripts.quali_practice.throttleBrake_comparison_2drivers import throttle_graph, throttle_graph_data

from src.utils.session_tracker import SessionTracker

# Initialize session tracker
session_tracker = SessionTracker()
app = FastAPI(
    title='F1 Telemetry API',
    description='API for Formula 1 Telemetry Data Analysis',
    version='1.0',
    docs_url='/docs'
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get('/api/health')
def health_check():
    """Check if the API is running"""
    return {"status": "healthy"}

@app.get('/api/top-speed-plot')
def quali_top_speed_plot(
    year: int = Query(2025, description='Year of the race'),
    gp: int = Query(15, description='Number of the gp'),
    session: str = Query('Q', description='Session type (Q for qualifying)')
):
    """Get top speed comparison plot"""
    try:
        output_path = TopSpeedPlot(year, gp, session)
        # Track the session analysis
        session_tracker.track_session('top-speed', year, gp, session)
        return FileResponse(output_path, media_type='image/png')

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/api/top-speed-data')
def quali_top_speed_data(
    year: int = Query(2025, description='Year of the race'),
    gp: int = Query(15, description='Number of the gp'),
    session: str = Query('Q', description='Session type (Q for qualifying)')
):
    """Get top speed comparison data"""
    try:
        output_path = TopSpeedData(year, gp, session)
        # Track the session analysis
        session_tracker.track_session('top-speed', year, gp, session)
        return FileResponse(output_path, media_type='application/json')

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/api/throttle-comparison-plot')
def throttle_comparison_plot(
    year: int = Query(2025, description='Year of the race'),
    gp: int = Query(15, description='Number of the gp'),
    session: str = Query('Q', description='Session type (Q for qualifying)')
):
    """Get throttle comparison plot"""
    try:
        output_path = ThrottleComp(year, gp, session)
        # Track the session analysis
        session_tracker.track_session('throttle-comparison', year, gp, session)
        return FileResponse(output_path, media_type='image/png')

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/api/throttle-comparison-data')
def throttle_comparison_data(
    year: int = Query(2025, description='Year of the race'),
    gp: int = Query(15, description='Number of the gp'),
    session: str = Query('Q', description='Session type (Q for qualifying)')
):
    """Get throttle comparison data"""
    try:
        output_path = ThrottleCompData(year, gp, session)
        # Track the session analysis
        session_tracker.track_session('throttle-comparison', year, gp, session)
        return FileResponse(output_path, media_type='application/json')

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/api/qualifying-results-plot')
def qualifying_results_plot(
    year: int = Query(2025, description='Year of the race'),
    gp: int = Query(15, description='Number of the gp'),
    session: str = Query('Q', description='Session type (Q for qualifying)')
):
    """Get qualifying comparison plot"""
    try:
        output_path = QualiResults(year, gp, session)
        # Track the session analysis
        session_tracker.track_session('qualifying-results', year, gp, session)
        return FileResponse(output_path, media_type='image/png')

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/api/qualifying-results-data')
def qualifying_results_data(
    year: int = Query(2025, description='Year of the race'),
    gp: int = Query(15, description='Number of the gp'),
    session: str = Query('Q', description='Session type (Q for qualifying)')
):
    """Get throttle comparison data"""
    try:
        output_path = QualiResultsData(year, gp, session)
        # Track the session analysis
        session_tracker.track_session('qualifying-results', year, gp, session)
        return FileResponse(output_path, media_type='application/json')

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/api/track-comparison-2drivers-plot')
def track_comparison_2drivers_plot(
    year: int = Query(2025, description='Year of the race'),
    gp: int = Query(15, description='Number of the gp'),
    session: str = Query('Q', description='Session type (Q for qualifying)'),
    driver1: str = Query('VER', description='First driver code'),
    driver2: str = Query('HAM', description='Second driver code')
):
    try:
        output_path = TrackComparisonPlot(year, gp, session, driver1, driver2)
        # Track the session analysis with driver information
        session_tracker.track_session('track-comparison-2drivers', year, gp, session, driver1, driver2)
        return FileResponse(output_path, media_type='image/png')

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/api/track-comparison-2drivers-data')
def track_comparison_2drivers_data(
    year: int = Query(2025, description='Year of the race'),
    gp: int = Query(15, description='Number of the gp'),
    session: str = Query('Q', description='Session type (Q for qualifying)'),
    driver1: str = Query('VER', description='First driver code'),
    driver2: str = Query('HAM', description='Second driver code')
):
    try:
        output_path = TrackComparisonData(year, gp, session, driver1, driver2)
        # Track the session analysis with driver information
        session_tracker.track_session('track-comparison-2drivers', year, gp, session, driver1, driver2)
        return FileResponse(output_path, media_type='application/json')

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get('/api/throttleBrake-comparison-2drivers-plot')
def throttleBrakeComparison2DriversPlot(
    year: int = Query(2025, description='Year of the race'),
    gp: int = Query(15, description='Number of the gp'),
    session: str = Query('Q', description='Session type (Q for qualifying)'),
    driver1: str = Query('VER', description='First driver code'),
    driver2: str = Query('HAM', description='Second driver code')
):
    try:
        output_path = throttle_graph(year, gp, session, driver1, driver2)
        # Track the session analysis with driver information
        #session_tracker.track_session('track-comparison-2drivers', year, gp, session, driver1, driver2)
        return FileResponse(output_path, media_type='image/png')

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get('/api/throttleBrake-comparison-2drivers-data')
def throttleBrakeComparison2DriversData(
    year: int = Query(2025, description='Year of the race'),
    gp: int = Query(15, description='Number of the gp'),
    session: str = Query('Q', description='Session type (Q for qualifying)'),
    driver1: str = Query('VER', description='First driver code'),
    driver2: str = Query('HAM', description='Second driver code')
):
    try:
        output_path = throttle_graph_data(year, gp, session, driver1, driver2)
        # Track the session analysis with driver information
        session_tracker.track_session('track-comparison-2drivers', year, gp, session, driver1, driver2)
        return FileResponse(output_path, media_type='application/json')

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Analytics endpoints
@app.get('/api/analytics/daily')
def get_daily_analytics(
    date_str: str = Query(None, description='Date in YYYY-MM-DD format, defaults to today')
):
    """Get daily session analytics"""
    try:
        if date_str:
            from datetime import datetime
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            target_date = None

        stats = session_tracker.get_daily_stats(target_date)
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/api/analytics/total')
def get_total_analytics():
    """Get total session analytics"""
    try:
        stats = session_tracker.get_total_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=5000)
