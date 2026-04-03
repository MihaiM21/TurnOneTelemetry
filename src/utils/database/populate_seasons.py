"""
Populate seasons_data collection with F1 season information
This script extracts data from teamColorPicker.py and stores it in MongoDB
"""

import traceback
from src.utils.database.seasons_manager import SeasonsDataManager
from src.utils.database.mongo import MongoDBManager
from src.data_loader.const_loader import get_season_events, get_season_drivers_and_teams

import json

def populate_2026_season():
    """Populate 2026 season data"""
    drivers_2026, teams_2026 = get_season_drivers_and_teams(2026)

    return drivers_2026, teams_2026

def populate_2025_season():
    """Populate 2025 season data"""
    drivers_2025, teams_2025 = get_season_drivers_and_teams(2025)

    return drivers_2025, teams_2025

def populate_2024_season():
    """Populate 2024 season data"""
    with open("lib/constants/drivers.json", "r") as f:
        drivers_data = json.load(f)
    with open("lib/constants/teams.json", "r") as f:
        teams_data = json.load(f)
    teams_2024 = teams_data["2024"]
    drivers_2024 = drivers_data["2024"]

    return drivers_2024, teams_2024


def main():
    """Main function to populate seasons data"""

    print("=" * 60)
    print("Populating Seasons Data Collection")
    print("=" * 60)

    # Initialize manager
    seasons_manager = SeasonsDataManager()

    # Populate 2026 season
    print("\n1. Populating 2026 Season Data...")
    drivers_2026, teams_2026 = populate_2026_season()
    success_2026 = seasons_manager.create_season_document(2026, drivers_2026, teams_2026, get_season_events(2026))

    if success_2026:
        print(f"   ✓ Added {len(drivers_2026)} drivers")
        print(f"   ✓ Added {len(teams_2026)} teams")
        print(f"   ✓ Added {len(get_season_events(2026))} races")

    # Populate 2025 season
    print("\n2. Populating 2025 Season Data...")
    drivers_2025, teams_2025 = populate_2025_season()
    success_2025 = seasons_manager.create_season_document(2025, drivers_2025, teams_2025, get_season_events(2025))

    if success_2025:
        print(f"   ✓ Added {len(drivers_2025)} drivers")
        print(f"   ✓ Added {len(teams_2025)} teams")
        print(f"   ✓ Added {len(get_season_events(2025))} races")

    # Populate 2024 season
    print("\n3. Populating 2024 Season Data...")
    drivers_2024, teams_2024 = populate_2024_season()
    success_2024 = seasons_manager.create_season_document(2024, drivers_2024, teams_2024)

    if success_2024:
        print(f"   ✓ Added {len(drivers_2024)} drivers")
        print(f"   ✓ Added {len(teams_2024)} teams")
        print(f"   ✓ No race data available for 2024")

    # Verify data
    print("\n4. Verifying Data...")
    available_seasons = seasons_manager.list_all_seasons()
    print(f"   Available seasons: {available_seasons}")

    # Test retrieval
    print("\n5. Testing Data Retrieval...")
    for year in available_seasons:
        drivers = seasons_manager.get_drivers(year)
        teams = seasons_manager.get_teams(year)
        print(f"   Year {year}: {len(drivers)} drivers, {len(teams)} teams")

        # Show sample driver
        if drivers:
            sample_driver = drivers[0]
            print(f"      Sample driver: {sample_driver['full_name']} ({sample_driver['code']}) - {sample_driver['team']}")

        # Show sample team
        if teams:
            sample_team = teams[0]
            print(f"      Sample team: {sample_team['name']} - Drivers: {', '.join(sample_team['drivers'])}")

    print("\n" + "=" * 60)
    print("Seasons Data Population Complete!")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n✗ Population failed with error: {e}")
        import traceback
        traceback.print_exc()

