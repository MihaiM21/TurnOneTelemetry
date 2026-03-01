from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool
import requests

from src.utils.logger import get_logger
from src.utils.auth import verify_api_key
from src.utils.rate_limiting import apply_tiered_limit

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v2")

from src.data_loader.f1_static_client import F1StaticClient

# ============================================================================
# SEASONAL DATA ENDPOINTS (V2)
# ============================================================================

@router.get('/seasons/{year}/events', tags=["API v2", "Seasonal Data"])
@apply_tiered_limit("standard")
async def get_season_events(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key)
):
    """
    Get a list of all available events (meetings) for a specific season.
    This fetches data directly from the formula1.com static API.
    """
    try:
        logger.info(f"Fetching events for season {year} via F1StaticClient")
        client = F1StaticClient()
        
        # Run synchronous network request in a threadpool to prevent blocking the async event loop
        season_index = await run_in_threadpool(client.fetch_season_index, year)
        
        meetings = season_index.get('Meetings', [])
        
        # Format the response nicely
        events = []
        for meeting in meetings:
            events.append({
                "name": meeting.get('Name'),
                "official_name": meeting.get('OfficialName'),
                "location": meeting.get('Location'),
                "country": meeting.get('Country', {}).get('Name'),
                "key": meeting.get('Key'),
                "code": meeting.get('Code')
            })
            
        return {"year": year, "events": events}
        
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.error(f"Season index not found for year {year}")
            raise HTTPException(status_code=404, detail=f"Season data for {year} not found on official F1 servers.")
        logger.error(f"HTTP Error fetching events for {year}: {e}")
        raise HTTPException(status_code=502, detail="Error communicating with F1 servers.")
    except Exception as e:
        logger.error(f"Error fetching season events: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch available events for the season.")


@router.get('/seasons/{year}/events/{event_name}/sessions', tags=["API v2", "Seasonal Data"])
@apply_tiered_limit("standard")
async def get_event_sessions(
    request: Request,
    year: int,
    event_name: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Get a list of all available sessions for a specific event in a season.
    event_name can be a broad match (e.g., "Italian Grand Prix").
    """
    try:
        logger.info(f"Fetching sessions for {event_name} ({year}) via F1StaticClient")
        client = F1StaticClient()
        
        # We need the full season index to find the event and its sessions
        season_index = await run_in_threadpool(client.fetch_season_index, year)
        
        # Find the meeting
        meetings = season_index.get('Meetings', [])
        event_data = None
        for meeting in meetings:
            if event_name.lower() in meeting.get('Name', '').lower() or event_name.lower() == str(meeting.get('Key')):
                event_data = meeting
                break
                
        if not event_data:
            raise HTTPException(status_code=404, detail=f"Event matching '{event_name}' not found in the {year} season.")
            
        sessions_data = event_data.get('Sessions', [])
        
        sessions = []
        for session in sessions_data:
            sessions.append({
                "name": session.get('Name'),
                "type": session.get('Type'),
                "number": session.get('Number'),
                "start_date": session.get('StartDate'),
                "end_date": session.get('EndDate'),
                "path": session.get('Path'),
                "key": session.get('Key')
            })
            
        return {
            "year": year,
            "event_name": event_data.get('Name'),
            "event_key": event_data.get('Key'),
            "sessions": sessions
        }
        
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.error(f"Season index not found for year {year}")
            raise HTTPException(status_code=404, detail=f"Season data for {year} not found on official F1 servers.")
        logger.error(f"HTTP Error fetching sessions for {event_name}: {e}")
        raise HTTPException(status_code=502, detail="Error communicating with F1 servers.")
    except Exception as e:
        logger.error(f"Error fetching event sessions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch available sessions for the event.")
