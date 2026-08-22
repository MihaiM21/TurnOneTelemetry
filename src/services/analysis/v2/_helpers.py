import json
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.core.logging import get_logger
from src.ingestion.static_client import F1StaticClient
from src.ingestion.circuits_loader import get_circuit_data_file

logger = get_logger(__name__)


def build_session_store(year: int, identifier: Any, session: str,
                        client: Optional[F1StaticClient] = None) -> Optional[Any]:
    """Best-effort :class:`SessionDataStore` for cache-routing the big streams.

    Imported lazily to avoid a module-load cycle (``session_store`` imports from
    this module). Returns ``None`` on any resolution failure so callers can fall
    back to direct fetching without a hard dependency on the cache being usable.
    """
    try:
        from src.services.analysis.v2.session_store import SessionDataStore
        return SessionDataStore(year, identifier, session, client=client)
    except Exception as exc:
        logger.debug("SessionDataStore unavailable for %s %s %s: %s",
                     year, identifier, session, exc)
        return None


CHANNEL_NAMES = {
    '2': 'Speed',
    '3': 'RPM',
    '4': 'Throttle',
    '5': 'Brake',
    '45': 'Gear',
}


def parse_f1_time(time_str) -> float:
    if pd.isna(time_str) or time_str == '':
        return 0.0
    if isinstance(time_str, (int, float)):
        return float(time_str)
    try:
        time_str = str(time_str).strip()
        parts = time_str.split(':')
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except:
        return 0.0


def format_lap_time(seconds: float) -> str:
    if seconds <= 0:
        return ''
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}:{secs:06.3f}"


def get_all_driver_codes(base_url: str, client: F1StaticClient) -> Dict[str, str]:
    """Returns {car_number: TLA}"""
    mapping = {}
    try:
        r = client.session.get(base_url + "DriverList.json")
        data = json.loads(r.content.decode('utf-8-sig'))
        for k, v in data.items():
            mapping[str(k)] = v.get('Tla', str(k))
    except:
        pass
    return mapping


def get_driver_tla_from_num(base_url: str, client: F1StaticClient, num: str) -> str:
    try:
        r = client.session.get(base_url + "DriverList.json")
        data = json.loads(r.content.decode('utf-8-sig'))
        for k, v in data.items():
            if str(k) == str(num):
                return v.get('Tla', str(num))
    except:
        pass
    return str(num)


def _to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def extract_stints_from_data(
    timing_app_data: List[Dict], driver_num: str
) -> List[Dict]:
    """
    Compute per-stint records for a driver from already-parsed TimingAppData.

    Same contract as :func:`extract_stints` but takes the parsed
    ``TimingAppData.jsonStream`` entries directly (or the entries exposed by
    ``SessionDataStore.timing_app_data()``), so the caller can parse the stream
    once and reuse it across every driver instead of re-fetching per driver.

    Returns per-stint records for a driver:
    [{stint_number, compound, start_lap, end_lap, lap_count, tyre_life_end}, ...]

    Race-lap boundaries are derived by accumulation, not from the raw fields.
    Each stint snapshot exposes:
      * StartLaps - the tyre's age (laps already on it) when the stint began
      * TotalLaps - the tyre's age at the end of the stint
    So laps driven in a stint = TotalLaps - StartLaps, and the cumulative sum of
    those lengths gives the race-lap window for each stint. (StartLaps is tyre
    age, NOT a race lap number - using it as a lap was the original bug.)
    """
    data = timing_app_data or []

    # Merge the incremental updates into one final snapshot per stint index.
    snaps: Dict[str, Dict] = {}
    for entry in data:
        lines = entry.get('Lines', {})
        if driver_num not in lines:
            continue
        drv_data = lines[driver_num]
        if not isinstance(drv_data, dict):
            continue
        drv_stints = drv_data.get('Stints')
        if not drv_stints:
            continue
        # Stints arrives as either a dict keyed by stint index or a list.
        if isinstance(drv_stints, list):
            drv_stints = {str(i): s for i, s in enumerate(drv_stints) if isinstance(s, dict)}
        for stint_idx, stint_data in drv_stints.items():
            if not isinstance(stint_data, dict):
                continue
            snaps.setdefault(str(stint_idx), {}).update(stint_data)

    records: List[Dict] = []
    cursor = 0  # race laps completed by the end of the previous stint
    for idx in sorted(snaps.keys(), key=lambda x: int(x)):
        s = snaps[idx]
        compound = str(s.get('Compound', 'UNKNOWN') or 'UNKNOWN').upper()
        total_laps = _to_int(s.get('TotalLaps'))
        start_age = _to_int(s.get('StartLaps'), 0) or 0
        if total_laps is None:
            continue
        laps_in_stint = total_laps - start_age
        if laps_in_stint <= 0:
            continue
        start_lap = cursor + 1
        end_lap = cursor + laps_in_stint
        cursor = end_lap
        records.append({
            'stint_number': len(records) + 1,
            'compound': compound,
            'start_lap': start_lap,
            'end_lap': end_lap,
            'lap_count': laps_in_stint,
            'tyre_life_end': total_laps,
        })
    return records


