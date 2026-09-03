"""
Seasons Data Manager for MongoDB
Manages season-specific data including drivers, teams, colors, and compositions
"""

from typing import Dict, List, Optional, Any
from src.repositories.mongo import MongoDBManager
from src.core.logging import get_logger

logger = get_logger(__name__)


class SeasonsDataManager:
    """
    Manager for seasons_data collection
    Handles CRUD operations for season-specific information
    """

    def __init__(self, db_manager: Optional[MongoDBManager] = None):
        """
        Initialize Seasons Data Manager

        Args:
            db_manager: Optional MongoDBManager instance. If None, creates a new one.
        """
        self.db_manager = db_manager if db_manager else MongoDBManager()
        self.collection = self.db_manager.db['seasons_data']

    def create_season_document(self, year: int, drivers: List[Dict], teams: List[Dict], races: Optional[List[Dict]] = None) -> bool:
        """
        Create or update a complete season document

        Args:
            year: Season year
            drivers: List of driver dictionaries with structure:
                {
                    "code": "VER",
                    "name": "Verstappen",
                    "full_name": "Max Verstappen",
                    "team": "Red Bull Racing",
                    "color": "#3671C6",
                    "number": 1
                }
            teams: List of team dictionaries with structure:
                {
                    "name": "Red Bull Racing",
                    "color": "#3671C6",
                    "drivers": ["VER", "TSU"]
                }
            races: Optional list of race dictionaries with structure:
                {
                    "grandPrix": "Australian Grand Prix",
                    "circuit": "Melbourne Grand Prix Circuit",
                    "country": "Australia",
                    "hasSprint": False,
                    "sessions": [...]
                }

        Returns:
            True if successful, False otherwise
        """
        try:
            season_doc = {
                "year": year,
                "drivers": drivers,
                "teams": teams,
                "last_updated": None  # Will be set by MongoDB
            }
            
            # Add races if provided
            if races:
                season_doc["races"] = races

            # Upsert (update if exists, insert if not)
            result = self.collection.update_one(
                {"year": year},
                {"$set": season_doc},
                upsert=True
            )

            if result.upserted_id:
                logger.info("Created season document for %s", year)
            else:
                logger.info("Updated season document for %s", year)

            return True

        except Exception as e:
            logger.exception("Error creating/updating season document: %s", e)
            return False

    def add_driver(self, year: int, driver: Dict) -> bool:
        """
        Add a driver to a season

        Args:
            year: Season year
            driver: Driver dictionary

        Returns:
            True if successful, False otherwise
        """
        try:
            result = self.collection.update_one(
                {"year": year},
                {"$push": {"drivers": driver}},
                upsert=True
            )
            logger.info("Added driver %s to %s season", driver.get('code'), year)
            return True
        except Exception as e:
            logger.error("Error adding driver: %s", e)
            return False

    def update_driver(self, year: int, driver_code: str, updates: Dict) -> bool:
        """
        Update a driver's information

        Args:
            year: Season year
            driver_code: Driver code (e.g., "VER")
            updates: Dictionary of fields to update

        Returns:
            True if successful, False otherwise
        """
        try:
            # Build update query for nested array element
            set_updates = {f"drivers.$.{key}": value for key, value in updates.items()}

            result = self.collection.update_one(
                {"year": year, "drivers.code": driver_code},
                {"$set": set_updates}
            )

            if result.modified_count > 0:
                logger.info("Updated driver %s in %s season", driver_code, year)
                return True
            else:
                logger.warning("Driver %s not found in %s season", driver_code, year)
                return False

        except Exception as e:
            logger.error("Error updating driver: %s", e)
            return False

    def add_team(self, year: int, team: Dict) -> bool:
        """
        Add a team to a season

        Args:
            year: Season year
            team: Team dictionary

        Returns:
            True if successful, False otherwise
        """
        try:
            result = self.collection.update_one(
                {"year": year},
                {"$push": {"teams": team}},
                upsert=True
            )
            logger.info("Added team %s to %s season", team.get('name'), year)
            return True
        except Exception as e:
            logger.error("Error adding team: %s", e)
            return False

    def update_team(self, year: int, team_name: str, updates: Dict) -> bool:
        """
        Update a team's information

        Args:
            year: Season year
            team_name: Team name
            updates: Dictionary of fields to update

        Returns:
            True if successful, False otherwise
        """
        try:
            set_updates = {f"teams.$.{key}": value for key, value in updates.items()}

            result = self.collection.update_one(
                {"year": year, "teams.name": team_name},
                {"$set": set_updates}
            )

            if result.modified_count > 0:
                logger.info("Updated team %s in %s season", team_name, year)
                return True
            else:
                logger.warning("Team %s not found in %s season", team_name, year)
                return False

        except Exception as e:
            logger.error("Error updating team: %s", e)
            return False

    def get_season_data(self, year: int) -> Optional[Dict]:
        """
        Get complete season data

        Args:
            year: Season year

        Returns:
            Season document or None if not found
        """
        try:
            season_doc = self.collection.find_one({"year": year}, {"_id": 0})
            return season_doc
        except Exception as e:
            logger.error("Error retrieving season data: %s", e)
            return None

    def get_drivers(self, year: int) -> List[Dict]:
        """
        Get all drivers for a season

        Args:
            year: Season year

        Returns:
            List of driver dictionaries
        """
        try:
            season_doc = self.collection.find_one({"year": year}, {"drivers": 1, "_id": 0})
            return season_doc.get('drivers', []) if season_doc else []
        except Exception as e:
            logger.error("Error retrieving drivers: %s", e)
            return []

    def get_driver(self, year: int, driver_code: str) -> Optional[Dict]:
        """
        Get a specific driver's information

        Args:
            year: Season year
            driver_code: Driver code (e.g., "VER")

        Returns:
            Driver dictionary or None if not found
        """
        try:
            drivers = self.get_drivers(year)
            for driver in drivers:
                if driver.get('code') == driver_code:
                    return driver
            return None
        except Exception as e:
            logger.error("Error retrieving driver: %s", e)
            return None

    def get_teams(self, year: int) -> List[Dict]:
        """
        Get all teams for a season

        Args:
            year: Season year

        Returns:
            List of team dictionaries
        """
        try:
            season_doc = self.collection.find_one({"year": year}, {"teams": 1, "_id": 0})
            return season_doc.get('teams', []) if season_doc else []
        except Exception as e:
            logger.error("Error retrieving teams: %s", e)
            return []

    def get_team(self, year: int, team_name: str) -> Optional[Dict]:
        """
        Get a specific team's information

        Args:
            year: Season year
            team_name: Team name

        Returns:
            Team dictionary or None if not found
        """
        try:
            teams = self.get_teams(year)
            for team in teams:
                if team.get('name') == team_name:
                    return team
            return None
        except Exception as e:
            logger.error("Error retrieving team: %s", e)
            return None

    def get_driver_color(self, year: int, driver_code: str) -> str:
        """
        Get a driver's color

        Args:
            year: Season year
            driver_code: Driver code

        Returns:
            Hex color string or "#FFFFFF" if not found
        """
        driver = self.get_driver(year, driver_code)
        return driver.get('color', '#FFFFFF') if driver else '#FFFFFF'

    def get_team_color(self, year: int, team_name: str) -> str:
        """
        Get a team's color

        Args:
            year: Season year
            team_name: Team name

        Returns:
            Hex color string or "#FFFFFF" if not found
        """
        team = self.get_team(year, team_name)
        return team.get('color', '#FFFFFF') if team else '#FFFFFF'

    def get_team_drivers(self, year: int, team_name: str) -> List[str]:
        """
        Get driver codes for a specific team

        Args:
            year: Season year
            team_name: Team name

        Returns:
            List of driver codes
        """
        team = self.get_team(year, team_name)
        return team.get('drivers', []) if team else []

    def list_all_seasons(self) -> List[int]:
        """
        List all available season years

        Returns:
            List of years with season data
        """
        try:
            seasons = self.collection.find({}, {"year": 1, "_id": 0}).sort("year", 1)
            return [s['year'] for s in seasons]
        except Exception as e:
            logger.error("Error listing seasons: %s", e)
            return []

    def delete_season(self, year: int) -> bool:
        """
        Delete a season document

        Args:
            year: Season year

        Returns:
            True if successful, False otherwise
        """
        try:
            result = self.collection.delete_one({"year": year})
            if result.deleted_count > 0:
                logger.info("Deleted season %s", year)
                return True
            else:
                logger.warning("Season %s not found", year)
                return False
        except Exception as e:
            logger.error("Error deleting season: %s", e)
            return False

