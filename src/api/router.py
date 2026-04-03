from fastapi import APIRouter
from src.api.v1 import seasonal as seasonal_v1
from src.api.v1 import analysis as analysis_v1
from src.api import monitoring, admin
from src.api.v2 import seasonal as seasonal_v2
from src.api.v2 import analysis as analysis_v2

# Static data files for drivers and teams
from src.api.static import drivers_api
from src.api.static import teams_api

api_router = APIRouter()

# General monitoring, health and admin endpoints that are not version-specific
api_router.include_router(monitoring.router, prefix="/")
api_router.include_router(admin.router, prefix="/")

# Version 1 API endpoints using FastF1 data processing and analysis
api_router.include_router(seasonal_v1.router, prefix="/v1")
api_router.include_router(analysis_v1.router, prefix="/v1")

# Version 2 API endpoints using custom telemetry processing and analysis
api_router.include_router(seasonal_v2.router, prefix="/v2")
api_router.include_router(analysis_v2.router, prefix="/v2")

# Static endpoints for drivers and teams data
api_router.include_router(drivers_api.router, prefix="/static")
api_router.include_router(teams_api.router, prefix="/static")