def extract_stints(base_url: str, client: F1StaticClient, driver_num: str) -> List[Dict]:
    """
    Backwards-compatible wrapper: fetch+parse TimingAppData then delegate to
    :func:`extract_stints_from_data`.

    Kept so the existing V2 modules that call ``extract_stints(base_url, client,
    num)`` are untouched. New callers with a :class:`SessionDataStore` should
    parse the stream once and use ``extract_stints_from_data`` directly.
    """
    try:
        data = client.parse_jsonstream_simple(base_url + "TimingAppData.jsonStream")
    except Exception as exc:
        print(f"Error fetching TimingAppData for {driver_num}: {exc}")
        return []
    return extract_stints_from_data(data, driver_num)


def get_finishing_order(base_url: str, client: F1StaticClient,
                         store: Optional[Any] = None) -> Dict[str, int]:
    """Returns {car_number: finishing_position} from the final TimingData positions.

    When a ``SessionDataStore`` is passed as ``store``, the parsed TimingData
    stream is served from its cache instead of being re-downloaded.
    """
    order: Dict[str, int] = {}
    try:
        data = store.timing_data() if store is not None else client.parse_jsonstream_simple(base_url + "TimingData.jsonStream")
    except Exception as exc:
        print(f"Error fetching TimingData for finishing order: {exc}")
        return order

    for entry in data:
        for drv, line in entry.get('Lines', {}).items():
            if not isinstance(line, dict):
                continue
            pos = _to_int(line.get('Position'))
            if pos and pos > 0:
                order[str(drv)] = pos
    return order


def get_driver_team_from_list(base_url: str, client: F1StaticClient) -> Dict[str, Dict]:
    """Returns {car_number: {'tla': ..., 'team': ...}}"""
    result = {}
    try:
        r = client.session.get(base_url + "DriverList.json")
        data = json.loads(r.content.decode('utf-8-sig'))
        for k, v in data.items():
            result[str(k)] = {
                'tla': v.get('Tla', str(k)),
                'team': v.get('TeamName', 'Unknown')
            }
    except:
        pass
    return result


def get_fastest_lap_windows(base_url: str, client: F1StaticClient,
                             target_driver_num: Optional[str] = None,
                             store: Optional[Any] = None) -> pd.DataFrame:
    """
    Returns DataFrame with DriverNum, StartTime, EndTime, LapTime for each driver's personal best.
    If target_driver_num given, returns single-row DataFrame for that driver.

    When a ``SessionDataStore`` is passed as ``store``, the parsed TimingData
    stream is served from its cache instead of being re-downloaded.
    """
    timing_url = base_url + "TimingData.jsonStream"
    try:
        data = store.timing_data() if store is not None else client.parse_jsonstream_simple(timing_url)
        best_laps = {}

        for entry in data:
            if 'Lines' not in entry:
                continue
            t_str = entry.get('_timestamp', entry.get('T'))
            if not t_str:
                continue
            t = parse_f1_time(t_str)

            for drv, line in entry['Lines'].items():
                if not isinstance(line, dict):
                    continue
                last_lap = line.get('LastLapTime', {})
                if not isinstance(last_lap, dict):
                    continue
                llt = last_lap.get('Value', '')
                if not llt:
                    continue
                lap_time = parse_f1_time(llt)
                if lap_time <= 0:
                    continue
                if drv not in best_laps or lap_time < best_laps[drv]['LapTime']:
                    best_laps[drv] = {
                        'DriverNum': str(drv),
                        'StartTime': t - lap_time,
                        'EndTime': t,
                        'LapTime': lap_time
                    }

        df = pd.DataFrame(list(best_laps.values()))
        if target_driver_num is not None and not df.empty:
            df = df[df['DriverNum'] == str(target_driver_num)]
        return df
    except Exception as ex:
        print(f"Error getting fastest lap windows: {ex}")
        return pd.DataFrame(columns=['DriverNum', 'StartTime', 'EndTime', 'LapTime'])


