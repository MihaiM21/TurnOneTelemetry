import json
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from typing import Dict, List, Tuple, Any, Optional, Union

from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.repositories.plots import store_data_dict_to_mongo, get_plot_data_from_mongo
from src.ingestion.static_client import F1StaticClient
from src.domain.mappings import get_driver_team_mapping
from src.services.plotting.colors import get_driver_color

# ============================================================================
# UTILS & PARSERS
# ============================================================================

def parse_f1_time(time_str: Any) -> float:
    if pd.isna(time_str) or time_str == '':
        return 0.0
    if isinstance(time_str, (int, float)):
        return float(time_str)
    try:
        time_str = str(time_str).strip()
        parts = time_str.split(':')
        if len(parts) == 3: # h:mm:ss.ms
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2: # mm:ss.ms
            return float(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except: return 0.0

def _init(y: int, event_name: str, session_name: str) -> Tuple[str, str, str]:
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f'Throttle comparison {y} {event_name} {session_name}.png'
    return location, name, name.replace("png", "json")

# ============================================================================
# ENGINE LOGIC: RESAMPLING (The FastF1 Killer)
# ============================================================================

def get_accurate_average(df_lap: pd.DataFrame) -> float:
    """
    Calculează media (precum v1). Pentru ca datele să fie identice cu v1,
    doar facem media eșantioanelor brute, fără resampling.
    """
    if df_lap.empty:
        return 0.0
    return float(df_lap['Throttle'].clip(0, 100).mean())



# ============================================================================
# MAIN LOGIC: DATA FETCHING
# ============================================================================

def get_fastest_lap_windows_pandas(base_url: str, client: F1StaticClient) -> pd.DataFrame:
    columns = ['Driver', 'StartTime', 'EndTime', 'LapTime']
    timing_url = base_url + "TimingData.jsonStream"
    
    try:
        data = client.parse_jsonstream_simple(timing_url)
        best_laps = {}

        for entry in data:
            if 'Lines' not in entry: continue
            
            t_str = entry.get('_timestamp', entry.get('T'))
            if not t_str: continue
            t = parse_f1_time(t_str)
            
            for drv, line in entry['Lines'].items():
                if isinstance(line, dict):
                    # LastLapTime format in TimingData: {'Value': '1:32.4', 'PersonalFastest': True, ...}
                    last_lap_data = line.get('LastLapTime', {})
                    if not isinstance(last_lap_data, dict):
                        continue
                        
                    llt = last_lap_data.get('Value', '')
                    if llt and llt != '':
                        lap_time = parse_f1_time(llt)
                        if lap_time <= 0: continue
                        
                        # Check if it's their best lap so far
                        if drv not in best_laps or lap_time < best_laps[drv]['LapTime']:
                            best_laps[drv] = {
                                'Driver': str(drv),
                                'StartTime': t - lap_time,
                                'EndTime': t,
                                'LapTime': lap_time
                            }
        return pd.DataFrame(list(best_laps.values()))
    except Exception as ex: 
        print(f"Error in fast laps: {ex}")
        return pd.DataFrame(columns=columns)

from datetime import datetime

def extract_telemetry_pandas(base_url: str, client: F1StaticClient) -> pd.DataFrame:
    records = []
    try:
        entries = client.parse_compressed_stream(base_url + "CarData.z.jsonStream")
        session_start_utc = None
        
        for entry in entries:
            t_str = entry.get('_timestamp', entry.get('T'))
            packet_time = parse_f1_time(t_str)
            
            entries_list = entry.get('Entries', [])
            if not isinstance(entries_list, list): entries_list = [entries_list]
            
            if session_start_utc is None and packet_time > 0 and len(entries_list) > 0:
                first_utc_str = entries_list[0].get('Utc')
                if first_utc_str:
                    first_dt = datetime.fromisoformat(first_utc_str.replace('Z', '+00:00'))
                    session_start_utc = first_dt.timestamp() - packet_time
                    
            for item in entries_list:
                utc_str = item.get('Utc')
                sample_time = packet_time
                if utc_str and session_start_utc:
                    dt = datetime.fromisoformat(utc_str.replace('Z', '+00:00'))
                    sample_time = dt.timestamp() - session_start_utc
                    
                for drv_num, drv_data in item.get('Cars', {}).items():
                    thr = drv_data.get('Channels', {}).get('4')
                    if thr is not None:
                        records.append({'Time': sample_time, 'Driver': drv_num, 'Throttle': float(thr)})
        return pd.DataFrame(records)
    except Exception as e:
        print(f"Error in telemetry extraction: {e}")
        return pd.DataFrame()

# ============================================================================
# ENGINE
# ============================================================================

def process_throttle_data(y: int, identifier: Union[int, str], e: str, client: F1StaticClient) -> List[Dict]:
    event_info = client.get_event_info(y, identifier)
    if not event_info: raise ValueError(f"Event info nu a fost gasit pt {identifier}")
    event_name = event_info['name']
    round_nr = event_info['round_nr']

    base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)
    if not base_url: raise ValueError("URL sesiune negăsit")
        
    driver_codes = get_all_driver_codes(base_url, client)
    df_windows = get_fastest_lap_windows_pandas(base_url, client)
    df_telemetry = extract_telemetry_pandas(base_url, client)
    
    if df_telemetry.empty: return []

    results = []
    drivers = df_telemetry['Driver'].unique()

    for drv in drivers:
        driver_telemetry = df_telemetry[df_telemetry['Driver'] == drv]
        drv_window = df_windows[df_windows['Driver'] == str(drv)] if not df_windows.empty else pd.DataFrame()
        
        avg_throttle = 0.0
        if not drv_window.empty:
            start_t, end_t = drv_window.iloc[0]['StartTime'], drv_window.iloc[0]['EndTime']
            lap_data = driver_telemetry[(driver_telemetry['Time'] >= start_t) & (driver_telemetry['Time'] <= end_t)]
            avg_throttle = get_accurate_average(lap_data)
        else:
            continue
        
        if avg_throttle > 0:
            tla = driver_codes.get(drv, drv)
            results.append({
                'Driver': tla,
                'Average Throttle (%)': round(avg_throttle, 2),
                'Color': get_driver_color(tla)
            })
            
    results.sort(key=lambda x: x['Average Throttle (%)'], reverse=True)
    return results

