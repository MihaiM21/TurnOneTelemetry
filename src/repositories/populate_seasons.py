"""
Populate seasons_data collection with F1 season information
This script extracts data from teamColorPicker.py and stores it in MongoDB
"""

import traceback
from src.repositories.seasons_manager import SeasonsDataManager
from src.repositories.mongo import MongoDBManager
from src.ingestion.reference import get_season_events, get_season_drivers_and_teams
from src.core.logging import get_logger

import json

logger = get_logger(__name__)

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
    with open("src/domain/data/drivers.json", "r") as f:
        drivers_data = json.load(f)
    with open("src/domain/data/teams.json", "r") as f:
        teams_data = json.load(f)
    teams_2024 = teams_data["2024"]
    drivers_2024 = drivers_data["2024"]

    return drivers_2024, teams_2024


def main():
    """Main function to populate seasons data"""

    logger.info("Populating Seasons Data Collection")

    # Initialize manager
    seasons_manager = SeasonsDataManager()

    # Populate 2026 season
    logger.info("Populating 2026 Season Data...")
    drivers_2026, teams_2026 = populate_2026_season()
    success_2026 = seasons_manager.create_season_document(2026, drivers_2026, teams_2026,
                                                           get_season_events(2026))

    if success_2026:
        logger.info("Added %s drivers, %s teams, %s races for 2026", len(drivers_2026),
                    len(teams_2026), len(get_season_events(2026)))

    # Populate 2025 season
    logger.info("Populating 2025 Season Data...")
    drivers_2025, teams_2025 = populate_2025_season()
    success_2025 = seasons_manager.create_season_document(2025, drivers_2025, teams_2025,
                                                           get_season_events(2025))

    if success_2025:
        logger.info("Added %s drivers, %s teams, %s races for 2025", len(drivers_2025),
                    len(teams_2025), len(get_season_events(2025)))

    # Populate 2024 season
    logger.info("Populating 2024 Season Data...")
    drivers_2024, teams_2024 = populate_2024_season()
    success_2024 = seasons_manager.create_season_document(2024, drivers_2024, teams_2024)

    if success_2024:
        logger.info("Added %s drivers, %s teams for 2024 (no race data available)", len(drivers_2024),
                    len(teams_2024))

    # Verify data
    logger.info("Verifying Data...")
    available_seasons = seasons_manager.list_all_seasons()
    logger.info("Available seasons: %s", available_seasons)

    # Test retrieval
    logger.info("Testing Data Retrieval...")
    for year in available_seasons:
        drivers = seasons_manager.get_drivers(year)
        teams = seasons_manager.get_teams(year)
        logger.info("Year %s: %s drivers, %s teams", year, len(drivers), len(teams))

        # Show sample driver
        if drivers:
            sample_driver = drivers[0]
            logger.info("Sample driver: %s (%s) - %s", sample_driver['full_name'],
                        sample_driver['code'], sample_driver['team'])

        # Show sample team
        if teams:
            sample_team = teams[0]
            logger.info("Sample team: %s - Drivers: %s", sample_team['name'],
                        ', '.join(sample_team['drivers']))

    logger.info("Seasons Data Population Complete!")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Population failed with error: %s", e)

