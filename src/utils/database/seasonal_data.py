"""
Seasonal Data API Functions
Provides functions to retrieve season-specific data from MongoDB
Used by API endpoints to fetch drivers, teams, and other seasonal information
"""

from typing import List, Dict, Optional
from src.utils.database.seasons_manager import SeasonsDataManager
from src.utils.logger import get_logger

logger = get_logger(__name__)


def get_drivers_for_season(year: int) -> List[Dict]:
    """
    Get all drivers for a specific season from MongoDB
    
    Args:
        year: Season year (e.g., 2025)
    
    Returns:
        List of driver dictionaries with structure:
        [
            {
                "code": "VER",
                "name": "Verstappen",
                "full_name": "Max Verstappen",
                "team": "Red Bull Racing",
                "color": "#3671C6",
                "number": 1
            },
            ...
        ]
    """
    try:
        manager = SeasonsDataManager()
        drivers = manager.get_drivers(year)
        
        if not drivers:
            logger.warning(f"No drivers found for season {year}")
            return []
        
        logger.info(f"Retrieved {len(drivers)} drivers for season {year}")
        return drivers
    
    except Exception as e:
        logger.error(f"Error retrieving drivers for season {year}: {e}", exc_info=True)
        return []


def get_teams_for_season(year: int) -> List[Dict]:
    """
    Get all teams for a specific season from MongoDB
    
    Args:
        year: Season year (e.g., 2025)
    
    Returns:
        List of team dictionaries with structure:
        [
            {
                "name": "Red Bull Racing",
                "color": "#3671C6",
                "drivers": ["VER", "PER"]
            },
            ...
        ]
    """
    try:
        manager = SeasonsDataManager()
        teams = manager.get_teams(year)
        
        if not teams:
            logger.warning(f"No teams found for season {year}")
            return []
        
        logger.info(f"Retrieved {len(teams)} teams for season {year}")
        return teams
    
    except Exception as e:
        logger.error(f"Error retrieving teams for season {year}: {e}", exc_info=True)
        return []


def get_driver_by_code(year: int, driver_code: str) -> Optional[Dict]:
    """
    Get a specific driver's information for a season
    
    Args:
        year: Season year (e.g., 2025)
        driver_code: Driver code (e.g., "VER")
    
    Returns:
        Driver dictionary or None if not found
    """
    try:
        manager = SeasonsDataManager()
        driver = manager.get_driver(year, driver_code)
        
        if not driver:
            logger.warning(f"Driver {driver_code} not found for season {year}")
            return None
        
        logger.info(f"Retrieved driver {driver_code} for season {year}")
        return driver
    
    except Exception as e:
        logger.error(f"Error retrieving driver {driver_code} for season {year}: {e}", exc_info=True)
        return None


def get_team_by_name(year: int, team_name: str) -> Optional[Dict]:
    """
    Get a specific team's information for a season
    
    Args:
        year: Season year (e.g., 2025)
        team_name: Team name (e.g., "Red Bull Racing")
    
    Returns:
        Team dictionary or None if not found
    """
    try:
        manager = SeasonsDataManager()
        team = manager.get_team(year, team_name)
        
        if not team:
            logger.warning(f"Team {team_name} not found for season {year}")
            return None
        
        logger.info(f"Retrieved team {team_name} for season {year}")
        return team
    
    except Exception as e:
        logger.error(f"Error retrieving team {team_name} for season {year}: {e}", exc_info=True)
        return None


def get_season_summary(year: int) -> Optional[Dict]:
    """
    Get complete season data including drivers, teams, and races
    
    Args:
        year: Season year (e.g., 2025)
    
    Returns:
        Complete season dictionary or None if not found
    """
    try:
        manager = SeasonsDataManager()
        season_data = manager.get_season_data(year)
        
        if not season_data:
            logger.warning(f"No season data found for {year}")
            return None
        
        logger.info(f"Retrieved complete season data for {year}")
        return season_data
    
    except Exception as e:
        logger.error(f"Error retrieving season data for {year}: {e}", exc_info=True)
        return None


def get_available_seasons() -> List[int]:
    """
    Get list of all available season years in the database
    
    Returns:
        List of years with season data
    """
    try:
        manager = SeasonsDataManager()
        seasons = manager.list_all_seasons()
        
        logger.info(f"Retrieved {len(seasons)} available seasons")
        return seasons
    
    except Exception as e:
        logger.error(f"Error retrieving available seasons: {e}", exc_info=True)
        return []


def get_driver_color(year: int, driver_code: str) -> str:
    """
    Get a driver's team color
    
    Args:
        year: Season year (e.g., 2025)
        driver_code: Driver code (e.g., "VER")
    
    Returns:
        Hex color string (e.g., "#3671C6") or "#FFFFFF" if not found
    """
    try:
        manager = SeasonsDataManager()
        color = manager.get_driver_color(year, driver_code)
        return color
    
    except Exception as e:
        logger.error(f"Error retrieving color for driver {driver_code} in {year}: {e}", exc_info=True)
        return "#FFFFFF"


def get_team_color(year: int, team_name: str) -> str:
    """
    Get a team's color
    
    Args:
        year: Season year (e.g., 2025)
        team_name: Team name (e.g., "Red Bull Racing")
    
    Returns:
        Hex color string (e.g., "#3671C6") or "#FFFFFF" if not found
    """
    try:
        manager = SeasonsDataManager()
        color = manager.get_team_color(year, team_name)
        return color
    
    except Exception as e:
        logger.error(f"Error retrieving color for team {team_name} in {year}: {e}", exc_info=True)
        return "#FFFFFF"


def get_team_drivers(year: int, team_name: str) -> List[str]:
    """
    Get driver codes for a specific team
    
    Args:
        year: Season year (e.g., 2025)
        team_name: Team name (e.g., "Red Bull Racing")
    
    Returns:
        List of driver codes (e.g., ["VER", "PER"])
    """
    try:
        manager = SeasonsDataManager()
        drivers = manager.get_team_drivers(year, team_name)
        return drivers
    
    except Exception as e:
        logger.error(f"Error retrieving drivers for team {team_name} in {year}: {e}", exc_info=True)
        return []
