import json
from typing import Dict
from src.ingestion.static_client import F1StaticClient

# Function to get the driver to team mapping right
def get_driver_team_mapping(base_url: str, client: F1StaticClient) -> Dict[str, str]:
    """
    Get mapping of driver numbers to team names from DriverList.json
    """
    try:
        driver_list_url = base_url + "DriverList.json"
        response = client.session.get(driver_list_url)
        response.raise_for_status()
        
        drivers = json.loads(response.content.decode('utf-8-sig'))
        
        # Build driver number -> team mapping
        driver_to_team = {}
        for driver_num, driver_info in drivers.items():
            team_name = driver_info.get('TeamName', 'Unknown')
            driver_to_team[driver_num] = team_name
        
        return driver_to_team
        
    except Exception as e:
        print(f"Warning: Could not fetch driver list: {e}")
        return {}