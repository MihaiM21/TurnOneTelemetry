"""
Helper functions to integrate MongoDB storage with F1 telemetry plot generation
"""

from src.utils.database.mongo import MongoDBManager, convert_numpy_types
from typing import Dict, Any, Optional
import json

# Session type mapping from FastF1 to our standard format
SESSION_TYPE_MAP = {
    'Practice 1': 'FP1',
    'Practice 2': 'FP2',
    'Practice 3': 'FP3',
    'Qualifying': 'Q',
    'Sprint Qualifying': 'SQ',
    'Sprint': 'S',
    'Race': 'R',
    'FP1': 'FP1',
    'FP2': 'FP2',
    'FP3': 'FP3',
    'Q': 'Q',
    'SQ': 'SQ',
    'S': 'S',
    'R': 'R'
}

# Data type mapping for different plot types
DATA_TYPE_MAP = {
    'top_speed': 'top_speed',
    'throttle_comparison': 'throttle_comparison_drivers',
    'qualifying_results': 'qualifying_results',
    'lap_times_distribution': 'lap_times_distribution',
    'track_comparison': 'track_comparison_2drivers',
    'throttle_brake_comparison': 'throttle_brake_comparison_2drivers'
}


def get_gp_id_from_session(session: Any) -> str:
    """
    Generate GP ID from session object

    Args:
        session: FastF1 session object

    Returns:
        GP ID string (e.g., "2025_AUS")
    """
    year = session.event['EventDate'].year
    # Try to get country code from event name
    event_name = session.event['EventName']

    # Map event names to country codes
    country_codes = {
        'Australian': 'AUS',
        'Bahrain': 'BHR',
        'Chinese': 'CHN',
        'Japanese': 'JPN',
        'Saudi Arabian': 'SAU',
        'Miami': 'MIA',
        'Emilia Romagna': 'EMI',
        'Monaco': 'MON',
        'Spanish': 'ESP',
        'Canadian': 'CAN',
        'Austrian': 'AUT',
        'British': 'GBR',
        'Belgian': 'BEL',
        'Dutch': 'NED',
        'Italian': 'ITA',
        'Azerbaijan': 'AZE',
        'Singapore': 'SGP',
        'United States': 'USA',
        'Mexico': 'MEX',
        'Brazil': 'BRA',
        'Las Vegas': 'LVG',
        'Qatar': 'QAT',
        'Abu Dhabi': 'ABU'
    }

    # Find matching country code
    country_code = None
    for key, code in country_codes.items():
        if key.lower() in event_name.lower():
            country_code = code
            break

    if not country_code:
        # Fallback to first 3 letters of event name
        country_code = event_name.replace(' ', '')[:3].upper()

    return f"{year}_{country_code}"


def normalize_session_type(session_name: str) -> str:
    """
    Normalize session type to standard format

    Args:
        session_name: Session name from FastF1

    Returns:
        Standardized session type (FP1, FP2, Q, etc.)
    """
    return SESSION_TYPE_MAP.get(session_name, session_name)


