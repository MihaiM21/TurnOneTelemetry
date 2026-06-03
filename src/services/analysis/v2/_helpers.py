import json
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional

from src.ingestion.static_client import F1StaticClient


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
                             target_driver_num: Optional[str] = None) -> pd.DataFrame:
    """
    Returns DataFrame with DriverNum, StartTime, EndTime, LapTime for each driver's personal best.
    If target_driver_num given, returns single-row DataFrame for that driver.
    """
    timing_url = base_url + "TimingData.jsonStream"
    try:
        data = client.parse_jsonstream_simple(timing_url)
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


def _get_session_start_utc(entries_list: list, packet_time: float) -> Optional[float]:
    if entries_list:
        first_utc_str = entries_list[0].get('Utc')
        if first_utc_str:
            first_dt = datetime.fromisoformat(first_utc_str.replace('Z', '+00:00'))
            return first_dt.timestamp() - packet_time
    return None


def extract_telemetry_for_lap(base_url: str, client: F1StaticClient,
                               driver_num: str, start_t: float, end_t: float,
                               channels: Optional[List[str]] = None) -> pd.DataFrame:
    """
    Extract telemetry for a specific driver during a lap window.
    channels: channel keys ('2'=Speed, '4'=Throttle, '5'=Brake, ...)
    Returns DataFrame with Time and one column per channel using CHANNEL_NAMES.
    """
    if channels is None:
        channels = ['2']

    records = []
    session_start_utc = None

    try:
        entries = client.parse_compressed_stream(base_url + "CarData.z.jsonStream")

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
        return df
    except Exception as e:
        print(f"Error extracting telemetry: {e}")
        return pd.DataFrame()


def extract_position_for_lap(base_url: str, client: F1StaticClient,
                              driver_num: str, start_t: float, end_t: float) -> pd.DataFrame:
    """
    Extract X/Y/Z position for a specific driver during a lap window from Position.z.jsonStream.
    Returns DataFrame with Time, X, Y, Z.
    """
    records = []
    session_start_utc = None
    total_entries_scanned = 0

    try:
        entries = client.parse_compressed_stream(base_url + "Position.z.jsonStream")
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