def get_qualifying_classification(base_url: str, client: F1StaticClient,
                                   store: Optional[Any] = None) -> pd.DataFrame:
    """Each driver's personal-best lap, ordered by official session classification.

    A combined Q1/Q2/Q3 ``TimingData`` stream lets a driver knocked out early
    keep a fast banker lap that beats a later segment's genuine pole time, so
    ordering by raw ``LapTime`` (as ``get_fastest_lap_windows`` alone does) can
    misrank drivers. Live timing's ``Position`` field already carries the
    officially maintained classification (Q1/Q2/Q3 knockouts included) -- the
    same field ``get_finishing_order`` reads for race results -- so this joins
    that classification onto the best-lap windows and sorts by it, using
    ``LapTime`` only as a tiebreak/fallback for any driver missing a position.
    """
    df = get_fastest_lap_windows(base_url, client, store=store)
    if df.empty:
        return df

    positions = get_finishing_order(base_url, client, store=store)
    df = df.copy()
    df['Position'] = df['DriverNum'].map(positions)
    df = df.sort_values(['Position', 'LapTime'], na_position='last').reset_index(drop=True)
    return df


def _get_session_start_utc(entries_list: list, packet_time: float) -> Optional[float]:
    if entries_list:
        first_utc_str = entries_list[0].get('Utc')
        if first_utc_str:
            first_dt = datetime.fromisoformat(first_utc_str.replace('Z', '+00:00'))
            return first_dt.timestamp() - packet_time
    return None


def extract_telemetry_for_lap(base_url: str, client: F1StaticClient,
                               driver_num: str, start_t: float, end_t: float,
                               channels: Optional[List[str]] = None,
                               store: Optional[Any] = None) -> pd.DataFrame:
    """
    Extract telemetry for a specific driver during a lap window.
    channels: channel keys ('2'=Speed, '4'=Throttle, '5'=Brake, ...)
    Returns DataFrame with Time and one column per channel using CHANNEL_NAMES.

    When a ``SessionDataStore`` is passed as ``store``, the (multi-MB) CarData.z
    stream is served from its durable cache instead of being re-downloaded and
    re-decompressed for every driver.
    """
    if channels is None:
        channels = ['2']

    records = []
    session_start_utc = None

    try:
        entries = (store.car_data() if store is not None
                   else client.parse_compressed_stream(base_url + "CarData.z.jsonStream"))

        for entry in entries:
            t_str = entry.get('_timestamp', entry.get('T'))
            packet_time = parse_f1_time(t_str)

            entries_list = entry.get('Entries', [])
            if not isinstance(entries_list, list):
                entries_list = [entries_list]

            if session_start_utc is None and packet_time > 0 and entries_list:
                session_start_utc = _get_session_start_utc(entries_list, packet_time)

            for item in entries_list:
                utc_str = item.get('Utc')
                sample_time = packet_time
                if utc_str and session_start_utc:
                    dt = datetime.fromisoformat(utc_str.replace('Z', '+00:00'))
                    sample_time = dt.timestamp() - session_start_utc

                if not (start_t - 2.0 <= sample_time <= end_t + 2.0):
                    continue

                cars = item.get('Cars', {})
                if driver_num not in cars:
                    continue

                ch = cars[driver_num].get('Channels', {})
                row = {'Time': sample_time - start_t}
                for c in channels:
                    val = ch.get(c)
                    if val is not None:
                        row[CHANNEL_NAMES.get(c, c)] = float(val)

                if len(row) > 1:
                    records.append(row)

        df = pd.DataFrame(records)
        if not df.empty:
            df = df.sort_values('Time')
            df = df[(df['Time'] >= 0) & (df['Time'] <= end_t - start_t)]
        lap_duration = end_t - start_t
        if lap_duration > 0 and len(df) < lap_duration:
            logger.warning(
                "Sparse CarData match for driver %s: %d samples over a %.1fs lap window "
                "(expected several per second) -- window may not be a genuine green-flag lap",
                driver_num, len(df), lap_duration,
            )
        return df
    except Exception as e:
        print(f"Error extracting telemetry: {e}")
        return pd.DataFrame()


