"""
F1 Static Content Ingestion Pipeline
=====================================
A robust client for fetching and parsing Formula 1 telemetry data from 
livetiming.formula1.com without relying on external libraries like FastF1.

Technical Details:
- Handles .json, .jsonStream, and .z compressed files
- Implements Base64 decoding + Zlib decompression (wbits=-15 for raw deflate)
- Parses concatenated JSON objects (not valid JSON arrays)
- Provides structured data output for downstream processing
- SAVES OUTPUT TO JSON FILE

Date: 2026-02-11
"""

import requests
import json
import base64
import zlib
import re
from typing import Dict, List, Optional, Any, Tuple, Union
from urllib.parse import urljoin
import logging
import os
import time
from threading import Lock

from src.core.config import settings
from src.core.exceptions import (
    DataNotAvailableError,
    SessionNotFoundError,
    UpstreamUnavailableError,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def source_token(base_url: Optional[str]) -> Optional[str]:
    """Stable identity of one livetiming session: ``{year}/{meeting_folder}/{session_folder}``.

    Every durable cache entry records this next to its data and is only served back
    when it matches the session being asked for. Cache keys built from
    ``(year, round, session label)`` alone cannot tell two events apart once round
    numbers move or a resolver picks the wrong meeting; the session's own path can.
    The proxy/origin host prefix is ignored on purpose.
    """
    if not base_url:
        return None
    parts = [p for p in str(base_url).rstrip("/").split("/") if p]
    return "/".join(parts[-3:]) if len(parts) >= 3 else None


# Season-index cache. The index was fetched on every call (twice per request via
# SessionDataStore); finished seasons never change, the current one gets a short TTL
# so a freshly listed meeting (a new session's path) is picked up quickly.
_INDEX_CACHE: dict = {}
_INDEX_CACHE_LOCK = Lock()
_INDEX_TTL_CURRENT_SECONDS = 300
_INDEX_TTL_PAST_SECONDS = 86400


def clear_index_cache() -> None:
    with _INDEX_CACHE_LOCK:
        _INDEX_CACHE.clear()


class F1StaticClient:
    """
    Client for fetching Formula 1 static telemetry data from livetiming.formula1.com
    """

    _ORIGIN_BASE_URL = "https://livetiming.formula1.com/static/"
    DEFAULT_TIMEOUT = 30  # seconds — prevents hung requests from exhausting uvicorn workers

    @property
    def BASE_URL(self) -> str:
        if settings.f1_proxy_base_url:
            return settings.f1_proxy_base_url.rstrip('/') + '/'
        return self._ORIGIN_BASE_URL

    _SESSION_ALIASES = {
        'race': {'r', 'race', 'grand prix'},
        'qualifying': {'q', 'quali', 'qualifying'},
        'sprint': {'s', 'sprint'},
        # Different seasons may label this as either Sprint Qualifying or Sprint Shootout.
        'sprint_qualifying': {'sq', 'sprint qualifying', 'sprint shootout'},
        'practice_1': {'fp1', 'p1', 'practice 1', 'free practice 1'},
        'practice_2': {'fp2', 'p2', 'practice 2', 'free practice 2'},
        'practice_3': {'fp3', 'p3', 'practice 3', 'free practice 3'},
    }
    
    def __init__(self, session: Optional[requests.Session] = None):
        """
        Initialize the F1 Static Client
        
        Args:
            session: Optional requests.Session object for connection pooling
        """
        self.session = session or requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Origin': 'https://www.formula1.com',
            'Referer': 'https://www.formula1.com/',
        })

    @staticmethod
    def _normalize_text(value: str) -> str:
        """Normalize text for loose, case-insensitive matching."""
        return re.sub(r'[^a-z0-9]+', ' ', value.lower()).strip()

    def _build_session_tokens(self, value: str) -> set:
        """Build a token set that includes common aliases and acronyms."""
        normalized = self._normalize_text(value)
        compact = normalized.replace(' ', '')
        words = [w for w in normalized.split() if w]
        acronym = ''.join(word[0] for word in words)

        tokens = {normalized, compact}
        if acronym:
            tokens.add(acronym)

        for _, aliases in self._SESSION_ALIASES.items():
            normalized_aliases = {self._normalize_text(alias) for alias in aliases}
            alias_compact = {alias.replace(' ', '') for alias in normalized_aliases}

            if (
                normalized in normalized_aliases
                or compact in alias_compact
                or acronym in alias_compact
            ):
                tokens.update(normalized_aliases)
                tokens.update(alias_compact)

        return {token for token in tokens if token}

    def _session_family(self, value: str) -> Optional[str]:
        """Return the ``_SESSION_ALIASES`` key ``value`` unambiguously matches, or ``None``."""
        normalized = self._normalize_text(value)
        compact = normalized.replace(' ', '')
        words = [w for w in normalized.split() if w]
        acronym = ''.join(word[0] for word in words) if words else ''

        for family, aliases in self._SESSION_ALIASES.items():
            normalized_aliases = {self._normalize_text(alias) for alias in aliases}
            alias_compact = {alias.replace(' ', '') for alias in normalized_aliases}
            if (
                normalized in normalized_aliases
                or compact in alias_compact
                or acronym in alias_compact
            ):
                return family
        return None

    def _session_matches(self, requested_session: str, candidate_session: str) -> bool:
        """Return True when two session labels refer to the same F1 session."""
        requested_tokens = self._build_session_tokens(requested_session)
        candidate_tokens = self._build_session_tokens(candidate_session)

        if requested_tokens.intersection(candidate_tokens):
            return True

        # Both labels unambiguously identify a *known* session family (checked
        # above and found different, since matching families would have
        # returned True already) -- e.g. "Qualifying" vs "Sprint Qualifying" on
        # a sprint weekend. "qualifying" is a literal substring of "sprint
        # qualifying", so without this guard the fuzzy fallback below would
        # wrongly treat them as the same session and silently serve the wrong
        # one's data. Only fall through to substring matching when at least
        # one side is an unrecognized/custom label the alias table can't judge.
        if self._session_family(requested_session) is not None and self._session_family(candidate_session) is not None:
            return False

        requested_normalized = self._normalize_text(requested_session)
        candidate_normalized = self._normalize_text(candidate_session)

        # Substring fallback only for sufficiently long labels. Short codes like
        # "r"/"q"/"s"/"p1" must match via the alias token path above; otherwise a
        # single letter spuriously matches inside a word (e.g. "r" in "pRactice").
        if len(requested_normalized) < 3 or len(candidate_normalized) < 3:
            return False

        return (
            requested_normalized in candidate_normalized
            or candidate_normalized in requested_normalized
        )
    
    # ========================================================================
    # TASK 1: THE SCRAPER
    # ========================================================================
    
    def fetch_season_index(self, year: int) -> Dict[str, Any]:
        """
        Fetch the season index for a given year (cached; do not mutate the result).
        """
        cache_key = (self.BASE_URL, int(year))
        with _INDEX_CACHE_LOCK:
            hit = _INDEX_CACHE.get(cache_key)
        if hit is not None and hit[0] > time.time():
            return hit[1]

        index = self._fetch_season_index_uncached(year)
        ttl = _INDEX_TTL_PAST_SECONDS if int(year) < time.gmtime().tm_year else _INDEX_TTL_CURRENT_SECONDS
        with _INDEX_CACHE_LOCK:
            _INDEX_CACHE[cache_key] = (time.time() + ttl, index)
        return index

    def _fetch_season_index_uncached(self, year: int) -> Dict[str, Any]:
        url = f"{self.BASE_URL}{year}/Index.json"
        logger.info(f"Fetching season index: {url}")

        try:
            response = self.session.get(url, timeout=self.DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            logger.exception("Network failure fetching season index for %s", year)
            raise UpstreamUnavailableError(
                source="livetiming", reason=f"Failed to fetch {url}: {exc}"
            ) from exc

        if response.status_code in (403, 404):
            # livetiming answers 403 (not 404) for objects it does not publish; the
            # 2022 index is one. That is "no data for this season", not an outage.
            raise SessionNotFoundError(
                year=year, reason=f"No livetiming index published for {year}"
            )
        if response.status_code >= 500:
            raise UpstreamUnavailableError(
                source="livetiming",
                reason=f"livetiming returned {response.status_code} for {url}",
            )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise UpstreamUnavailableError(
                source="livetiming", reason=str(exc)
            ) from exc

        # F1 JSON files have UTF-8 BOM, decode properly
        content = response.content.decode('utf-8-sig')
        return json.loads(content)

    def _find_meeting(
        self,
        year: int,
        event_name: str,
        round_nr: Optional[int],
        event_key: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        """Pick the livetiming meeting for an event -- by ``Key``, never by position."""
        from src.ingestion.event_resolver import resolve_event

        meetings = self.fetch_season_index(year).get('Meetings', [])

        key = event_key
        resolved_name = None
        if key is None:
            identifier: Union[int, str, None] = round_nr if round_nr else (event_name or None)
            if identifier is not None:
                try:
                    info = resolve_event(year, identifier)
                except SessionNotFoundError as exc:
                    logger.warning("Could not resolve %r for %s: %s", identifier, year, exc.reason)
                else:
                    key, resolved_name = info.key, info.name
                    if round_nr and event_name and not self._same_event(info, event_name):
                        logger.error(
                            "Round %s of %s is %r but the caller asked for %r; refusing to guess",
                            round_nr, year, info.name, event_name,
                        )
                        return None

        if key is not None:
            for meeting in meetings:
                if meeting.get('Key') == key:
                    logger.info("Found event by key %s: %s", key, meeting.get('Name'))
                    return meeting
            logger.error(
                "Meeting key %s (%s) is not in the %s livetiming index", key, resolved_name or event_name, year
            )
            return None

        # No key known (e.g. a historical snapshot built without livetiming): accept
        # only a full-name match that identifies exactly one non-testing meeting.
        wanted = self._normalize_text(event_name) if event_name else ""
        exact = [
            m for m in meetings
            if wanted and self._normalize_text(m.get('Name', '')) == wanted
            and 'testing' not in m.get('Name', '').lower()
        ]
        if len(exact) == 1:
            logger.info("Found event by exact name: %s", exact[0].get('Name'))
            return exact[0]
        return None

    @staticmethod
    def _same_event(info: Any, name: str) -> bool:
        from src.ingestion.calendar import fold
        return fold(name) in {fold(info.name), fold(info.official_name), fold(info.location)}

    def get_event_session_url(
        self,
        year: int,
        event_name: str,
        session_name: str,
        round_nr: Optional[int] = None,
        event_key: Optional[int] = None,
    ) -> Optional[str]:
        """
        Construct the base URL for a specific event session.

        The meeting is chosen by its livetiming ``Key`` (``event_key``, or the key of
        the calendar event that ``round_nr`` -- the *official* round -- or
        ``event_name`` resolves to). Position in the ``Meetings`` array is never
        used: it includes pre-season testing and skips cancelled rounds, and trusting
        it served one event's laps for another.
        """
        event_data = self._find_meeting(year, event_name, round_nr, event_key)
        if not event_data:
            logger.error(f"Event '{event_name}' (round_nr={round_nr}) not found in {year} season")
            return None

        # Find the session within the event's sessions
        session_data = None
        for session in event_data.get('Sessions', []):
            if self._session_matches(session_name, session.get('Name', '')):
                session_data = session
                logger.info(f"Found session: {session.get('Name')}")
                break

        if not session_data:
            logger.error(f"Session '{session_name}' not found for {event_name}")
            return None

        # Get session path
        session_path = session_data.get('Path')
        if not session_path:
            logger.error(f"No path found for session: {session_name}")
            return None

        # Construct the full base URL
        base_url = f"{self.BASE_URL}{session_path}"
        logger.info(f"Session base URL: {base_url}")

        return base_url

    def get_timing_data_url(
        self, 
        year: int, 
        event_name: str, 
        session_name: str
    ) -> Optional[str]:
        """
        Get the full URL for the TimingData.jsonStream file.
        """
        base_url = self.get_event_session_url(year, event_name, session_name)
        
        if not base_url:
            return None
        
        timing_data_url = urljoin(base_url, "TimingData.jsonStream")
        logger.info(f"TimingData URL: {timing_data_url}")
        
        return timing_data_url
    
    def get_event_info(self, year: int, identifier: Union[int, str]) -> Optional[Dict[str, Any]]:
        """
        Resolve a round number, event key, or event name to canonical event info.

        Delegates to :class:`~src.ingestion.event_resolver.EventResolver` so all
        V2 callers share one resolution path. Returns the legacy dict shape for
        backwards compatibility; raises :class:`SessionNotFoundError` on miss
        (the FastAPI handler turns this into a 404 with ``suggestions``).
        """
        from src.ingestion.event_resolver import resolve_event

        info = resolve_event(year, identifier)
        return {
            "round_nr": info.round_nr,
            "name": info.name,
            "official_name": info.official_name,
            "key": info.key,
            "code": info.code,
            "circuit": info.circuit,
            "country": info.country,
            "circuit_key": info.circuit_key,
            "location": info.location,
            "kind": info.kind,
            "status": info.status,
            "gp_id": info.gp_id,
        }
    
    # ========================================================================
    # CHAMPIONSHIP STANDINGS (best-effort; falls back to Ergast)
    # ========================================================================
    #
    # livetiming.formula1.com does not publish a clean standings JSON. We
    # attempt a small set of plausible static paths; on any failure we raise
    # UpstreamUnavailableError so `with_fallback` switches to Ergast cleanly.
    # SessionNotFoundError is deliberately NOT raised here — it would suppress
    # the fallback per `_FALLBACK_TRIGGERS` in services/analysis/base.py.

    def _attempt_standings_endpoints(self, candidate_urls: List[str]) -> Dict[str, Any]:
        last_reason = "no candidate endpoint returned standings"
        for url in candidate_urls:
            try:
                resp = self.session.get(url, timeout=self.DEFAULT_TIMEOUT)
            except requests.RequestException as exc:
                last_reason = f"network failure on {url}: {exc}"
                continue
            if resp.status_code != 200:
                last_reason = f"{url} returned {resp.status_code}"
                continue
            try:
                return json.loads(resp.content.decode("utf-8-sig"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                last_reason = f"{url} returned non-JSON: {exc}"
                continue
        raise UpstreamUnavailableError(source="livetiming", reason=last_reason)

    def fetch_drivers_standings(
        self, year: int, round_nr: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        suffix = f"/{round_nr}" if round_nr is not None else ""
        candidates = [
            f"{self.BASE_URL}{year}{suffix}/DriverStandings.json",
            f"{self.BASE_URL}{year}/DriverStandings.json",
        ]
        payload = self._attempt_standings_endpoints(candidates)
        rows = payload.get("Standings") or payload.get("DriverStandings") or []
        if not isinstance(rows, list) or not rows:
            raise UpstreamUnavailableError(
                source="livetiming",
                reason=f"empty drivers standings payload for {year} round={round_nr}",
            )
        return rows

    def fetch_constructors_standings(
        self, year: int, round_nr: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        suffix = f"/{round_nr}" if round_nr is not None else ""
        candidates = [
            f"{self.BASE_URL}{year}{suffix}/ConstructorStandings.json",
            f"{self.BASE_URL}{year}/ConstructorStandings.json",
        ]
        payload = self._attempt_standings_endpoints(candidates)
        rows = payload.get("Standings") or payload.get("ConstructorStandings") or []
        if not isinstance(rows, list) or not rows:
            raise UpstreamUnavailableError(
                source="livetiming",
                reason=f"empty constructors standings payload for {year} round={round_nr}",
            )
        return rows

    # ========================================================================
    # TASK 2: THE PARSER
    # ========================================================================
    
    def parse_jsonstream(self, url: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Parse a .jsonStream file (concatenated JSON objects).
        """
        logger.info(f"Fetching and parsing jsonStream: {url}")
        
        response = self.session.get(url, stream=True, timeout=self.DEFAULT_TIMEOUT)
        response.raise_for_status()
        
        parsed_entries = []
        buffer = ""
        entry_count = 0
        
        # Process the stream chunk by chunk
        for chunk in response.iter_content(chunk_size=8192, decode_unicode=True):
            if chunk:
                buffer += chunk
                
                # Try to parse complete JSON objects from buffer
                while buffer:
                    # Skip whitespace
                    buffer = buffer.lstrip()
                    
                    if not buffer:
                        break
                    
                    # Try to find a complete JSON object
                    try:
                        # Use JSONDecoder to parse one object at a time
                        decoder = json.JSONDecoder()
                        obj, idx = decoder.raw_decode(buffer)
                        
                        parsed_entries.append(obj)
                        entry_count += 1
                        
                        # Remove the parsed object from buffer
                        buffer = buffer[idx:]
                        
                        # Check if we've hit the limit
                        if limit and entry_count >= limit:
                            logger.info(f"Reached limit of {limit} entries")
                            return parsed_entries
                        
                    except json.JSONDecodeError:
                        # Not enough data for a complete object, break and get more
                        break
        
        # Process any remaining data in buffer
        buffer = buffer.strip()
        if buffer:
            try:
                obj = json.loads(buffer)
                parsed_entries.append(obj)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse remaining buffer: {e}")
        
        logger.info(f"Successfully parsed {len(parsed_entries)} entries")
        return parsed_entries
    
    def parse_jsonstream_simple(self, url: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Simpler line-by-line parser for .jsonStream files.
        """
        logger.info(f"Fetching and parsing jsonStream (line-by-line): {url}")
        
        response = self.session.get(url, timeout=self.DEFAULT_TIMEOUT)
        response.raise_for_status()

        # Decode with UTF-8 BOM handling
        content = response.content.decode('utf-8-sig')
        # Handle both CRLF and LF line endings
        lines = content.replace('\r\n', '\n').strip().split('\n')
        
        parsed_entries = []
        
        for i, line in enumerate(lines):
            if limit and i >= limit:
                break
            
            line = line.strip()
            if not line:
                continue
            
            try:
                # F1 format: "HH:MM:SS.mmm{json}"
                # Find where the JSON starts (first '{' or '[')
                json_start = -1
                for char_idx, char in enumerate(line):
                    if char in ('{', '['):
                        json_start = char_idx
                        break
                
                if json_start == -1:
                    logger.warning(f"No JSON found in line {i}")
                    continue
                
                # Extract timestamp and JSON
                timestamp = line[:json_start].strip()
                json_str = line[json_start:]
                
                # Parse the JSON
                obj = json.loads(json_str)
                
                # Add the timestamp to the object if it exists
                if timestamp:
                    obj['_timestamp'] = timestamp
                
                parsed_entries.append(obj)
                
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse line {i}: {e}")
                continue
        
        logger.info(f"Successfully parsed {len(parsed_entries)} entries")
        return parsed_entries
    
    # ========================================================================
    # TASK 3: THE DECOMPRESSOR
    # ========================================================================
    
    def decompress_z_entry(self, encoded_data: str) -> str:
        """
        Decompress a single .z entry (Base64 + Zlib compressed).
        """
        try:
            # Step 1: Decode from Base64
            compressed_data = base64.b64decode(encoded_data)
            
            # Step 2: Decompress using Zlib with wbits=-15 (raw deflate stream)
            decompressed_data = zlib.decompress(compressed_data, wbits=-15)
            
            # Step 3: Convert bytes to string
            return decompressed_data.decode('utf-8-sig')
            
        except Exception as e:
            logger.error(f"Decompression failed: {e}")
            raise
    
    def parse_compressed_stream(self, url: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Parse a .z.jsonStream file (Base64 + Zlib compressed concatenated JSON).
        """
        logger.info(f"Fetching and parsing compressed stream: {url}")
        
        response = self.session.get(url, timeout=self.DEFAULT_TIMEOUT)
        response.raise_for_status()

        # Handle UTF-8 BOM and normalize line endings
        content = response.content.decode('utf-8-sig')
        lines = content.replace('\r\n', '\n').strip().split('\n')
        
        parsed_entries = []
        
        for i, line in enumerate(lines):
            if limit and i >= limit:
                break
            
            line = line.strip()
            if not line:
                continue
            
            try:
                # F1 .z format has two patterns:
                # Pattern A: HH:MM:SS.mmm"BASE64DATA" (CarData.z, Position.z)
                # Pattern B: HH:MM:SS.mmm{"key":"BASE64DATA"} (some other files)
                
                # Try Pattern A first (most common)
                if '"' in line:
                    parts = line.split('"', 1)
                    if len(parts) == 2:
                        timestamp = parts[0].strip()
                        base64_data = parts[1].strip().strip('"')
                        
                        # Try to decompress
                        try:
                            decompressed = self.decompress_z_entry(base64_data)
                            # Try to parse as JSON
                            try:
                                data_obj = json.loads(decompressed)
                                if isinstance(data_obj, dict):
                                    data_obj['_timestamp'] = timestamp
                                    parsed_entries.append(data_obj)
                                else:
                                    parsed_entries.append({'_timestamp': timestamp, 'data': data_obj})
                                continue
                            except json.JSONDecodeError:
                                # Not JSON, store as raw string
                                parsed_entries.append({'_timestamp': timestamp, 'data': decompressed})
                                continue
                        except Exception:
                            # Decompression failed, try Pattern B
                            pass
                
                # Pattern B: Try JSON object parsing
                json_start = -1
                for char_idx, char in enumerate(line):
                    if char in ('{', '['):
                        json_start = char_idx
                        break
                
                if json_start == -1:
                    continue
                
                timestamp = line[:json_start].strip()
                json_str = line[json_start:]
                
                # Parse the outer JSON object
                outer_obj = json.loads(json_str)
                
                decompressed_obj = {}
                
                # Store the timestamp
                if timestamp:
                    decompressed_obj['_timestamp'] = timestamp
                elif 'Utc' in outer_obj:
                    decompressed_obj['_timestamp'] = outer_obj.get('Utc')
                
                # Try to decompress any Base64-looking strings in the object
                for key, value in outer_obj.items():
                    if isinstance(value, str) and len(value) > 20:
                        # Looks like it might be Base64, try to decompress
                        try:
                            decompressed = self.decompress_z_entry(value)
                            # Try to parse as JSON
                            try:
                                decompressed_obj[key] = json.loads(decompressed)
                            except json.JSONDecodeError:
                                # Not JSON, store as string
                                decompressed_obj[key] = decompressed
                        except Exception:
                            # Not compressed, store as-is
                            decompressed_obj[key] = value
                    else:
                        decompressed_obj[key] = value
                
                parsed_entries.append(decompressed_obj)
                
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse line {i}: {e}")
                continue
            except Exception as e:
                logger.warning(f"Failed to process line {i}: {e}")
                continue
        
        logger.info(f"Successfully parsed and decompressed {len(parsed_entries)} entries")
        return parsed_entries


# ============================================================================
# DEMONSTRATION FUNCTIONS
# ============================================================================

def save_to_json(data: Any, filename: str):
    """
    Helper function to save data to a JSON file
    """
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, default=str)
        logger.info("Data successfully saved to: %s", os.path.abspath(filename))
    except IOError as e:
        logger.error("Could not save file: %s", e)

def demo_task_1_scraper():
    """
    Task 1 Demo: Scrape the 2023 Italian Grand Prix Race session
    """
    logger.info("TASK 1: THE SCRAPER")

    client = F1StaticClient()

    # Find the TimingData.jsonStream URL
    url = client.get_timing_data_url(
        year=2023,
        event_name="Italian Grand Prix",
        session_name="Race"
    )

    if url:
        logger.info("TimingData URL: %s", url)
    else:
        logger.error("Could not find the session")

    return url


def demo_task_2_parser(url: Optional[str] = None):
    """
    Task 2 Demo: Parse the TimingData.jsonStream
    """
    logger.info("TASK 2: THE PARSER")

    client = F1StaticClient()

    if not url:
        url = client.get_timing_data_url(2023, "Italian Grand Prix", "Race")

    if not url:
        logger.error("No URL available to parse")
        return []

    # Parse the first 5 entries
    logger.info("Parsing first 5 entries from: %s", url)
    entries = client.parse_jsonstream_simple(url, limit=5)

    logger.info("Parsed %s entries", len(entries))

    for i, entry in enumerate(entries, 1):
        logger.info("Entry %s: %s", i, json.dumps(entry, indent=2))

    return entries


def demo_task_3_decompressor():
    """
    Task 3 Demo: Decompress a .z.jsonStream file
    """
    logger.info("TASK 3: THE DECOMPRESSOR")

    client = F1StaticClient()

    # Get the session base URL
    base_url = client.get_event_session_url(2023, "Italian Grand Prix", "Race")

    if not base_url:
        logger.error("Could not find session")
        return []

    # Try CarData.z.jsonStream
    car_data_url = urljoin(base_url, "CarData.z.jsonStream")

    logger.info("Parsing compressed stream: %s", car_data_url)

    try:
        entries = client.parse_compressed_stream(car_data_url, limit=3)

        logger.info("Decompressed %s entries", len(entries))

        for i, entry in enumerate(entries, 1):
            logger.info("Entry %s: %s", i, json.dumps(entry, indent=2, default=str))

        return entries

    except Exception as e:
        logger.error("Failed to parse compressed stream: %s", e)
        return []



def run_all_demos():
    """
    Run all three task demonstrations in sequence and save result
    """
    logger.info("F1 STATIC CONTENT INGESTION PIPELINE - Complete Demonstration")

    # Task 1
    url = demo_task_1_scraper()

    # Task 2
    if url:
        demo_task_2_parser(url)

    # Task 3 - This returns the most complex data, let's capture and save this
    car_telemetry_data = demo_task_3_decompressor()

    if car_telemetry_data:
        # SAVE THE FILE
        output_filename = "f1_telemetry_output.json"
        logger.info("Saving %s telemetry entries to disk...", len(car_telemetry_data))
        save_to_json(car_telemetry_data, output_filename)

    logger.info("ALL TASKS COMPLETED SUCCESSFULLY")


if __name__ == "__main__":
    # Run all demonstrations
    run_all_demos()