from typing import Dict

from src.core.logging import get_logger
from src.ingestion.static_client import F1StaticClient

logger = get_logger(__name__)


def get_driver_team_mapping(base_url: str, client: F1StaticClient) -> Dict[str, str]:
    """
    Get mapping of driver numbers to team names from DriverList.json
    """
    try:
        driver_list_url = base_url + "DriverList.json"
        drivers = client.fetch_json(driver_list_url)

        # Build driver number -> team mapping
        driver_to_team = {}
        for driver_num, driver_info in drivers.items():
            team_name = driver_info.get('TeamName', 'Unknown')
            driver_to_team[driver_num] = team_name

        return driver_to_team

    except Exception as e:
        logger.warning("Could not fetch driver list: %s", e, exc_info=True)
        return {}
