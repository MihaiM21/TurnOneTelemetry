from lib.constants.seasons import f1_2025_races_data, f1_2026_races_data
import json


def get_season_events(season_year):
    """Get season events data for a given season year"""
    if season_year == 2026:
        return f1_2026_races_data
    elif season_year == 2025:
        return f1_2025_races_data
    else:
        raise ValueError(f"Season year {season_year} not found in constants.")

def get_season_drivers_and_teams(season_year):
    """Get season drivers and teams data for a given season year"""
    with open("lib/constants/drivers.json", "r") as f:
        drivers_data = json.load(f)
    
    with open("lib/constants/teams.json", "r") as f:
        teams_data = json.load(f)
    
    if season_year == 2026:
        return drivers_data["2026"], teams_data["2026"]
    elif season_year == 2025:
        return drivers_data["2025"], teams_data["2025"]
    else:
        raise ValueError(f"Season year {season_year} not found in constants.")
    
def get_season_drivers(season_year):
    """Get season drivers data for a given season year"""
    with open("lib/constants/drivers.json", "r") as f:
        drivers_data = json.load(f)
    
    if season_year == 2026:
        return drivers_data["2026"]
    elif season_year == 2025:
        return drivers_data["2025"]
    else:
        raise ValueError(f"Season year {season_year} not found in constants.")
    
def get_season_teams(season_year):
    """Get season teams data for a given season year"""
    with open("lib/constants/teams.json", "r") as f:
        teams_data = json.load(f)
    
    if season_year == 2026:
        return teams_data["2026"]
    elif season_year == 2025:
        return teams_data["2025"]
    else:
        raise ValueError(f"Season year {season_year} not found in constants.")