def extract_position_for_lap(base_url: str, client: F1StaticClient,
                              driver_num: str, start_t: float, end_t: float,
                              store: Optional[Any] = None) -> pd.DataFrame:
    """
    Extract X/Y/Z position for a specific driver during a lap window from Position.z.jsonStream.
    Returns DataFrame with Time, X, Y, Z.

    When a ``SessionDataStore`` is passed as ``store``, the (multi-MB) Position.z
    stream is served from its durable cache instead of being re-downloaded for
    every driver.
    """
    records = []
    session_start_utc = None
    total_entries_scanned = 0

    try:
        entries = (store.position_data() if store is not None
                   else client.parse_compressed_stream(base_url + "Position.z.jsonStream"))
        print(f"Position.z: fetched {len(entries)} compressed entries for driver {driver_num}")

        # Position.z structure: entry['Position'] is a list of frames.
        # Each frame: {'Timestamp': ISO_UTC_str, 'Entries': {car_num: {'Status', 'X', 'Y', 'Z'}}}
        for entry in entries:
            t_str = entry.get('_timestamp', entry.get('T'))
            packet_time = parse_f1_time(t_str)

            frames = entry.get('Position', [])
            if not frames:
                continue

            # Calibrate session_start_utc from the first frame's absolute UTC timestamp
            if session_start_utc is None and packet_time > 0:
                first_ts = frames[0].get('Timestamp') or frames[0].get('Utc')
                if first_ts:
                    first_dt = datetime.fromisoformat(first_ts.replace('Z', '+00:00'))
                    session_start_utc = first_dt.timestamp() - packet_time

            for frame in frames:
                total_entries_scanned += 1
                ts_str = frame.get('Timestamp') or frame.get('Utc')
                sample_time = packet_time
                if ts_str and session_start_utc:
                    dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    sample_time = dt.timestamp() - session_start_utc

                if not (start_t - 2.0 <= sample_time <= end_t + 2.0):
                    continue

                cars = frame.get('Entries', frame.get('Cars', {}))
                if driver_num not in cars:
                    continue

                pos = cars[driver_num]
                x = pos.get('X', 0)
                y = pos.get('Y', 0)
                z = pos.get('Z', 0)
                if x != 0 or y != 0:
                    records.append({
                        'Time': sample_time - start_t,
                        'X': float(x),
                        'Y': float(y),
                        'Z': float(z)
                    })

        print(f"Position.z: scanned {total_entries_scanned} samples, found {len(records)} in window "
              f"[{round(start_t,1)}-{round(end_t,1)}s] for driver {driver_num}")
        df = pd.DataFrame(records)
        if not df.empty:
            df = df.sort_values('Time')
            df = df[(df['Time'] >= 0) & (df['Time'] <= end_t - start_t)]
        lap_duration = end_t - start_t
        if lap_duration > 0 and len(df) < lap_duration:
            logger.warning(
                "Sparse Position match for driver %s: %d samples over a %.1fs lap window "
                "(expected several per second) -- window may not be a genuine green-flag lap",
                driver_num, len(df), lap_duration,
            )
        return df
    except Exception as e:
        print(f"Error extracting position for driver {driver_num}: {e}")
        return pd.DataFrame()


def compute_distance(df_pos: pd.DataFrame) -> pd.DataFrame:
    """Add Distance (m) column to position DataFrame using Euclidean X/Y deltas."""
    if df_pos.empty:
        return df_pos
    df = df_pos.copy()
    dx = df['X'].diff().fillna(0)
    dy = df['Y'].diff().fillna(0)
    df['Distance'] = np.sqrt(dx**2 + dy**2).cumsum()
    return df


def merge_distance_onto_telemetry(df_tel: pd.DataFrame, df_pos: pd.DataFrame) -> pd.DataFrame:
    """Attach Distance from position data onto telemetry via nearest-time merge."""
    if df_tel.empty:
        return df_tel
    if df_pos.empty or 'Distance' not in df_pos.columns:
        df_tel = df_tel.copy()
        df_tel['Distance'] = 0.0
        return df_tel

    df_pos_dist = df_pos[['Time', 'Distance']].sort_values('Time')
    df_tel_sorted = df_tel.sort_values('Time').copy()

    merged = pd.merge_asof(df_tel_sorted, df_pos_dist, on='Time', direction='nearest')
    return merged


