import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.collections import LineCollection
from matplotlib.colors import ListedColormap
import matplotlib.patches as mpatches
from typing import Dict, Union

from src.utils import dirOrg
from src.utils import setup_theme
from src.utils.database.mongo_helper import store_data_dict_to_mongo, get_plot_data_from_mongo
from src.utils.teamColorPicker import get_driver_color
from src.data_loader.f1_static_client import F1StaticClient
from src.scripts.simple._v2_helpers import (
    get_all_driver_codes, get_fastest_lap_windows,
    extract_telemetry_for_lap, extract_position_for_lap, compute_distance
)


def _init(y: int, event_name: str, session_name: str, d1: str, d2: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f'{event_name} {session_name} {y} {d1} vs {d2}.png'
    return location, name, name.replace('png', 'json')


def _get_driver_track_data(base_url: str, client: F1StaticClient,
                            driver_tla: str) -> pd.DataFrame:
    """Returns X/Y/Distance/Speed DataFrame for a driver's fastest lap."""
    driver_codes = get_all_driver_codes(base_url, client)
    tla_to_num = {v.upper(): k for k, v in driver_codes.items()}

    driver_num = tla_to_num.get(driver_tla.upper())
    if not driver_num:
        print(f"Driver {driver_tla} not found in session")
        return pd.DataFrame()

    df_windows = get_fastest_lap_windows(base_url, client, target_driver_num=driver_num)
    if df_windows.empty:
        print(f"No fastest lap found for {driver_tla}")
        return pd.DataFrame()

    row = df_windows.iloc[0]
    start_t, end_t = row['StartTime'], row['EndTime']

    df_pos = extract_position_for_lap(base_url, client, driver_num, start_t, end_t)
    if df_pos.empty:
        print(f"No position data for {driver_tla}")
        return pd.DataFrame()

    df_pos = compute_distance(df_pos)

    df_tel = extract_telemetry_for_lap(base_url, client, driver_num, start_t, end_t, channels=['2'])

    if not df_tel.empty:
        df_tel_sorted = df_tel.sort_values('Time')
        df_pos_sorted = df_pos.sort_values('Time')
        merged = pd.merge_asof(df_pos_sorted, df_tel_sorted, on='Time', direction='nearest')
    else:
        merged = df_pos.copy()
        merged['Speed'] = 0.0

    merged['Driver'] = driver_tla.upper()
    return merged


def _process_data(base_url: str, client: F1StaticClient,
                   d1: str, d2: str, y: int, event_name: str, e: str) -> Dict:
    color1 = get_driver_color(d1)
    color2 = get_driver_color(d2)

    df1 = _get_driver_track_data(base_url, client, d1)
    df2 = _get_driver_track_data(base_url, client, d2)

    if df1.empty or df2.empty:
        return {}

    telemetry = pd.concat([df1, df2], ignore_index=True)

    num_minisectors = 25
    total_distance = telemetry['Distance'].max()
    if total_distance <= 0:
        return {}

    minisector_length = total_distance / num_minisectors
    telemetry['Minisector'] = (
        (telemetry['Distance'] // minisector_length + 1).astype(int).clip(1, num_minisectors)
    )

    avg_speed = telemetry.groupby(['Minisector', 'Driver'])['Speed'].mean().reset_index()
    fastest = avg_speed.loc[avg_speed.groupby('Minisector')['Speed'].idxmax()]
    fastest = fastest[['Minisector', 'Driver']].rename(columns={'Driver': 'Fastest_driver'})

    telemetry = telemetry.merge(fastest, on='Minisector')
    telemetry = telemetry.sort_values('Distance')
    telemetry['Fastest_driver_int'] = telemetry['Fastest_driver'].map(
        {d1.upper(): 1, d2.upper(): 2}
    )

    telemetry_list = []
    for _, row in telemetry.iterrows():
        fdi = row['Fastest_driver_int']
        telemetry_list.append({
            'x': float(row['X']),
            'y': float(row['Y']),
            'distance': float(row['Distance']),
            'speed': float(row.get('Speed', 0)),
            'driver': row['Driver'],
            'minisector': int(row['Minisector']),
            'fastest_driver': row['Fastest_driver'],
            'fastest_driver_int': int(fdi) if pd.notna(fdi) else 0
        })

    return {
        'driver1': d1.upper(),
        'driver2': d2.upper(),
        'driver1_color': color1,
        'driver2_color': color2,
        'telemetry': telemetry_list,
        'session_info': {
            'year': y,
            'event_name': event_name,
            'session_name': e
        }
    }


def _generate_plot(data: Dict, y: int, event_name: str, session_name: str,
                   location: str, name: str):
    d1 = data['driver1']
    d2 = data['driver2']
    color1 = data['driver1_color']
    color2 = data['driver2_color']

    telemetry = pd.DataFrame(data['telemetry'])
    if telemetry.empty:
        return

    telemetry = telemetry.sort_values('distance')

    x = np.array(telemetry['x'].values)
    y_vals = np.array(telemetry['y'].values)
    fastest_int = telemetry['fastest_driver_int'].to_numpy().astype(float)

    points = np.array([x, y_vals]).T.reshape(-1, 1, 2)
    segments = np.concatenate([points[:-1], points[1:]], axis=1)

    cmap = ListedColormap([color1, color2])
    lc = LineCollection(segments, norm=plt.Normalize(1, cmap.N + 1), cmap=cmap)
    lc.set_array(fastest_int)
    lc.set_linewidth(5)

    fig, ax = plt.subplots(figsize=(13, 13))
    ax.add_collection(lc)
    plt.axis('equal')
    plt.tick_params(labelleft=False, left=False, labelbottom=False, bottom=False)

    legend_patches = [
        mpatches.Patch(color=color1, label=d1),
        mpatches.Patch(color=color2, label=d2)
    ]
    plt.legend(handles=legend_patches, loc='upper right')
    plt.suptitle(f'{d1} vs {d2} {y} {event_name} {session_name}')

    try:
        logo = mpimg.imread('lib/logo mic.png')
        plt.figimage(logo, 575, 575, zorder=3, alpha=.6)
    except:
        pass

    plt.savefig(f"{location}/{name}")
    plt.close()


def TrackComparisonPlot(y: int, identifier: Union[int, str], e: str, d1: str, d2: str) -> str:
    d1, d2 = d1.upper(), d2.upper()
    cache_key = f'track_comparison_{d1}_{d2}'
    cached = get_plot_data_from_mongo(y, identifier, e, cache_key, version='v2')

    client = F1StaticClient()
    event_info = client.get_event_info(y, identifier)
    if not event_info:
        return ""
    event_name = event_info['name']
    round_nr = event_info['round_nr']

    location, name, _ = _init(y, event_name, e, d1, d2)

    if cached:
        data = cached['data']
    else:
        base_url = client.get_event_session_url(y, event_name, e)
        if not base_url:
            return ""
        data = _process_data(base_url, client, d1, d2, y, event_name, e)
        if data and data.get('telemetry'):
            store_data_dict_to_mongo(
                year=y, round_nr=round_nr, session_name=e, event_name=event_name,
                data_type=cache_key, data=data, version='v2'
            )

    if not data or not data.get('telemetry'):
        return ""

    setup_theme.setup_turnone_theme()
    _generate_plot(data, y, event_name, e, location, name)
    return f"{location}/{name}"


def TrackComparisonData(y: int, identifier: Union[int, str], e: str, d1: str, d2: str,
                        store_to_mongo: bool = True) -> dict:
    d1, d2 = d1.upper(), d2.upper()
    cache_key = f'track_comparison_{d1}_{d2}'
    cached = get_plot_data_from_mongo(y, identifier, e, cache_key, version='v2')
    if cached:
        return cached['data']

    client = F1StaticClient()
    event_info = client.get_event_info(y, identifier)
    if not event_info:
        return {}
    event_name = event_info['name']
    round_nr = event_info['round_nr']

    base_url = client.get_event_session_url(y, event_name, e)
    if not base_url:
        return {}

    data = _process_data(base_url, client, d1, d2, y, event_name, e)

    if store_to_mongo and data and data.get('telemetry'):
        store_data_dict_to_mongo(
            year=y, round_nr=round_nr, session_name=e, event_name=event_name,
            data_type=cache_key, data=data, version='v2'
        )
    return data


if __name__ == "__main__":
    print("Testing V2 Track Comparison...")
    try:
        plot_path = TrackComparisonPlot(2023, 14, "Qualifying", "VER", "NOR")
        print(f"Plot: {plot_path}")
        data = TrackComparisonData(2023, 14, "Qualifying", "VER", "NOR")
        print(f"Telemetry points: {len(data.get('telemetry', []))}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
