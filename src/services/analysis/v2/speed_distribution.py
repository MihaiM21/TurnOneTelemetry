import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from typing import Dict, List, Tuple, Any, Optional, Union
from datetime import datetime
import json

from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.repositories.plots import store_data_dict_to_mongo, get_plot_data_from_mongo
from src.ingestion.static_client import F1StaticClient
from src.services.plotting.colors import get_driver_color
from src.services.analysis.v2._helpers import build_session_store

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

def _data_type(driver: Optional[str]) -> str:
    """Stored MongoDB key. The TLA is upper-cased so ``?driver=ver`` and
    ``?driver=VER`` resolve to the same document (the admin backfill writes the
    upper-cased form). The no-driver series keeps its historical capital-O
    ``Overall`` suffix.
    """
    return f"speed_distribution_{driver.strip().upper() if driver else 'Overall'}"


def _init(y: int, event_name: str, session_name: str, driver: Optional[str]) -> Tuple[str, str, str]:
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"

    drv_str = driver.strip().upper() if driver else "Overall"
    name = f'Speed Distribution {y} {event_name} {session_name} {drv_str}.png'
    return location, name, name.replace("png", "json")

def get_fastest_lap_windows_pandas(base_url: str, client: F1StaticClient, target_driver: Optional[str] = None) -> pd.DataFrame:
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
                    last_lap_data = line.get('LastLapTime', {})
                    if not isinstance(last_lap_data, dict):
                        continue
                        
                    llt = last_lap_data.get('Value', '')
                    if llt and llt != '':
                        lap_time = parse_f1_time(llt)
                        if lap_time <= 0: continue
                        
                        if drv not in best_laps or lap_time < best_laps[drv]['LapTime']:
                            best_laps[drv] = {
                                'DriverNum': str(drv),
                                'StartTime': t - lap_time,
                                'EndTime': t,
                                'LapTime': lap_time
                            }
        return pd.DataFrame(list(best_laps.values()))
    except Exception as ex: 
        print(f"Error in fast laps: {ex}")
        return pd.DataFrame(columns=columns)


def extract_telemetry_pandas(base_url: str, client: F1StaticClient, target_driver_num: str, start_t: float, end_t: float, store=None) -> pd.DataFrame:
    records = []
    try:
        print(f"Extracting telemetry for Car {target_driver_num} from {round(start_t, 2)} to {round(end_t, 2)}")
        entries = store.car_data() if store is not None else client.parse_compressed_stream(base_url + "CarData.z.jsonStream")
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
                    
                # Only process if within the lap window (+/- 2 seconds margin just in case)
                if sample_time >= (start_t - 2.0) and sample_time <= (end_t + 2.0):
                    cars = item.get('Cars', {})
                    if target_driver_num in cars:
                        drv_data = cars[target_driver_num]
                        speed = drv_data.get('Channels', {}).get('2') # 2 is speed
                        if speed is not None:
                            # Normalize time so the lap starts at 0
                            records.append({'Time (s)': sample_time - start_t, 'Speed (km/h)': float(speed)})
                            
        df = pd.DataFrame(records)
        # Sort by time to ensure line plot is sequential
        if not df.empty:
            df = df.sort_values(by='Time (s)')
            # Filter strictly strictly bounds to ensure clean start and end
            df = df[(df['Time (s)'] >= 0) & (df['Time (s)'] <= (end_t - start_t))]
        return df
    except Exception as e:
        print(f"Error in telemetry extraction: {e}")
        return pd.DataFrame()


def get_all_driver_codes(base_url, client):
    mapping = {} # Tla -> Car Number
    try:
        r = client.session.get(base_url + "DriverList.json")
        data = json.loads(r.content.decode('utf-8-sig'))
        for k, v in data.items(): 
            tla = v.get('Tla', k)
            mapping[tla] = str(k)
    except: pass
    return mapping

def get_driver_tla_from_num(base_url, client, num):
    try:
        r = client.session.get(base_url + "DriverList.json")
        data = json.loads(r.content.decode('utf-8-sig'))
        for k, v in data.items(): 
            if str(k) == str(num):
                return v.get('Tla', str(num))
    except: pass
    return str(num)