def get_fastest_lap_telemetry(base_url: str, client: F1StaticClient, driver_num: str,
                               channels: Optional[List[str]] = None,
                               store: Optional[Any] = None) -> pd.DataFrame:
    """Fastest-lap telemetry for one driver, with Distance + X/Y merged on.

    Composes the existing single-purpose helpers:
      ``get_fastest_lap_windows`` -> the driver's personal-best lap window,
      ``extract_telemetry_for_lap`` + ``extract_position_for_lap`` -> raw
      channel / position samples inside that window,
      ``compute_distance`` -> cumulative track distance from X/Y,
      ``merge_distance_onto_telemetry`` -> nearest-time merge of Distance onto
      the telemetry frame.

    X/Y are merged onto the telemetry frame the same way (nearest-time
    ``merge_asof``) so a single frame carries Time, Distance, X, Y and every
    requested channel column.

    Returns an empty DataFrame if no fastest lap / telemetry / position data
    is available for the driver.

    When ``store`` exposes the race-derived accessors (``lap_times`` /
    ``track_status_periods``), the fastest *clean* lap (excludes pit in/out
    laps and non-green track-status periods) is preferred over the raw
    minimum-``LastLapTime`` scan -- a race has far more chances than
    practice/qualifying for an anomalous lap (red flag, VSC/SC, pit-affected)
    to falsely win on time and hand back a near-empty telemetry window.
    """
    if channels is None:
        channels = ['2']

    window = None
    if store is not None and hasattr(store, "lap_times") and hasattr(store, "track_status_periods"):
        try:
            from src.services.analysis.v2._race_helpers import get_clean_fastest_lap_window
            window = get_clean_fastest_lap_window(store, driver_num)
        except Exception as exc:
            logger.debug("Clean fastest-lap selection unavailable for %s: %s", driver_num, exc)

    if window is not None:
        start_t, end_t = window['StartTime'], window['EndTime']
        lap_time = window['LapTime']
    else:
        df_windows = get_fastest_lap_windows(base_url, client, target_driver_num=driver_num, store=store)
        if df_windows.empty:
            return pd.DataFrame()
        row = df_windows.iloc[0]
        start_t, end_t = row['StartTime'], row['EndTime']
        lap_time = row['LapTime']

    df_tel = extract_telemetry_for_lap(base_url, client, driver_num, start_t, end_t,
                                       channels=channels, store=store)
    if df_tel.empty:
        return pd.DataFrame()

    df_pos = extract_position_for_lap(base_url, client, driver_num, start_t, end_t, store=store)
    if df_pos.empty:
        df_tel = df_tel.copy()
        df_tel['Distance'] = 0.0
        df_tel['X'] = 0.0
        df_tel['Y'] = 0.0
        return df_tel

    df_pos = compute_distance(df_pos)
    df_tel = merge_distance_onto_telemetry(df_tel, df_pos)

    df_pos_xy = df_pos[['Time', 'X', 'Y']].sort_values('Time')
    df_tel_sorted = df_tel.sort_values('Time')
    merged = pd.merge_asof(df_tel_sorted, df_pos_xy, on='Time', direction='nearest')
    merged['LapTime'] = float(lap_time)
    return merged


