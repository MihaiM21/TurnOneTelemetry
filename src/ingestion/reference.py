from src.domain.data.seasons import f1_2025_races_data, f1_2026_races_data
import json

from src.core.exceptions import (
    DataNotAvailableError,
    SessionNotFoundError,
    UpstreamUnavailableError,
)
from src.core.logging import get_logger

logger = get_logger(__name__)

# Livetiming's static API covers ~2018 onward. Years before that lack the
# JSON index entirely.
_MIN_LIVETIMING_YEAR = 2018

# In-memory cache for years synthesized from livetiming.formula1.com.
# Mapping: year -> list[dict] in the same shape as the curated season files.
_LIVETIMING_SEASON_CACHE: "dict[int, list[dict]]" = {}


def _adapt_livetiming_index_to_season_events(year: int, index: dict) -> list[dict]:
    """
    Convert a livetiming Index.json payload into the same shape as the curated
    f1_YYYY_races_data lists (round, grandPrix, circuit, country, sessions).
    Missing fields are filled with empty strings — downstream consumers should
    treat curated data as authoritative when both exist.
    """
    events: list[dict] = []
    for idx, meeting in enumerate(index.get("Meetings", []), start=1):
        events.append({
            "round": idx,
            "grandPrix": meeting.get("Name", ""),
            "officialName": meeting.get("OfficialName", ""),
            "circuit": meeting.get("Circuit", {}).get("ShortName", "") if isinstance(meeting.get("Circuit"), dict) else "",
            "country": meeting.get("Country", {}).get("Name", "") if isinstance(meeting.get("Country"), dict) else "",
            "code": meeting.get("Code", ""),
            "key": meeting.get("Key"),
            "sessions": [
                {"name": s.get("Name", ""), "startDate": s.get("StartDate", ""), "endDate": s.get("EndDate", "")}
                for s in meeting.get("Sessions", [])
            ],
        })
    return events


def _fetch_season_events_from_livetiming(year: int) -> list[dict]:
    """
    Synthesize a season-events list from livetiming.formula1.com for a year
    not present in the curated data. Cached in-process per year.
    """
    if year in _LIVETIMING_SEASON_CACHE:
        return _LIVETIMING_SEASON_CACHE[year]

    if year < _MIN_LIVETIMING_YEAR:
        raise SessionNotFoundError(
            year=year,
            reason=f"Season {year} is before livetiming coverage ({_MIN_LIVETIMING_YEAR}+).",
        )

    # Imported here to avoid a circular import at module load time.
    from src.ingestion.static_client import F1StaticClient

    client = F1StaticClient()
    try:
        index = client.fetch_season_index(year)
    except SessionNotFoundError:
        raise
    except (DataNotAvailableError, UpstreamUnavailableError):
        raise
    except Exception as exc:
        logger.exception("Failed to fetch livetiming index for %s", year)
        raise UpstreamUnavailableError(
            source="livetiming", reason=f"Could not load season {year}: {exc}"
        ) from exc

    events = _adapt_livetiming_index_to_season_events(year, index)
    _LIVETIMING_SEASON_CACHE[year] = events
    return events


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
    """
    Get season events for a given year.

    Curated data exists for 2025 and 2026 (with richer metadata). For other
    years >= 2018 we synthesize the list from livetiming.formula1.com so V2
    endpoints can serve historical seasons. Years before 2018 raise
    SessionNotFoundError.
    """
    if season_year == 2026:
        return f1_2026_races_data
    if season_year == 2025:
        return f1_2025_races_data
    return _fetch_season_events_from_livetiming(int(season_year))

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