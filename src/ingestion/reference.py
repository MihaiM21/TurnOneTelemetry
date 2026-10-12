import json
import time
from collections import OrderedDict
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional

from src.core.exceptions import (
    DataNotAvailableError,
    SessionNotFoundError,
    UpstreamUnavailableError,
)
from src.core.logging import get_logger
from src.domain.data.seasons import f1_2025_races_data, f1_2026_races_data
from src.ingestion import calendar as cal

logger = get_logger(__name__)

# Livetiming's static API covers ~2018 onward. Years before that lack the
# JSON index entirely.
_MIN_LIVETIMING_YEAR = 2018

_CURATED = {2025: f1_2025_races_data, 2026: f1_2026_races_data}

# Bounded TTL cache for built calendars. A calendar built while livetiming was
# unreachable (curated-only) gets a short TTL so one transient failure cannot pin
# an un-enriched schedule for an hour.
_CALENDAR_CACHE_MAX = 20
_CALENDAR_CACHE_TTL_SECONDS = 3600
_CALENDAR_DEGRADED_TTL_SECONDS = 60


class _TTLCache:
    """Tiny bounded LRU+TTL cache. Pure stdlib so no new dependency."""

    def __init__(self, maxsize: int, ttl: float):
        self._maxsize = maxsize
        self._ttl = ttl
        self._store: "OrderedDict[Any, tuple[float, Any]]" = OrderedDict()
        self._lock = Lock()

    def get(self, key):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < time.time():
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return value

    def set(self, key, value, ttl: Optional[float] = None):
        with self._lock:
            self._store[key] = (time.time() + (self._ttl if ttl is None else ttl), value)
            self._store.move_to_end(key)
            while len(self._store) > self._maxsize:
                self._store.popitem(last=False)


_CALENDAR_CACHE = _TTLCache(_CALENDAR_CACHE_MAX, _CALENDAR_CACHE_TTL_SECONDS)


def _fold(value) -> str:
    return cal.fold(value)


def fetch_livetiming_index(year: int) -> dict:
    """Fetch the livetiming season index, mapping unexpected failures to upstream errors."""
    if year < _MIN_LIVETIMING_YEAR:
        raise SessionNotFoundError(
            year=year,
            reason=f"Season {year} is before livetiming coverage ({_MIN_LIVETIMING_YEAR}+).",
        )
    # Imported here to avoid a circular import at module load time.
    from src.ingestion.static_client import F1StaticClient

    try:
        return F1StaticClient().fetch_season_index(year)
    except (SessionNotFoundError, DataNotAvailableError, UpstreamUnavailableError):
        raise
    except Exception as exc:
        logger.exception("Failed to fetch livetiming index for %s", year)
        raise UpstreamUnavailableError(
            source="livetiming", reason=f"Could not load season {year}: {exc}"
        ) from exc


def _build_live_calendar(year: int) -> tuple[list[dict], bool]:
    """Build the calendar from live inputs. Returns ``(events, degraded)``."""
    curated_raw = _CURATED.get(year)
    curated = cal.adapt_curated(curated_raw, cal.load_overrides()) if curated_raw else None
    live: Optional[list[dict]] = None
    try:
        live = cal.adapt_livetiming_index(fetch_livetiming_index(year))
    except (SessionNotFoundError, DataNotAvailableError, UpstreamUnavailableError) as exc:
        if curated is None:
            raise
        logger.warning("Calendar %s built from the curated schedule only: %s", year, exc)
    events = cal.build_calendar(year, live=live, curated=curated)
    return events, live is None


def _calendar(year: int) -> list[dict]:
    cached = _CALENDAR_CACHE.get(year)
    if cached is not None:
        return cached

    events: Optional[list[dict]] = None
    degraded = False
    if year < datetime.now(timezone.utc).year:
        events = cal.load_snapshot(year)
    if events is None:
        events, degraded = _build_live_calendar(year)

    _CALENDAR_CACHE.set(
        year, events, ttl=_CALENDAR_DEGRADED_TTL_SECONDS if degraded else None
    )
    return events


def get_season_calendar(season_year) -> list[dict]:
    """Every event of a season -- testing and cancelled included -- with live status.

    Dicts carry ``round`` (official championship round, ``None`` for testing and
    cancelled events), ``kind``, ``status``, ``key``, ``aliases``, ``gp_id`` and
    ``sessions``. See :mod:`src.ingestion.calendar`.
    """
    return cal.apply_runtime_status(_calendar(int(season_year)))


def get_season_events(season_year, *, include_cancelled: bool = False, include_testing: bool = False) -> list[dict]:
    """Race events for a season, by official round.

    Curated data exists for 2025 and 2026; every other year >= 2018 comes from a
    committed snapshot (or, for the current year, livetiming). Cancelled events and
    pre-season testing are left out unless asked for: nearly every caller iterates
    "the races that were or will be run". Years before 2018 raise
    ``SessionNotFoundError``.
    """
    events = get_season_calendar(season_year)
    return [
        e for e in events
        if (include_cancelled or e["status"] != cal.STATUS_CANCELLED)
        and (include_testing or e["kind"] != cal.KIND_TESTING)
    ]


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