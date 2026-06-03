# Seasons Data Collection Guide

## Overview

The `seasons_data` collection stores season-specific information including drivers, teams, colors, and team compositions for each F1 season.

## Collection Structure

Each season document has the following structure:

```json
{
  "year": 2025,
  "drivers": [
    {
      "code": "VER",
      "name": "Verstappen",
      "full_name": "Max Verstappen",
      "team": "Red Bull Racing",
      "color": "#3671C6",
      "number": 1
    },
    ...
  ],
  "teams": [
    {
      "name": "Red Bull Racing",
      "color": "#3671C6",
      "drivers": ["VER", "TSU"]
    },
    ...
  ]
}
```

## Current Data

✅ **2024 Season**: 20 drivers, 10 teams
✅ **2025 Season**: 21 drivers, 10 teams

## Using SeasonsDataManager

### Basic Usage

```python
from src.repositories.seasonal_data import SeasonsDataManager

# Initialize the manager
seasons_manager = SeasonsDataManager()

# List all available seasons
years = seasons_manager.list_all_seasons()
print(years)  # Output: [2024, 2025]
```

### Get Driver Information

```python
# Get all drivers for a season
drivers_2025 = seasons_manager.get_drivers(2025)

# Get a specific driver
verstappen = seasons_manager.get_driver(2025, "VER")
print(verstappen)
# Output: {
#   "code": "VER",
#   "name": "Verstappen",
#   "full_name": "Max Verstappen",
#   "team": "Red Bull Racing",
#   "color": "#3671C6",
#   "number": 1
# }

# Get driver's color
color = seasons_manager.get_driver_color(2025, "VER")
print(color)  # Output: "#3671C6"
```

### Get Team Information

```python
# Get all teams for a season
teams_2025 = seasons_manager.get_teams(2025)

# Get a specific team
red_bull = seasons_manager.get_team(2025, "Red Bull Racing")
print(red_bull)
# Output: {
#   "name": "Red Bull Racing",
#   "color": "#3671C6",
#   "drivers": ["VER", "TSU"]
# }

# Get team's color
color = seasons_manager.get_team_color(2025, "Red Bull Racing")
print(color)  # Output: "#3671C6"

# Get team's drivers
drivers = seasons_manager.get_team_drivers(2025, "Red Bull Racing")
print(drivers)  # Output: ["VER", "TSU"]
```

### Get Complete Season Data

```python
# Get all data for a season
season_2025 = seasons_manager.get_season_data(2025)
print(season_2025.keys())  # Output: dict_keys(['year', 'drivers', 'teams'])
```

### Update Driver Information

```python
# Update a driver's team (e.g., mid-season change)
seasons_manager.update_driver(2025, "VER", {"team": "Ferrari", "color": "#E80020"})

# Add a new driver to a season
new_driver = {
    "code": "NEW",
    "name": "Newdriver",
    "full_name": "New Driver",
    "team": "Williams",
    "color": "#64C4FF",
    "number": 99
}
seasons_manager.add_driver(2025, new_driver)
```

### Update Team Information

```python
# Update a team's drivers
seasons_manager.update_team(2025, "Red Bull Racing", {"drivers": ["VER", "LAW"]})

# Add a new team
new_team = {
    "name": "New Team",
    "color": "#FF00FF",
    "drivers": ["DR1", "DR2"]
}
seasons_manager.add_team(2025, new_team)
```

## API Integration Example

You can integrate this into your API endpoints to get driver/team colors dynamically:

```python
from fastapi import FastAPI, Query
from src.repositories.seasonal_data import SeasonsDataManager

app = FastAPI()
seasons_manager = SeasonsDataManager()

@app.get('/api/driver-info')
def get_driver_info(
    year: int = Query(2025, description='Season year'),
    driver_code: str = Query('VER', description='Driver code')
):
    """Get driver information for a specific season"""
    driver = seasons_manager.get_driver(year, driver_code)
    if driver:
        return driver
    return {"error": "Driver not found"}

@app.get('/api/team-info')
def get_team_info(
    year: int = Query(2025, description='Season year'),
    team_name: str = Query('Red Bull Racing', description='Team name')
):
    """Get team information for a specific season"""
    team = seasons_manager.get_team(year, team_name)
    if team:
        return team
    return {"error": "Team not found"}

@app.get('/api/season-overview')
def get_season_overview(year: int = Query(2025, description='Season year')):
    """Get complete season overview"""
    drivers = seasons_manager.get_drivers(year)
    teams = seasons_manager.get_teams(year)
    
    return {
        "year": year,
        "total_drivers": len(drivers),
        "total_teams": len(teams),
        "drivers": drivers,
        "teams": teams
    }
```

## Adding Future Seasons

To add data for a new season (e.g., 2026):

```python
from src.repositories.seasonal_data import SeasonsDataManager

seasons_manager = SeasonsDataManager()

# Define 2026 teams
teams_2026 = [
    {
        "name": "Red Bull Racing",
        "color": "#3671C6",
        "drivers": ["VER", "NEW"]
    },
    # ... add all teams
]

# Define 2026 drivers
drivers_2026 = [
    {
        "code": "VER",
        "name": "Verstappen",
        "full_name": "Max Verstappen",
        "team": "Red Bull Racing",
        "color": "#3671C6",
        "number": 1
    },
    # ... add all drivers
]

# Create the season document
seasons_manager.create_season_document(2026, drivers_2026, teams_2026)
```

## Benefits

1. **Centralized Data**: All team and driver information in one place
2. **Year-Specific**: Different lineups for different seasons
3. **Easy Updates**: Update driver/team information without code changes
4. **Consistent Colors**: Same color scheme across all your visualizations
5. **API Ready**: Easily expose season data through your API

## 2025 Season Data

### Teams
- **Red Bull Racing** (#3671C6): VER, TSU
- **Ferrari** (#E80020): HAM, LEC
- **Mercedes** (#27F4D2): RUS, ANT
- **McLaren** (#FF8000): NOR, PIA
- **Aston Martin** (#229971): ALO, STR
- **Alpine** (#0093CC): GAS, COL
- **Williams** (#64C4FF): ALB, SAI
- **Racing Bulls** (#6692FF): LAW, HAD
- **Kick Sauber** (#52E252): HUL, BOR
- **Haas** (#B6BABD): BEA, OCO

### All 21 Drivers
VER, TSU, HAM, LEC, RUS, ANT, NOR, PIA, ALO, STR, GAS, COL, ALB, SAI, LAW, HAD, HUL, BOR, BEA, OCO, DOO

## 2024 Season Data

### Teams
- **Red Bull Racing** (#3671C6): VER, PER
- **Ferrari** (#E80020): LEC, SAI
- **Mercedes** (#27F4D2): HAM, RUS
- **McLaren** (#FF8000): NOR, PIA
- **Aston Martin** (#229971): ALO, STR
- **Alpine** (#0093CC): GAS, OCO
- **Williams** (#64C4FF): ALB, SAR
- **Racing Bulls** (#6692FF): RIC, TSU
- **Kick Sauber** (#52E252): BOT, ZHO
- **Haas** (#B6BABD): MAG, HUL

### All 20 Drivers
VER, PER, LEC, SAI, HAM, RUS, NOR, PIA, ALO, STR, GAS, OCO, ALB, SAR, RIC, TSU, BOT, ZHO, MAG, HUL

