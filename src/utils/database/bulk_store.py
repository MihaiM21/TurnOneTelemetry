"""
Bulk data storage utility for MongoDB
This script can store all existing JSON data files to MongoDB
"""

import os
import json
from pathlib import Path
from typing import Optional
from src.utils.database.mongo import MongoDBManager
from src.utils.database.mongo_helper import normalize_session_type

# Map output folder structure to data types
DATA_TYPE_MAPPING = {
    'Top speed comparison': 'top_speed',
    'Throttle comparison': 'throttle_comparison_drivers',
    'results': 'qualifying_results',
    'Lap times distribution': 'lap_times_distribution',
    'Track comparison': 'track_comparison_2drivers',
    'Throttle and Brake comparison': 'throttle_brake_comparison_2drivers'
}

# Country code mapping
COUNTRY_CODES = {
    'Australian': 'AUS', 'Bahrain': 'BHR', 'Chinese': 'CHN', 'Japanese': 'JPN',
    'Saudi Arabian': 'SAU', 'Miami': 'MIA', 'Emilia Romagna': 'EMI', 'Monaco': 'MON',
    'Spanish': 'ESP', 'Canadian': 'CAN', 'Austrian': 'AUT', 'British': 'GBR',
    'Belgian': 'BEL', 'Dutch': 'NED', 'Italian': 'ITA', 'Azerbaijan': 'AZE',
    'Singapore': 'SGP', 'United States': 'USA', 'Mexico': 'MEX', 'Brazil': 'BRA',
    'Las Vegas': 'LVG', 'Qatar': 'QAT', 'Abu Dhabi': 'ABU'
}


def get_country_code(gp_name: str) -> str:
    """Extract country code from GP name"""
    for key, code in COUNTRY_CODES.items():
        if key.lower() in gp_name.lower():
            return code
    # Fallback
    return gp_name.replace(' ', '').replace('Grand', '').replace('Prix', '')[:3].upper()


def infer_data_type_from_filename(filename: str) -> Optional[str]:
    """Infer data type from filename"""
    for key, value in DATA_TYPE_MAPPING.items():
        if key.lower() in filename.lower():
            return value
    return None


def parse_session_name(session_str: str) -> str:
    """Parse session name from file path"""
    session_str = session_str.lower()

    if 'practice 1' in session_str or 'practice_1' in session_str or 'FP1' in session_str:
        return 'FP1'
    elif 'practice 2' in session_str or 'practice_2' in session_str or 'FP2' in session_str:
        return 'FP2'
    elif 'practice 3' in session_str or 'practice_3' in session_str or 'FP3' in session_str:
        return 'FP3'
    elif 'qualifying' in session_str or 'Q' in session_str:
        return 'Q'
    elif 'sprintqualifying' in session_str or 'SQ' in session_str:
        return 'SQ'
    elif 'sprint' in session_str or 'S' not in session_str:
        return 'S'
    elif 'race' in session_str or 'R' in session_str:
        return 'R'

    return None


def bulk_store_plots(base_path: str = "outputs/plots", year: Optional[int] = None):
    """
    Bulk store all JSON plot data to MongoDB

    Args:
        base_path: Base path to plots directory
        year: Optional filter by year
    """
    db_manager = MongoDBManager()

    try:
        base_path = Path(base_path)

        # Get all years
        years = [year] if year else [int(y.name) for y in base_path.iterdir() if y.is_dir() and y.name.isdigit()]

        total_stored = 0
        total_failed = 0

        for yr in years:
            year_path = base_path / str(yr)
            if not year_path.exists():
                continue

            print(f"\n{'='*60}")
            print(f"Processing year: {yr}")
            print(f"{'='*60}")

            # Iterate through GP folders
            gp_folders = [f for f in year_path.iterdir() if f.is_dir()]

            for gp_idx, gp_folder in enumerate(gp_folders, 1):
                gp_name = gp_folder.name
                country_code = get_country_code(gp_name)
                gp_id = f"{yr}_{country_code}"

                print(f"\n[{gp_idx}/{len(gp_folders)}] Processing: {gp_name}")

                # Get or create GP document (round number will be approximate)
                db_manager.get_or_create_gp(yr, gp_idx, gp_name.replace(' ', ''), gp_id)

                # Iterate through session folders
                session_folders = [f for f in gp_folder.iterdir() if f.is_dir()]

                for session_folder in session_folders:
                    session_name = parse_session_name(session_folder.name)
                    if not session_name:
                        print(f"  ⚠ Skipping unknown session: {session_folder.name}")
                        continue

                    # Find all JSON files in this session
                    json_files = list(session_folder.glob('*.json'))

                    for json_file in json_files:
                        data_type = infer_data_type_from_filename(json_file.name)

                        if not data_type:
                            print(f"  ⚠ Unknown data type for: {json_file.name}")
                            continue

                        try:
                            # Read JSON data
                            with open(json_file, 'r') as f:
                                data = json.load(f)

                            # Store to MongoDB
                            success = db_manager.add_session_data(
                                gp_id=gp_id,
                                session_type=session_name,
                                data_type=data_type,
                                data=data
                            )

                            if success:
                                total_stored += 1
                                print(f"  ✓ Stored: {session_name} - {data_type}")
                            else:
                                total_failed += 1
                                print(f"  ✗ Failed: {session_name} - {data_type}")

                        except Exception as e:
                            total_failed += 1
                            print(f"  ✗ Error processing {json_file.name}: {e}")

        print(f"\n{'='*60}")
        print(f"SUMMARY")
        print(f"{'='*60}")
        print(f"Total files stored: {total_stored}")
        print(f"Total files failed: {total_failed}")
        print(f"{'='*60}\n")

    finally:
        db_manager.close()


