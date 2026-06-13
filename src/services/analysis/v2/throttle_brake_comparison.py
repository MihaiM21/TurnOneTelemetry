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
    compute_distance, merge_distance_onto_telemetry
)


def _init(y: int, event_name: str, session_name: str, d1: str, d2: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f'{y} {event_name} {d1} vs {d2} Throttle graph.png'
    return location, name, name.replace('png', 'json')


def _get_driver_telemetry(base_url: str, client: F1StaticClient, driver_tla: str) -> pd.DataFrame:
    """Returns Speed/Throttle/Brake/Distance DataFrame for a driver's fastest lap."""
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

    df_tel = extract_telemetry_for_lap(base_url, client, driver_num, start_t, end_t,
                                        channels=['2', '4', '5'])
    if df_tel.empty:
        return pd.DataFrame()

    df_pos = extract_position_for_lap(base_url, client, driver_num, start_t, end_t)
    if not df_pos.empty:
        df_pos = compute_distance(df_pos)
        df_tel = merge_distance_onto_telemetry(df_tel, df_pos)
    else:
        df_tel['Distance'] = 0.0

    df_tel['Driver'] = driver_tla.upper()
    return df_tel


def _process_data(base_url: str, client: F1StaticClient, d1: str, d2: str) -> Dict:
    color1 = get_driver_color(d1)
    color2 = get_driver_color(d2)

    tel1 = _get_driver_telemetry(base_url, client, d1)
    tel2 = _get_driver_telemetry(base_url, client, d2)

    telemetry_list = []
    for df, drv in [(tel1, d1.upper()), (tel2, d2.upper())]:
        if df.empty:
            continue
        for _, row in df.iterrows():
            telemetry_list.append({
                'distance': float(row.get('Distance', 0)),
                'speed': float(row.get('Speed', 0)),
                'throttle': float(row.get('Throttle', 0)),
                'brake': float(row.get('Brake', 0)),
                'lap_time': float(row.get('Time', 0)),
                'driver': drv
            })

    return {
        'driver1': d1.upper(),
        'driver2': d2.upper(),
        'driver1_color': color1,
        'driver2_color': color2,
        'telemetry': telemetry_list
    }


def _generate_plot(data: Dict, y: int, event_name: str, session_name: str,
                   location: str, name: str):
    d1 = data['driver1']
    d2 = data['driver2']
    color1 = data['driver1_color']
    color2 = data['driver2_color']

    df = pd.DataFrame(data['telemetry'])
    df1 = df[df['driver'] == d1]
    df2 = df[df['driver'] == d2]

    fig, axes = plt.subplots(3, figsize=(13, 13), clear=True)
    fig.suptitle(f'Throttle graph\n{y} {event_name} {session_name}')

    axes[0].plot(df1['distance'], df1['speed'], color=color1, label=d1)
    axes[0].plot(df2['distance'], df2['speed'], color=color2, label=d2)
    axes[0].set(ylabel='Speed')
    axes[0].legend(loc='lower right')

    axes[1].plot(df1['distance'], df1['throttle'], color=color1, label=d1)
    axes[1].plot(df2['distance'], df2['throttle'], color=color2, label=d2)
    axes[1].set(ylabel='Throttle')

    axes[2].plot(df1['distance'], df1['brake'], color=color1, label=d1)
    axes[2].plot(df2['distance'], df2['brake'], color=color2, label=d2)
    axes[2].set(ylabel='Brakes')

    for ax in axes.flat:
        ax.label_outer()

    try:
        logo = mpimg.imread('assets/images/logo mic.png')
        fig.figimage(logo, 575, 575, zorder=3, alpha=.6)
    except:
        pass

    plt.savefig(f"{location}/{name}")
    plt.close()


def ThrottleBrakeComp(y: int, identifier: Union[int, str], e: str, d1: str, d2: str) -> str:
    d1, d2 = d1.upper(), d2.upper()
    cache_key = f'throttle_brake_comparison_{d1}_{d2}'
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
        data = _process_data(base_url, client, d1, d2)
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


def ThrottleBrakeCompData(y: int, identifier: Union[int, str], e: str, d1: str, d2: str,
                           store_to_mongo: bool = True) -> dict:
    d1, d2 = d1.upper(), d2.upper()
    cache_key = f'throttle_brake_comparison_{d1}_{d2}'
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

    data = _process_data(base_url, client, d1, d2)

    if store_to_mongo and data and data.get('telemetry'):
        store_data_dict_to_mongo(
            year=y, round_nr=round_nr, session_name=e, event_name=event_name,
            data_type=cache_key, data=data, version='v2'
        )
    return data


if __name__ == "__main__":
    print("Testing V2 Throttle/Brake Comparison...")
    try:
        plot_path = ThrottleBrakeComp(2023, 14, "Qualifying", "VER", "NOR")
        print(f"Plot: {plot_path}")
        data = ThrottleBrakeCompData(2023, 14, "Qualifying", "VER", "NOR")
        print(f"Telemetry points: {len(data.get('telemetry', []))}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