def process_speed_distribution_data(y: int, identifier: Union[int, str], e: str, client: F1StaticClient, driver: Optional[str] = None):
    event_info = client.get_event_info(y, identifier)
    if not event_info: raise ValueError(f"Event info not found for {identifier}")
    event_name = event_info['name']
    round_nr = event_info['round_nr']

    base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)
    if not base_url: raise ValueError("Session URL not found")
        
    df_windows = get_fastest_lap_windows_pandas(base_url, client)
    if df_windows.empty:
        return None, None
        
    if driver:
        # Find driver number
        driver_codes = get_all_driver_codes(base_url, client)
        if driver.upper() in driver_codes:
            drv_num = driver_codes[driver.upper()]
        else:
            drv_num = driver # Fallback
            
        driver_window = df_windows[df_windows['DriverNum'] == drv_num]
        if driver_window.empty:
            print(f"Could not find valid fast lap for {driver}")
            return None, None
            
        start_t = driver_window.iloc[0]['StartTime']
        end_t = driver_window.iloc[0]['EndTime']
        target_tla = driver.upper()
    else:
        # Overall fastest lap
        fastest_idx = df_windows['LapTime'].idxmin()
        fastest_row = df_windows.loc[fastest_idx]
        drv_num = fastest_row['DriverNum']
        start_t = fastest_row['StartTime']
        end_t = fastest_row['EndTime']
        target_tla = get_driver_tla_from_num(base_url, client, drv_num)
        
    store = build_session_store(y, identifier, e, client)
    df_telemetry = extract_telemetry_pandas(base_url, client, drv_num, start_t, end_t, store=store)
    if df_telemetry.empty: return None, None
    
    color = get_driver_color(target_tla)
    df_telemetry['Driver'] = target_tla
    df_telemetry['Color'] = color
    data_list = df_telemetry.to_dict('records')
    
    return data_list, {
        'driver': target_tla,
        'color': color,
        'event_name': event_name,
        'round_nr': event_info['round_nr']
    }

# ============================================================================
# EXPORTED FUNCTIONS
# ============================================================================

def SpeedDistributionPlot(y: int, identifier: Union[int, str], e: str, driver: Optional[str] = None, store_to_mongo: bool = True) -> str:
    drv_str = driver if driver else "Overall"
    cache_key = f'speed_distribution_{drv_str}'
    cached = get_plot_data_from_mongo(y, identifier, e, cache_key, version='v2')
    
    client = F1StaticClient()
    event_info = client.get_event_info(y, identifier)
    if not event_info: return ""
    event_name = event_info['name']
    
    location, name, _ = _init(y, event_name, e, driver)

    if cached:
        data = cached['data']
        if len(data) > 0:
            target_tla = data[0].get('Driver', driver if driver else "Unknown")
            color = data[0].get('Color', '#FFFFFF')
        else:
            target_tla = driver if driver else "Unknown"
            color = '#FFFFFF'
    else:
        result = process_speed_distribution_data(y, identifier, e, client, driver)
        if not result or not result[0]: return ""
        
        data, metadata = result
        target_tla = metadata['driver']
        color = metadata['color']
        
        if store_to_mongo:
            store_data_dict_to_mongo(
                year=y, round_nr=metadata['round_nr'], session_name=e, event_name=event_name, 
                data_type=cache_key, data=data, version='v2'
            )

    setup_theme.setup_turnone_theme()
    
    df = pd.DataFrame(data)
    times = df['Time (s)'].tolist()
    speeds = df['Speed (km/h)'].tolist()
    
    fig, ax = plt.subplots(figsize=(13, 8), layout='constrained')
    ax.plot(times, speeds, color=color, linewidth=2)
    
    ax.set_xlabel("Time (s)", fontsize=14, color='white')
    ax.set_ylabel("Speed (km/h)", fontsize=14, color='white')
    
    title_driver = target_tla if driver else f"Overall: {target_tla}"
    plt.suptitle(f'Speed Distribution (V2 Client) - Fastest Lap ({title_driver})\n{y} {event_name} {e}')
    
    try:
        logo = mpimg.imread('assets/images/logo mic.png')
        fig.figimage(logo, 575, 350, zorder=3, alpha=.6)
    except: pass
    
    setup_theme.add_glow(ax)
    plt.savefig(f"{location}/{name}")
    plt.close()
    
    return f"{location}/{name}"


def SpeedDistributionData(y: int, identifier: Union[int, str], e: str, driver: Optional[str] = None, store_to_mongo: bool = True) -> list:
    cache_key = _data_type(driver)
    cached = get_plot_data_from_mongo(y, identifier, e, cache_key, version='v2')
    if cached: return cached['data']

    client = F1StaticClient()
    result = process_speed_distribution_data(y, identifier, e, client, driver)
    
    if not result or not result[0]: return []
    data, metadata = result
    
    if store_to_mongo:
        event_info = client.get_event_info(y, identifier)
        if event_info:
            event_name = event_info['name']
            store_data_dict_to_mongo(
                year=y, round_nr=metadata['round_nr'], session_name=e, event_name=event_name, 
                data_type=cache_key, data=data, version='v2'
            )
            
    return data

if __name__ == "__main__":
    print("Testing V2 Speed Distribution...")
    try:
        plot_path = SpeedDistributionPlot(2023, 14, "Race", driver="VER")
        print(f"Plot saved to: {plot_path}")
        
        data = SpeedDistributionData(2023, 14, "Race", driver="VER")
        print(f"Data length: {len(data)}")
        
        print("\nTesting V2 Overall Fastest Lap...")
        plot_path_overall = SpeedDistributionPlot(2023, 14, "Race")
        print(f"Plot saved to: {plot_path_overall}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