def store_plot_data_to_mongo(session: Any, data_type: str, json_file_path: str,
                             db_manager: Optional[MongoDBManager] = None) -> bool:
    """
    Store plot data to MongoDB

    Args:
        session: FastF1 session object
        data_type: Type of plot data (from DATA_TYPE_MAP keys)
        json_file_path: Path to the JSON file with plot data
        db_manager: Optional existing MongoDBManager instance

    Returns:
        True if successful, False otherwise
    """
    should_close = False

    try:
        # Extract session information
        year = int(session.event['EventDate'].year)
        round_nr = int(session.event['RoundNumber'])
        gp_name = session.event['EventName'].replace(' ', '')
        gp_id = get_gp_id_from_session(session)
        session_type = normalize_session_type(session.name)

        # Create DB manager if not provided, passing the year
        if db_manager is None:
            db_manager = MongoDBManager(year=year)
            should_close = True

        # Get session date and time
        session_date = session.event['EventDate'].strftime('%Y-%m-%d')
        session_time = session.date.strftime('%H:%M:%SZ') if hasattr(session, 'date') and session.date else None

        # Read JSON data
        with open(json_file_path, 'r') as f:
            data = json.load(f)

        # Convert numpy types in the data
        data = convert_numpy_types(data)

        # Get or create GP document
        db_manager.get_or_create_gp(year, round_nr, gp_name, gp_id)

        # Map data_type to standard format
        standard_data_type = DATA_TYPE_MAP.get(data_type, data_type)

        # Add session data
        success = db_manager.add_session_data(
            gp_id=gp_id,
            session_type=session_type,
            data_type=standard_data_type,
            data=data,
            date=session_date,
            time=session_time,
            year=year
        )

        return success

    except Exception as e:
        print(f"✗ Error storing plot data to MongoDB: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        if should_close and db_manager:
            db_manager.close()


def store_data_dict_to_mongo(year: int, round_nr: int, session_name: str,
                             event_name: str, data_type: str, data: list,
                             session_date: Optional[str] = None,
                             session_time: Optional[str] = None,
                             db_manager: Optional[MongoDBManager] = None) -> bool:
    """
    Store data directly to MongoDB without a JSON file

    Args:
        year: Race year
        round_nr: Round number
        session_name: Session name (FP1, FP2, Q, etc.)
        event_name: Event name (e.g., "Australian Grand Prix")
        data_type: Type of data
        data: Data list to store
        session_date: Optional session date
        session_time: Optional session time
        db_manager: Optional existing MongoDBManager instance

    Returns:
        True if successful, False otherwise
    """
    should_close = False

    try:
        # Convert numpy types in the data
        data = convert_numpy_types(data)
        year = int(year)
        round_nr = int(round_nr)

        # Create DB manager if not provided, passing the year
        if db_manager is None:
            db_manager = MongoDBManager(year=year)
            should_close = True

        # Create GP ID
        gp_name = event_name.replace(' ', '').replace('Grand', '').replace('Prix', 'GP')
        gp_id = f"{year}_{gp_name[:3].upper()}"

        # Get or create GP document
        db_manager.get_or_create_gp(year, round_nr, gp_name, gp_id)

        # Normalize session type
        session_type = normalize_session_type(session_name)

        # Add session data
        success = db_manager.add_session_data(
            gp_id=gp_id,
            session_type=session_type,
            data_type=data_type,
            data=data,
            date=session_date,
            time=session_time,
            year=year
        )

        return success

    except Exception as e:
        print(f"✗ Error storing data to MongoDB: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        if should_close and db_manager:
            db_manager.close()


# Global MongoDB manager instance (optional, for reusing connections)
_global_db_manager = None


def get_global_db_manager(year: Optional[int] = None) -> MongoDBManager:
    """
    Get or create a global MongoDB manager instance

    Args:
        year: Optional year for the collection. If None, uses current year

    Returns:
        MongoDBManager instance
    """
    global _global_db_manager

    if _global_db_manager is None:
        _global_db_manager = MongoDBManager(year=year)
    elif year is not None and _global_db_manager.get_current_year() != year:
        # Switch to different year if requested
        _global_db_manager.set_year(year)

    return _global_db_manager


def close_global_db_manager():
    """Close the global MongoDB manager if it exists"""
    global _global_db_manager

    if _global_db_manager is not None:
        _global_db_manager.close()
        _global_db_manager = None


def get_plot_data_from_mongo(year: int, round_nr: int, event_name: str, data_type: str,
                             db_manager: Optional[MongoDBManager] = None) -> Optional[dict]:
    """
    Retrieve plot data from MongoDB if it exists (lightweight - no session loading required)

    Args:
        year: Race year
        round_nr: Round number
        event_name: Event name (e.g., 'FP1', 'FP2', 'Q', 'R')
        data_type: Type of plot data (from DATA_TYPE_MAP keys)
        db_manager: Optional existing MongoDBManager instance

    Returns:
        Dict with 'data' and 'metadata' keys if found, None otherwise
    """
    should_close = False

    try:
        # Normalize session type
        session_type = normalize_session_type(event_name)
        
        # Create DB manager if not provided
        if db_manager is None:
            db_manager = MongoDBManager(year=year)
            should_close = True

        # Map data_type to standard format
        standard_data_type = DATA_TYPE_MAP.get(data_type, data_type)
        
        # Build GP ID from event_name instead of using round_nr
        # This is more reliable since round_nr might not be unique or correctly stored
        # Get the GP info from FastF1 to construct proper gp_id
        import fastf1
        try:
            # Get event by round number to extract proper event name
            event = fastf1.get_event(year, round_nr)
            event_full_name = event['EventName']
            
            # Construct gp_id using same logic as get_gp_id_from_session
            country_codes = {
                'Australian': 'AUS',
                'Bahrain': 'BHR',
                'Chinese': 'CHN',
                'Japanese': 'JPN',
                'Saudi Arabian': 'SAU',
                'Miami': 'MIA',
                'Emilia Romagna': 'EMI',
                'Monaco': 'MON',
                'Spanish': 'ESP',
                'Canadian': 'CAN',
                'Austrian': 'AUT',
                'British': 'GBR',
                'Belgian': 'BEL',
                'Dutch': 'NED',
                'Italian': 'ITA',
                'Azerbaijan': 'AZE',
                'Singapore': 'SGP',
                'United States': 'USA',
                'Mexico': 'MEX',
                'Brazil': 'BRA',
                'Las Vegas': 'LVG',
                'Qatar': 'QAT',
                'Abu Dhabi': 'ABU'
            }
            
            country_code = None
            for key, code in country_codes.items():
                if key.lower() in event_full_name.lower():
                    country_code = code
                    break
            
            if not country_code:
                country_code = event_full_name.replace(' ', '')[:3].upper()
                
            gp_id = f"{year}_{country_code}"
            print(f"🔍 Searching MongoDB cache: year={year}, round={round_nr}, event={event_name} -> gp_id={gp_id}, session_type={session_type}, data_type={standard_data_type}")
        except Exception as e:
            print(f"✗ Could not get event info from FastF1: {e}")
            return None
        
        # Find GP document by gp_id (unique identifier)
        collection = db_manager._get_collection(year)
        gp_doc = collection.find_one({"year": year, "gp_id": gp_id})
        
        if not gp_doc:
            print(f"✗ No GP found for year={year}, gp_id={gp_id}")
            return None
            
        gp_name = gp_doc['name']
        print(f"✓ Found GP: {gp_id} ({gp_name})")

        # Try to get data from MongoDB
        data = db_manager.get_session_data(
            gp_id=gp_id,
            session_type=session_type,
            data_type=standard_data_type,
            year=year
        )

        if data:
            print(f"✓ Retrieved {data_type} from MongoDB cache for {gp_id} - {session_type}")
            # Return data with metadata so we don't need to load session
            return {
                'data': data,
                'metadata': {
                    'event_name': gp_name,
                    'session_name': event_name,
                    'year': year,
                    'round': round_nr,
                    'gp_id': gp_id
                }
            }
        else:
            print(f"✗ No data found in GP {gp_id} for session_type={session_type}, data_type={standard_data_type}")
            # Let's check what sessions exist
            sessions = gp_doc.get('sessions', [])
            print(f"📋 Available sessions in GP: {[s.get('session_type') for s in sessions]}")
            for session in sessions:
                if session.get('session_type') == session_type:
                    data_types = [d.get('data_type') for d in session.get('data', [])]
                    print(f"📋 Available data_types in {session_type}: {data_types}")
            return None

    except Exception as e:
        print(f"✗ Error retrieving plot data from MongoDB: {e}")
        return None

    finally:
        if should_close and db_manager:
            db_manager.close()