def detect_corners(df: pd.DataFrame, min_separation_m: float = 25.0,
                    min_drop_kmh: float = 10.0) -> List[Dict[str, float]]:
    """Detect corner apexes from a Distance/Speed telemetry frame.

    Pure function, fully offline-testable: smooths ``Speed`` with a rolling
    median (~5 samples) to reduce sensor noise, then walks the smoothed trace
    looking for local minima ("apexes") preceded by a local maximum ("entry")
    where the drop from that entry speed exceeds ``min_drop_kmh``. Detected
    apexes closer together than ``min_separation_m`` are collapsed, keeping the
    slower (more pronounced) apex.

    Returns a list of ``{"distance_m", "min_speed_kmh", "entry_speed_kmh"}``
    dicts sorted by ``distance_m``. Returns ``[]`` if the frame lacks the
    required columns or has too few points.
    """
    if df is None or df.empty or 'Distance' not in df.columns or 'Speed' not in df.columns:
        return []

    frame = df[['Distance', 'Speed']].dropna().sort_values('Distance').reset_index(drop=True)
    if len(frame) < 5:
        return []

    smoothed = frame['Speed'].rolling(window=5, center=True, min_periods=1).median()
    distances = frame['Distance'].to_numpy()
    speeds = smoothed.to_numpy()

    n = len(speeds)
    candidates: List[Dict[str, float]] = []

    running_max = speeds[0]
    for i in range(0, n):
        # Track the local maximum ("entry speed") seen since the last apex.
        if speeds[i] > running_max:
            running_max = speeds[i]

        # At the boundaries there's no sample on one side; treat the missing
        # side as satisfied so an apex right at the start/end of the lap
        # (most commonly the final corner before the finish line) isn't
        # silently excluded from detection.
        left_ok = (i == 0) or (speeds[i] <= speeds[i - 1])
        right_ok = (i == n - 1) or (speeds[i] <= speeds[i + 1])
        is_local_min = left_ok and right_ok
        if not is_local_min:
            continue

        drop = running_max - speeds[i]
        if drop < min_drop_kmh:
            continue

        candidates.append({
            "distance_m": float(distances[i]),
            "min_speed_kmh": float(speeds[i]),
            "entry_speed_kmh": float(running_max),
        })
        # Reset the running max search after an apex so the next entry speed
        # is measured from the corner exit onward.
        running_max = speeds[i]

    if not candidates:
        return []

    # Enforce minimum separation, keeping the slower (more pronounced) apex
    # when two candidates fall within the window.
    candidates.sort(key=lambda c: c["distance_m"])
    merged: List[Dict[str, float]] = [candidates[0]]
    for cand in candidates[1:]:
        prev = merged[-1]
        if cand["distance_m"] - prev["distance_m"] < min_separation_m:
            if cand["min_speed_kmh"] < prev["min_speed_kmh"]:
                merged[-1] = cand
            continue
        merged.append(cand)

    merged.sort(key=lambda c: c["distance_m"])
    return merged


def get_circuit_info_for_session(store: Any) -> Optional[Dict[str, Any]]:
    """Circuit rotation + corner + outline data for a session, or ``None``.

    Reads ``store.session_info()['Meeting']['Circuit']['Key']`` and looks the
    circuit up via ``circuits_loader.get_circuit_data_file`` (the full
    ``CircuitLayout`` with corners/rotation/track_outline — not
    ``get_circuit_data_by_id``, which only returns the lightweight
    ``CircuitSummary`` listing entry with no track-map data), falling back
    across a small set of known years (the session's own year first, then
    2024/2025/2026) since not every circuit JSON is duplicated every season.

    Never raises: any failure (missing session info, unknown circuit, missing
    file, malformed JSON) results in ``None`` so callers can render an
    unrotated map / fall back to sequential corner numbering.
    """
    try:
        info = store.session_info()
    except Exception:
        return None
    if not isinstance(info, dict):
        return None

    circuit_key = None
    try:
        circuit_key = info.get('Meeting', {}).get('Circuit', {}).get('Key')
    except Exception:
        circuit_key = None
    if circuit_key is None:
        return None

    years_to_try = []
    session_year = getattr(store, 'year', None)
    if session_year is not None:
        years_to_try.append(session_year)
    for y in (2024, 2025, 2026):
        if y not in years_to_try:
            years_to_try.append(y)

    circuit = None
    for y in years_to_try:
        try:
            circuit = get_circuit_data_file(circuit_key, y)
        except Exception:
            circuit = None
        if circuit:
            break

    if not circuit:
        return None

    try:
        corners_raw = circuit.get('corners') or [] if isinstance(circuit, dict) else []
        corners = []
        for c in corners_raw:
            if not isinstance(c, dict):
                continue
            pos = c.get('position') or {}
            corners.append({
                "number": c.get('number'),
                "x": pos.get('x'),
                "y": pos.get('y'),
            })
        outline = circuit.get('track_outline') or {}
        return {
            "rotation": int(circuit.get('rotation') or 0),
            "corners": corners,
            "x": list(outline.get('x') or []),
            "y": list(outline.get('y') or []),
        }
    except Exception:
        return None