def get_all_driver_codes(base_url, client):
    mapping = {}
    try:
        r = client.session.get(base_url + "DriverList.json")
        data = json.loads(r.content.decode('utf-8-sig'))
        for k, v in data.items(): mapping[k] = v.get('Tla', k)
    except: pass
    return mapping

# ============================================================================
# EXPORTED FUNCTIONS
# ============================================================================

def ThrottleComp(y: int, identifier: Union[int, str], e: str, store_to_mongo: bool = True) -> str:
    cached = get_plot_data_from_mongo(y, identifier, e, 'throttle_comparison', version='v2')
    client = F1StaticClient()
    
    event_info = client.get_event_info(y, identifier)
    if not event_info: return ""
    event_name = event_info['name']
    round_nr = event_info['round_nr']
    
    location, name, _ = _init(y, event_name, e)

    if cached:
        data = cached['data']
    else:
        data = process_throttle_data(y, identifier, e, client)
        if data:
            store_data_dict_to_mongo(year=y, round_nr=round_nr, session_name=e, event_name=event_name, 
                                     data_type='throttle_comparison', data=data, version='v2')

    if not data: return ""

    if store_to_mongo and data and not cached:
        pass # Already stored in else block above

    setup_theme.setup_turnone_theme()
    df = pd.DataFrame(data)
    _generate_plot(df['Driver'], df['Average Throttle (%)'], df['Color'], y, event_name, e, location, name)
    return f"{location}/{name}"

def ThrottleCompData(y: int, identifier: Union[int, str], e: str, store_to_mongo: bool = True) -> list:
    cached = get_plot_data_from_mongo(y, identifier, e, 'throttle_comparison', version='v2')
    if cached: return cached['data']

    client = F1StaticClient()
    data = process_throttle_data(y, identifier, e, client)
    
    if store_to_mongo and data:
        event_info = client.get_event_info(y, identifier)
        if event_info:
            event_name = event_info['name']
            round_nr = event_info['round_nr']
            store_data_dict_to_mongo(year=y, round_nr=round_nr, session_name=e, event_name=event_name, 
                                     data_type='throttle_comparison', data=data, version='v2')
    return data

def _generate_plot(drivers, throttles, colors, y, event, session, loc, name):
    fig, ax = plt.subplots(figsize=(13, 13), layout='constrained')
    ax.bar(drivers, throttles, color=colors)
    ax.set_ylim(50, 100)
    plt.yticks(range(50, 101, 5))
    
    for i, (d, t) in enumerate(zip(drivers, throttles)):
        ax.text(d, t + 0.5, f"{t}%", ha='center', color='white', fontweight='normal', fontsize=10)
        
    try:
        logo = mpimg.imread('assets/images/logo mic.png')
        fig.figimage(logo, 575, 575, zorder=3, alpha=.6)
    except: pass
    
    plt.suptitle(f'Throttle comparison (V2 Engine)\n{y} {event} {session}')
    setup_theme.add_glow(ax)
    plt.savefig(f"{loc}/{name}")
    plt.close()