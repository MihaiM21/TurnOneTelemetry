"""
Lap Time Analysis (V2 / livetiming F1StaticClient).

Mirror of the V1 FastF1 implementation, sourced from the raw F1 live timing
API. Three stacked panels comparing two drivers' fastest laps along distance:
Speed, Delta time (gap of d2 vs d1), Throttle.

Delta time is computed by interpolating each driver's cumulative lap time
(the per-sample `Time` relative to lap start) onto the reference driver's
distance axis — identical math to the V1 sibling.
"""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from typing import Dict, Union

from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.repositories.plots import store_data_dict_to_mongo, get_plot_data_from_mongo
from src.services.plotting.colors import get_driver_color
from src.ingestion.static_client import F1StaticClient
from src.services.analysis.v2._helpers import (
    get_all_driver_codes, get_fastest_lap_windows,
    extract_telemetry_for_lap, extract_position_for_lap,
    compute_distance, merge_distance_onto_telemetry,
)

DATA_TYPE = 'lap_time_analysis'


def _init(y: int, event_name: str, session_name: str, d1: str, d2: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f'Lap Time Analysis {y} {event_name} {session_name} {d1} vs {d2}.png'
    return location, name, name.replace('png', 'json')


def _get_driver_telemetry(base_url: str, client: F1StaticClient, driver_tla: str):
    """Returns (Speed/Throttle/Time/Distance DataFrame, lap_time_seconds)."""
    driver_codes = get_all_driver_codes(base_url, client)
    tla_to_num = {v.upper(): k for k, v in driver_codes.items()}

    driver_num = tla_to_num.get(driver_tla.upper())
    if not driver_num:
        print(f"Driver {driver_tla} not found in session")
        return pd.DataFrame(), None

    df_windows = get_fastest_lap_windows(base_url, client, target_driver_num=driver_num)
    if df_windows.empty:
        print(f"No fastest lap found for {driver_tla}")
        return pd.DataFrame(), None

    row = df_windows.iloc[0]
    start_t, end_t, lap_time = row['StartTime'], row['EndTime'], float(row['LapTime'])

    df_tel = extract_telemetry_for_lap(base_url, client, driver_num, start_t, end_t,
                                       channels=['2', '4'])
    if df_tel.empty:
        return pd.DataFrame(), None

    df_pos = extract_position_for_lap(base_url, client, driver_num, start_t, end_t)
    if not df_pos.empty:
        df_pos = compute_distance(df_pos)
        df_tel = merge_distance_onto_telemetry(df_tel, df_pos)
    else:
        df_tel['Distance'] = 0.0

    df_tel['Driver'] = driver_tla.upper()
    return df_tel, lap_time


def _compute_delta(ref_dist, ref_time, cmp_dist, cmp_time):
    """Gap of the compare lap vs the reference lap on the reference distance axis.

    Positive => compare driver is behind (slower).
    """
    ref_dist = np.asarray(ref_dist, dtype=float)
    ref_time = np.asarray(ref_time, dtype=float)
    cmp_dist = np.asarray(cmp_dist, dtype=float)
    cmp_time = np.asarray(cmp_time, dtype=float)

    cmp_on_ref = np.interp(ref_dist, cmp_dist, cmp_time)
    return cmp_on_ref - ref_time


def _process_data(base_url: str, client: F1StaticClient,
                  d1: str, d2: str, y: int, event_name: str, e: str) -> Dict:
    color1 = get_driver_color(d1)
    color2 = get_driver_color(d2)

    tel1, lap1 = _get_driver_telemetry(base_url, client, d1)
    tel2, lap2 = _get_driver_telemetry(base_url, client, d2)

    if tel1.empty or tel2.empty:
        return {}

    tel1 = tel1.sort_values('Distance')
    tel2 = tel2.sort_values('Distance')

    telemetry_data = []
    for tel, drv in [(tel1, d1.upper()), (tel2, d2.upper())]:
        for _, r in tel.iterrows():
            telemetry_data.append({
                'distance': float(r.get('Distance', 0)),
                'speed': float(r.get('Speed', 0)),
                'throttle': float(r.get('Throttle', 0)),
                'lap_time': float(r.get('Time', 0)),
                'driver': drv,
            })

    delta = _compute_delta(
        tel1['Distance'], tel1['Time'], tel2['Distance'], tel2['Time']
    )
    delta_data = [
        {'distance': float(dist), 'delta': float(dlt)}
        for dist, dlt in zip(tel1['Distance'], delta)
    ]

    return {
        'driver1': d1.upper(),
        'driver2': d2.upper(),
        'driver1_color': color1,
        'driver2_color': color2,
        'driver1_laptime': lap1,
        'driver2_laptime': lap2,
        'reference_driver': d1.upper(),
        'telemetry': telemetry_data,
        'delta': delta_data,
        'session_info': {
            'year': y,
            'event_name': event_name,
            'session_name': e,
        },
    }


def _generate_plot(data: Dict, location: str, name: str):
    d1 = data['driver1']
    d2 = data['driver2']
    color1 = data['driver1_color']
    color2 = data['driver2_color']

    df = pd.DataFrame(data['telemetry'])
    df1 = df[df['driver'] == d1]
    df2 = df[df['driver'] == d2]
    delta_df = pd.DataFrame(data['delta'])

    fig, axes = plt.subplots(3, figsize=(13, 13), clear=True)
    fig.suptitle(
        f"Lap Time Analysis\n{data['session_info']['year']} "
        f"{data['session_info']['event_name']} "
        f"{data['session_info']['session_name']} — {d1} vs {d2}"
    )

    axes[0].plot(df1['distance'], df1['speed'], color=color1, label=d1)
    axes[0].plot(df2['distance'], df2['speed'], color=color2, label=d2)
    axes[0].set(ylabel='Speed (km/h)')
    axes[0].legend(loc='lower right')

    axes[1].plot(delta_df['distance'], delta_df['delta'], color=color2)
    axes[1].axhline(0.0, color=color1, linestyle='--', linewidth=1)
    axes[1].set(ylabel=f'Delta (s)\n<-- {d1} ahead | {d2} ahead -->')

    axes[2].plot(df1['distance'], df1['throttle'], color=color1, label=d1)
    axes[2].plot(df2['distance'], df2['throttle'], color=color2, label=d2)
    axes[2].set(ylabel='Throttle (%)', xlabel='Distance (m)')

    for a in axes.flat:
        a.label_outer()

    try:
        logo = mpimg.imread('assets/images/logo mic.png')
        fig.figimage(logo, 575, 575, zorder=3, alpha=.6)
    except Exception:
        pass

    plt.savefig(f"{location}/{name}")
    plt.close()


def LapTimeAnalysisPlot(y: int, identifier: Union[int, str], e: str, d1: str, d2: str) -> str:
    d1, d2 = d1.upper(), d2.upper()
    cache_key = f'{DATA_TYPE}_{d1}_{d2}'
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
        base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)
        if not base_url:
            return ""
        data = _process_data(base_url, client, d1, d2, y, event_name, e)
        if data and data.get('telemetry'):
            store_data_dict_to_mongo(
                year=y, round_nr=round_nr, session_name=e, event_name=event_name,
                data_type=cache_key, data=data, version='v2',
            )

    if not data or not data.get('telemetry'):
        return ""

    setup_theme.setup_turnone_theme()
    _generate_plot(data, location, name)
    return f"{location}/{name}"


