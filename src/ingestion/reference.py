from src.domain.data.seasons import f1_2025_races_data, f1_2026_races_data
import json
import time
import unicodedata
from collections import OrderedDict
from threading import Lock
from typing import Any, Optional

from src.core.config import settings
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

# Bounded TTL cache for livetiming-derived season data. Previous version was
# an unbounded dict that grew for the life of the process.
_LIVETIMING_CACHE_MAX = 20
_LIVETIMING_CACHE_TTL_SECONDS = 3600


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

    def set(self, key, value):
        with self._lock:
            self._store[key] = (time.time() + self._ttl, value)
            self._store.move_to_end(key)
            while len(self._store) > self._maxsize:
                self._store.popitem(last=False)


_LIVETIMING_SEASON_CACHE = _TTLCache(_LIVETIMING_CACHE_MAX, _LIVETIMING_CACHE_TTL_SECONDS)
_ENRICHED_SEASON_CACHE = _TTLCache(_LIVETIMING_CACHE_MAX, _LIVETIMING_CACHE_TTL_SECONDS)


def _fold(value) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(ch for ch in text.lower() if ch.isalnum())


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
    cached = _LIVETIMING_SEASON_CACHE.get(year)
    if cached is not None:
        return cached

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
    _LIVETIMING_SEASON_CACHE.set(year, events)
    return events


def _fetch_livetiming_meetings_safe(year: int) -> Optional[list[dict]]:
    """Fetch and adapt livetiming meetings; return None on any failure.

    Used to enrich curated season data with ``key`` / ``code`` /
    ``officialName``. Failures must never break the curated-data path.
    """
    try:
        return _fetch_season_events_from_livetiming(year)
    except (SessionNotFoundError, DataNotAvailableError, UpstreamUnavailableError) as exc:
        logger.debug("Livetiming enrichment skipped for %s: %s", year, exc)
        return None
    except Exception:
        logger.exception("Unexpected error enriching curated %s with livetiming", year)
        return None


def _enrich_curated_with_livetiming(year: int, curated: list[dict]) -> list[dict]:
    """Attach livetiming ``key`` / ``code`` / ``officialName`` to curated races.

    Curated data owns scheduling fields (``startTime``, ``endTime``,
    ``hasSprint``). Livetiming owns identity fields needed by
    :class:`~src.ingestion.event_resolver.EventResolver`. We merge them, prefer
    curated for anything already present, and log a WARN when round-order
    diverges so future drift is visible.
    """
    enriched = _ENRICHED_SEASON_CACHE.get(year)
    if enriched is not None:
        return enriched

    live = _fetch_livetiming_meetings_safe(year)
    if not live:
        # No livetiming available — return curated as-is (still works for
        # round-number and exact-name lookups).
        _ENRICHED_SEASON_CACHE.set(year, curated)
        return curated

    # Build lookup from folded curated name -> livetiming meeting.
    live_by_name = {_fold(m.get("grandPrix")): m for m in live if m.get("grandPrix")}
    live_by_country = {_fold(m.get("country")): m for m in live if m.get("country")}

    result: list[dict] = []
    for idx, race in enumerate(curated):
        match = (
            live_by_name.get(_fold(race.get("grandPrix")))
            or live_by_country.get(_fold(race.get("country")))
        )
        merged = dict(race)
        if match:
            # Don't overwrite curated keys — only fill gaps.
            for k in ("officialName", "code", "key", "circuit"):
                v = match.get(k)
                if v and not merged.get(k):
                    merged[k] = v
            live_round = match.get("round")
            curated_round = idx + 1
            if isinstance(live_round, int) and live_round != curated_round:
                logger.warning(
                    "Curated/livetiming round mismatch for %s %s: curated=%s livetiming=%s",
                    year, race.get("grandPrix"), curated_round, live_round,
                )
        merged.setdefault("round", idx + 1)
        result.append(merged)

    _ENRICHED_SEASON_CACHE.set(year, result)
    return result


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
        return _enrich_curated_with_livetiming(2026, f1_2026_races_data)
    if season_year == 2025:
        return _enrich_curated_with_livetiming(2025, f1_2025_races_data)
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