from fastapi import FastAPI, Query, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn

from src.scripts.simple.top_speed import TopSpeedPlot, TopSpeedData
from src.scripts.simple.throttle_comparison import ThrottleComp, ThrottleCompData


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
        return FileResponse(output_path, media_type='application/json')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=5000)