def store_custom_data(year: int, round_nr: int, gp_name: str, session_type: str,
                     data_type: str, data: list):
    """
    Store custom data directly to MongoDB

    Args:
        year: Race year
        round_nr: Round number
        gp_name: GP name (e.g., "Australian Grand Prix")
        session_type: Session type (FP1, FP2, Q, etc.)
        data_type: Custom data type identifier
        data: List of data dictionaries to store

    Example:
        store_custom_data(
            year=2025,
            round_nr=1,
            gp_name="Australian Grand Prix",
            session_type="FP1",
            data_type="custom_analysis",
            data=[
                {"metric": "value1", "data": 123},
                {"metric": "value2", "data": 456}
            ]
        )
    """
    db_manager = MongoDBManager()

    try:
        country_code = get_country_code(gp_name)
        gp_id = f"{year}_{country_code}"
        clean_gp_name = gp_name.replace(' ', '').replace('Grand', '').replace('Prix', 'GP')

        # Get or create GP document
        db_manager.get_or_create_gp(year, round_nr, clean_gp_name, gp_id)

        # Store data
        success = db_manager.add_session_data(
            gp_id=gp_id,
            session_type=session_type,
            data_type=data_type,
            data=data
        )

        if success:
            print(f"✓ Successfully stored {data_type} for {gp_name} - {session_type}")
        else:
            print(f"✗ Failed to store {data_type}")

        return success

    finally:
        db_manager.close()


def retrieve_data(gp_id: str, session_type: str, data_type: Optional[str] = None):
    """
    Retrieve data from MongoDB

    Args:
        gp_id: GP identifier (e.g., "2025_AUS")
        session_type: Session type (FP1, FP2, Q, etc.)
        data_type: Optional specific data type to retrieve

    Returns:
        Retrieved data or None
    """
    db_manager = MongoDBManager()

    try:
        data = db_manager.get_session_data(gp_id, session_type, data_type)
        return data
    finally:
        db_manager.close()


def list_all_data(year: Optional[int] = None):
    """
    List all GPs in the database

    Args:
        year: Optional filter by year

    Returns:
        List of GP documents
    """
    db_manager = MongoDBManager()

    try:
        gps = db_manager.list_all_gps(year)
        return gps
    finally:
        db_manager.close()


if __name__ == "__main__":
    import sys

    print("MongoDB Bulk Storage Utility")
    print("="*60)

    if len(sys.argv) > 1:
        if sys.argv[1] == "store":
            # Store data
            year = int(sys.argv[2]) if len(sys.argv) > 2 else None
            bulk_store_plots(year=year)
        elif sys.argv[1] == "list":
            # List all data
            year = int(sys.argv[2]) if len(sys.argv) > 2 else None
            gps = list_all_data(year)
            print(f"\nFound {len(gps)} Grand Prix events:")
            for gp in gps:
                sessions_count = len(gp.get('sessions', []))
                print(f"  - {gp['name']} ({gp['year']}) - {sessions_count} sessions")
    else:
        print("\nUsage:")
        print("  python -m src.utils.database.bulk_store store [year]  - Store all JSON files")
        print("  python -m src.utils.database.bulk_store list [year]   - List all stored data")
        print("\nOr import and use the functions directly:")
        print("  from src.utils.database.bulk_store import bulk_store_plots, store_custom_data")