def LapTimeAnalysisData(y: int, identifier: Union[int, str], e: str, d1: str, d2: str,
                        store_to_mongo: bool = True) -> dict:
    d1, d2 = d1.upper(), d2.upper()
    cache_key = f'{DATA_TYPE}_{d1}_{d2}'
    cached = get_plot_data_from_mongo(y, identifier, e, cache_key, version='v2')
    if cached:
        return cached['data']

    client = F1StaticClient()
    event_info = client.get_event_info(y, identifier)
    if not event_info:
        return {}
    event_name = event_info['name']
    round_nr = event_info['round_nr']

    base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)
    if not base_url:
        return {}

    data = _process_data(base_url, client, d1, d2, y, event_name, e)

    if store_to_mongo and data and data.get('telemetry'):
        store_data_dict_to_mongo(
            year=y, round_nr=round_nr, session_name=e, event_name=event_name,
            data_type=cache_key, data=data, version='v2',
        )
    return data


if __name__ == "__main__":
    print("Testing V2 Lap Time Analysis...")
    try:
        plot_path = LapTimeAnalysisPlot(2023, 14, "Qualifying", "VER", "NOR")
        print(f"Plot: {plot_path}")
        data = LapTimeAnalysisData(2023, 14, "Qualifying", "VER", "NOR")
        print(f"Telemetry points: {len(data.get('telemetry', []))}, "
              f"delta points: {len(data.get('delta', []))}")
    except Exception as ex:
        print(f"Error: {ex}")
        import traceback
        traceback.print_exc()
