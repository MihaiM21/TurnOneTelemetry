from src.domain.data.seasons import f1_2025_races_data, f1_2026_races_data
import json


# Utils function
def check_team_name(year, name):
    teams = get_season_teams(year)

    for team in teams:
        if team["name"].lower() == name.lower() or team["short_name"].lower() == name.lower():
            return team

    return None

def check_driver_name(year, name):
    drivers = get_season_drivers(year)

    for driver in drivers:
        if driver["name"].lower() == name.lower() or driver["code"].lower() == name.lower() or driver["full_name"].lower() == name.lower():
            return driver
        if driver["number"] and str(driver["number"]) == name:
            return driver

    return None

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
    with open("src/domain/data/drivers.json", "r") as f:
        drivers_data = json.load(f)
    
    with open("src/domain/data/teams.json", "r") as f:
        teams_data = json.load(f)
    
    if season_year == 2026:
        return drivers_data["2026"], teams_data["2026"]
    elif season_year == 2025:
        return drivers_data["2025"], teams_data["2025"]
    else:
        raise ValueError(f"Season year {season_year} not found in constants.")
    
def get_season_drivers(season_year):
    """Get season drivers data for a given season year"""
    with open("src/domain/data/drivers.json", "r") as f:
        drivers_data = json.load(f)
    
    if season_year == 2026:
        return drivers_data["2026"]
    elif season_year == 2025:
        return drivers_data["2025"]
    else:
        raise ValueError(f"Season year {season_year} not found in constants.")
    
def get_season_teams(season_year):
    """Get season teams data for a given season year"""
    with open("src/domain/data/teams.json", "r") as f:
        teams_data = json.load(f)
    
    if season_year == 2026:
        return teams_data["2026"]
    elif season_year == 2025:
        return teams_data["2025"]
    else:
        raise ValueError(f"Season year {season_year} not found in constants.")
    
def get_team_details_by_name(season_year, team_name):
    """Get team details by team name"""
    team = check_team_name(season_year, team_name)

    if team:
        return team
    else:
        raise ValueError(f"Team {team_name} not found for season {season_year}.")
    
def get_driver_details_by_name(season_year, driver_name):
    """Get driver details by driver name or code"""
    driver = check_driver_name(season_year, driver_name)

    if driver:
        return driver
    else:
        raise ValueError(f"Driver {driver_name} not found for season {season_year}.")