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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class F1StaticClient:
    """
    Client for fetching Formula 1 static telemetry data from livetiming.formula1.com
    """
    
    BASE_URL = "https://livetiming.formula1.com/static/"

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
            'User-Agent': 'F1TelemetryClient/1.0',
            'Accept': 'application/json, text/plain, */*'
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

    def _session_matches(self, requested_session: str, candidate_session: str) -> bool:
        """Return True when two session labels refer to the same F1 session."""
        requested_tokens = self._build_session_tokens(requested_session)
        candidate_tokens = self._build_session_tokens(candidate_session)

        if requested_tokens.intersection(candidate_tokens):
            return True

        requested_normalized = self._normalize_text(requested_session)
        candidate_normalized = self._normalize_text(candidate_session)
        return (
            requested_normalized in candidate_normalized
            or candidate_normalized in requested_normalized
        )
    
    # ========================================================================
    # TASK 1: THE SCRAPER
    # ========================================================================
    
    def fetch_season_index(self, year: int) -> Dict[str, Any]:
        """
        Fetch the season index for a given year.
        """
        url = f"{self.BASE_URL}{year}/Index.json"
        logger.info(f"Fetching season index: {url}")
        
        response = self.session.get(url)
        response.raise_for_status()
        
        # F1 JSON files have UTF-8 BOM, decode properly
        content = response.content.decode('utf-8-sig')
        return json.loads(content)
    
    def get_event_session_url(
        self, 
        year: int, 
        event_name: str, 
        session_name: str
    ) -> Optional[str]:
        """
        Construct the base URL for a specific event session.
        """
        season_index = self.fetch_season_index(year)
        
        # Find the event
        event_data = None
        for meeting in season_index.get('Meetings', []):
            if event_name.lower() in meeting.get('Name', '').lower():
                event_data = meeting
                logger.info(f"Found event: {meeting.get('Name')}")
                break
        
        if not event_data:
            logger.error(f"Event '{event_name}' not found in {year} season")
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
    
    def get_event_name(self, year: int, round_nr: int) -> Optional[str]:
        """
        Get the event name for a specific round number.
        
        Args:
            year: Season year
            round_nr: Round number (1-based index)
        
        Returns:
            Event name (e.g., "Italian Grand Prix") or None if not found
        """
        try:
            season_index = self.fetch_season_index(year)
            
            # Meetings are typically in chronological order
            meetings = season_index.get('Meetings', [])
            
            if 1 <= round_nr <= len(meetings):
                event_name = meetings[round_nr - 1].get('Name', '')
                logger.info(f"Round {round_nr}: {event_name}")
                return event_name
            else:
                logger.error(f"Round {round_nr} out of range (1-{len(meetings)})")
                return None
                
        except Exception as e:
            logger.error(f"Failed to get event name for round {round_nr}: {e}")
            return None
    
    def get_event_info(self, year: int, identifier: Union[int, str]) -> Optional[Dict[str, Any]]:
        """
        Get event information for a specific round number, event key, or official name.
        
        Args:
            year: Season year
            identifier: Round number (1-based index), Event Key (e.g., 1304), or Official Name
        
        Returns:
            Dictionary with event information or None if not found
        """
        try:
            season_index = self.fetch_season_index(year)
            meetings = season_index.get('Meetings', [])
            
            # 1. Try resolving as an integer (Round or Key)
            if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
                num_id = int(identifier)
                # Small numbers are likely round numbers
                if 1 <= num_id <= len(meetings) and num_id <= 30:
                    meeting = meetings[num_id - 1]
                    logger.info(f"Resolved round {num_id}: {meeting.get('Name')}")
                    return {
                        'round_nr': num_id,
                        'name': meeting.get('Name'),
                        'official_name': meeting.get('OfficialName'),
                        'key': meeting.get('Key')
                    }
                # Larger numbers are likely Keys
                else:
                    for idx, meeting in enumerate(meetings):
                        if meeting.get('Key') == num_id:
                            logger.info(f"Resolved Event Key {num_id}: {meeting.get('Name')}")
                            return {
                                'round_nr': idx + 1,
                                'name': meeting.get('Name'),
                                'official_name': meeting.get('OfficialName'),
                                'key': meeting.get('Key')
                            }
            
            # 2. Try resolving as a string (Name, Official Name, Code)
            str_id = str(identifier).lower()
            for idx, meeting in enumerate(meetings):
                name = meeting.get('Name', '').lower()
                official_name = meeting.get('OfficialName', '').lower()
                code = meeting.get('Code', '').lower()
                
                if str_id in name or str_id in official_name or str_id == code:
                    logger.info(f"Resolved Event String '{identifier}': {meeting.get('Name')}")
                    return {
                        'round_nr': idx + 1,
                        'name': meeting.get('Name'),
                        'official_name': meeting.get('OfficialName'),
                        'key': meeting.get('Key')
                    }
            
            logger.error(f"Event identifier '{identifier}' not found in {year} season")
            return None
                
        except Exception as e:
            logger.error(f"Failed to get event info for identifier {identifier}: {e}")
            return None
    
    # ========================================================================
    # TASK 2: THE PARSER
    # ========================================================================
    
    def parse_jsonstream(self, url: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Parse a .jsonStream file (concatenated JSON objects).
        """
        logger.info(f"Fetching and parsing jsonStream: {url}")
        
        response = self.session.get(url, stream=True)
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
        
        response = self.session.get(url)
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
        
        response = self.session.get(url)
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
        print(f"\n[SAVED] Data successfully saved to: {os.path.abspath(filename)}")
    except IOError as e:
        print(f"\n[ERROR] Could not save file: {e}")

def demo_task_1_scraper():
    """
    Task 1 Demo: Scrape the 2023 Italian Grand Prix Race session
    """
    print("=" * 80)
    print("TASK 1: THE SCRAPER")
    print("=" * 80)
    
    client = F1StaticClient()
    
    # Find the TimingData.jsonStream URL
    url = client.get_timing_data_url(
        year=2023,
        event_name="Italian Grand Prix",
        session_name="Race"
    )
    
    if url:
        print(f"\n[SUCCESS] TimingData URL:")
        print(f"   {url}")
    else:
        print("\n[FAILED] Could not find the session")
    
    return url


def demo_task_2_parser(url: Optional[str] = None):
    """
    Task 2 Demo: Parse the TimingData.jsonStream
    """
    print("\n" + "=" * 80)
    print("TASK 2: THE PARSER")
    print("=" * 80)
    
    client = F1StaticClient()
    
    if not url:
        url = client.get_timing_data_url(2023, "Italian Grand Prix", "Race")
    
    if not url:
        print("[FAILED] No URL available to parse")
        return []
    
    # Parse the first 5 entries
    print(f"\nParsing first 5 entries from: {url}")
    entries = client.parse_jsonstream_simple(url, limit=5)
    
    print(f"\n[SUCCESS] Parsed {len(entries)} entries\n")
    
    for i, entry in enumerate(entries, 1):
        print(f"Entry {i}:")
        print(json.dumps(entry, indent=2))
        print("-" * 40)
    
    return entries


def demo_task_3_decompressor():
    """
    Task 3 Demo: Decompress a .z.jsonStream file
    """
    print("\n" + "=" * 80)
    print("TASK 3: THE DECOMPRESSOR")
    print("=" * 80)
    
    client = F1StaticClient()
    
    # Get the session base URL
    base_url = client.get_event_session_url(2023, "Italian Grand Prix", "Race")
    
    if not base_url:
        print("[FAILED] Could not find session")
        return []
    
    # Try CarData.z.jsonStream
    car_data_url = urljoin(base_url, "CarData.z.jsonStream")
    
    print(f"\nParsing compressed stream: {car_data_url}")
    
    try:
        entries = client.parse_compressed_stream(car_data_url, limit=3)
        
        print(f"\n[SUCCESS] Decompressed {len(entries)} entries\n")
        
        for i, entry in enumerate(entries, 1):
            print(f"Entry {i}:")
            print(json.dumps(entry, indent=2, default=str))
            print("-" * 40)
        
        return entries
        
    except Exception as e:
        logger.error(f"Failed to parse compressed stream: {e}")
        print(f"\n[ERROR] {e}")
        return []



def run_all_demos():
    """
    Run all three task demonstrations in sequence and save result
    """
    print("\n")
    print("=" * 80)
    print("         F1 STATIC CONTENT INGESTION PIPELINE")
    print("                  Complete Demonstration")
    print("=" * 80)
    
    # Task 1
    url = demo_task_1_scraper()
    
    # Task 2
    if url:
        demo_task_2_parser(url)
    
    # Task 3 - This returns the most complex data, let's capture and save this
    car_telemetry_data = demo_task_3_decompressor()
    
    print("\n" + "=" * 80)
    
    if car_telemetry_data:
        # SAVE THE FILE
        output_filename = "f1_telemetry_output.json"
        print(f"Saving {len(car_telemetry_data)} telemetry entries to disk...")
        save_to_json(car_telemetry_data, output_filename)
        
    print("ALL TASKS COMPLETED SUCCESSFULLY")
    print("=" * 80)


if __name__ == "__main__":
    # Run all demonstrations
    run_all_demos()