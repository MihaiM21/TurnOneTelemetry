from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.concurrency import run_in_threadpool
import fastf1
import pandas as pd

from src.utils.logger import get_logger
from src.utils.auth import verify_api_key
from src.utils.rate_limiting import apply_tiered_limit
from src.data_loader.f1_static_client import F1StaticClient
from src.data_loader.const_loader import get_season_events

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v2")


# ============================================================================
# SEASONAL DATA ENDPOINTS (V2)
# ============================================================================

@router.get('/seasons/{year}/events', tags=["API v2", "Seasonal Data"])
@apply_tiered_limit("standard")
async def get_season_events_endpoint(
    request: Request,
    year: int,
    api_key: str = Depends(verify_api_key)
):
    """
    Get a list of all available events (meetings) for a specific season.
    Tries multiple sources in order: F1StaticClient → FastF1 → Local Constants
    """
    # Try F1StaticClient first
    try:
        logger.info(f"Attempting to fetch events for season {year} via F1StaticClient")
        client = F1StaticClient()
        season_index = await run_in_threadpool(client.fetch_season_index, year)
        meetings = season_index.get('Meetings', [])
        
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
        
        logger.info(f"Successfully fetched {len(events)} events from F1StaticClient for {year}")
        return {"year": year, "events": events, "source": "F1StaticClient"}
        
    except Exception as e:
        logger.warning(f"F1StaticClient failed for {year}: {e}. Trying FastF1...")
    
    # Fallback to FastF1
    try:
        logger.info(f"Attempting to fetch events for season {year} via FastF1")
        schedule = await run_in_threadpool(fastf1.get_event_schedule, year)
        
        events = []
        for idx, row in schedule.iterrows():
            events.append({
                "name": row.get('EventName', ''),
                "official_name": row.get('EventName', ''),
                "location": row.get('Circuit', ''),
                "country": row.get('Country', ''),
                "key": str(idx + 1),
                "code": row.get('EventCode', '')
            })
        
        logger.info(f"Successfully fetched {len(events)} events from FastF1 for {year}")
        return {"year": year, "events": events, "source": "FastF1"}
        
    except Exception as e:
        logger.warning(f"FastF1 failed for {year}: {e}. Trying local constants...")
    
    # Fallback to local constants
    try:
        logger.info(f"Attempting to fetch events for season {year} via local constants")
        races_data = await run_in_threadpool(get_season_events, year)
        
        events = []
        for race in races_data:
            events.append({
                "name": race.get('grandPrix', ''),
                "official_name": race.get('grandPrix', ''),
                "location": race.get('circuit', ''),
                "country": race.get('country', ''),
                "key": race.get('grandPrix', ''),
                "code": "",
                "hasSprint": race.get('hasSprint', False)
            })
        
        logger.info(f"Successfully fetched {len(events)} events from local constants for {year}")
        return {"year": year, "events": events, "source": "local_constants"}
        
    except Exception as e:
        logger.error(f"All fallback sources failed for season {year}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to fetch available events for season {year} from all sources."
        )


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
    Tries multiple sources in order: F1StaticClient → FastF1 → Local Constants
    event_name can be a broad match (e.g., "Italian Grand Prix").
    """
    # Try F1StaticClient first
    try:
        logger.info(f"Attempting to fetch sessions for {event_name} ({year}) via F1StaticClient")
        client = F1StaticClient()
        season_index = await run_in_threadpool(client.fetch_season_index, year)
        
        # Find the meeting
        meetings = season_index.get('Meetings', [])
        event_data = None
        for meeting in meetings:
            if event_name.lower() in meeting.get('Name', '').lower() or event_name.lower() == str(meeting.get('Key')):
                event_data = meeting
                break
                
        if not event_data:
            raise ValueError(f"Event matching '{event_name}' not found in F1StaticClient data for {year}")
            
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
            
        logger.info(f"Successfully fetched {len(sessions)} sessions from F1StaticClient for {event_name} ({year})")
        return {
            "year": year,
            "event_name": event_data.get('Name'),
            "event_key": event_data.get('Key'),
            "sessions": sessions,
            "source": "F1StaticClient"
        }
        
    except Exception as e:
        logger.warning(f"F1StaticClient failed for {event_name} ({year}): {e}. Trying FastF1...")
    
    # Fallback to FastF1
    try:
        logger.info(f"Attempting to fetch sessions for {event_name} ({year}) via FastF1")
        schedule = await run_in_threadpool(fastf1.get_event_schedule, year)
        
        # Find matching event
        event_data = None
        round_num = None
        for idx, row in schedule.iterrows():
            if event_name.lower() in row.get('EventName', '').lower():
                event_data = row
                round_num = idx + 1
                break
        
        if event_data is None:
            raise ValueError(f"Event matching '{event_name}' not found in FastF1 data for {year}")
        
        # Get session dates from the row
        sessions = []
        session_map = {
            'Session1Date': 'Free Practice 1',
            'Session2Date': 'Free Practice 2',
            'Session3Date': 'Free Practice 3',
            'Session4Date': 'Qualifying',
            'Session5Date': 'Race'
        }
        
        for date_col, session_name in session_map.items():
            if pd.notna(event_data.get(date_col)):
                sessions.append({
                    "name": session_name,
                    "type": session_name.split()[0] if ' ' in session_name else session_name,
                    "number": len(sessions) + 1,
                    "start_date": str(event_data.get(date_col)),
                    "end_date": str(event_data.get(date_col)),
                    "path": "",
                    "key": session_name.lower().replace(' ', '_')
                })
        
        logger.info(f"Successfully fetched {len(sessions)} sessions from FastF1 for {event_name} ({year})")
        return {
            "year": year,
            "event_name": event_data.get('EventName', event_name),
            "event_key": str(round_num),
            "sessions": sessions,
            "source": "FastF1"
        }
        
    except Exception as e:
        logger.warning(f"FastF1 failed for {event_name} ({year}): {e}. Trying local constants...")
    
    # Fallback to local constants
    try:
        logger.info(f"Attempting to fetch sessions for {event_name} ({year}) via local constants")
        races_data = await run_in_threadpool(get_season_events, year)
        
        # Find matching race
        event_data = None
        for race in races_data:
            if event_name.lower() in race.get('grandPrix', '').lower():
                event_data = race
                break
        
        if not event_data:
            raise ValueError(f"Event matching '{event_name}' not found in local constants for {year}")
        
        sessions = []
        for idx, session in enumerate(event_data.get('sessions', []), 1):
            sessions.append({
                "name": session.get('name', ''),
                "type": session.get('name', '').split()[0] if ' ' in session.get('name', '') else session.get('name', ''),
                "number": idx,
                "start_date": session.get('startTime').isoformat() if session.get('startTime') else '',
                "end_date": session.get('endTime').isoformat() if session.get('endTime') else '',
                "path": "",
                "key": session.get('name', '').lower().replace(' ', '_')
            })
        
        logger.info(f"Successfully fetched {len(sessions)} sessions from local constants for {event_name} ({year})")
        return {
            "year": year,
            "event_name": event_data.get('grandPrix', event_name),
            "event_key": event_data.get('grandPrix', ''),
            "sessions": sessions,
            "source": "local_constants"
        }
        
    except Exception as e:
        logger.error(f"All fallback sources failed for {event_name} ({year}): {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch sessions for event '{event_name}' in {year} from all sources."
        )
