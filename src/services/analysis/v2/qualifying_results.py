import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from typing import List, Dict, Union

from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.repositories.plots import store_data_dict_to_mongo, get_plot_data_from_mongo
from src.services.plotting.colors import get_team_color
from src.ingestion.static_client import F1StaticClient
from src.services.analysis.v2._helpers import (
    format_lap_time, get_fastest_lap_windows, get_driver_team_from_list
)


def _init(y: int, event_name: str, session_name: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f'{y} {event_name} {session_name} results.png'
    return location, name, name.replace('png', 'json')


def _process_data(base_url: str, client: F1StaticClient) -> List[Dict]:
    driver_info = get_driver_team_from_list(base_url, client)
    df = get_fastest_lap_windows(base_url, client)

    if df.empty:
        return []

    df = df.sort_values('LapTime').reset_index(drop=True)
    pole_time = df.iloc[0]['LapTime']

    results = []
    for _, row in df.iterrows():
        drv_num = row['DriverNum']
        info = driver_info.get(drv_num, {})
        tla = info.get('tla', drv_num)
        team = info.get('team', 'Unknown')
        color = get_team_color(team)
        delta = round(row['LapTime'] - pole_time, 3)

        results.append({
            'Driver': tla,
            'Team': team,
            'LapTime': format_lap_time(row['LapTime']),
            'LapTimeDelta': delta,
            'Color': color
        })

    return results


def _generate_plot(data: List[Dict], y: int, event_name: str, session_name: str,
                   location: str, name: str):
    df = pd.DataFrame(data)

    fig, ax = plt.subplots(figsize=(13, 13))
    ax.barh(df.index, df['LapTimeDelta'], color=df['Color'].tolist(), edgecolor='grey')
    ax.set_yticks(df.index)
    ax.set_yticklabels(df['Driver'])

    max_val = df['LapTimeDelta'].max()
    ax.set_xlim(0, max(max_val * 1.15, 0.1))

    for i, row in df.iterrows():
        label = f"  {row['LapTime']}" if i == 0 else f"  +{row['LapTimeDelta']}s"
        ax.text(row['LapTimeDelta'], i, label, va='center', fontsize=13, weight='bold')

    ax.invert_yaxis()
    ax.set_axisbelow(True)
    ax.xaxis.grid(True, which='major', linestyle='--', color='black', zorder=-1000)

    pole = data[0]
    plt.suptitle(f"{event_name} {y} {session_name}\nFastest Lap: {pole['LapTime']} ({pole['Driver']})")

    try:
        logo = mpimg.imread('assets/images/logo mic.png')
        fig.figimage(logo, 575, 575, zorder=3, alpha=.6)
    except:
        pass

    setup_theme.add_glow(ax)
    plt.savefig(f"{location}/{name}")
    plt.close()


def QualiResultsPlot(y: int, identifier: Union[int, str], e: str) -> str:
    cached = get_plot_data_from_mongo(y, identifier, e, 'qualifying_results', version='v2')
    client = F1StaticClient()

    event_info = client.get_event_info(y, identifier)
    if not event_info:
        return ""
    event_name = event_info['name']
    round_nr = event_info['round_nr']

    location, name, _ = _init(y, event_name, e)

    if cached:
        data = cached['data']
    else:
        base_url = client.get_event_session_url(y, event_name, e)
        if not base_url:
            return ""
        data = _process_data(base_url, client)
        if data:
            store_data_dict_to_mongo(
                year=y, round_nr=round_nr, session_name=e, event_name=event_name,
                data_type='qualifying_results', data=data, version='v2'
            )

    if not data:
        return ""

    setup_theme.setup_turnone_theme()
    _generate_plot(data, y, event_name, e, location, name)
    return f"{location}/{name}"


def QualiResultsData(y: int, identifier: Union[int, str], e: str,
                     store_to_mongo: bool = True) -> list:
    cached = get_plot_data_from_mongo(y, identifier, e, 'qualifying_results', version='v2')
    if cached:
        return cached['data']

    client = F1StaticClient()
    event_info = client.get_event_info(y, identifier)
    if not event_info:
        return []
    event_name = event_info['name']
    round_nr = event_info['round_nr']

    base_url = client.get_event_session_url(y, event_name, e)
    if not base_url:
        return []

    data = _process_data(base_url, client)

    if store_to_mongo and data:
        store_data_dict_to_mongo(
            year=y, round_nr=round_nr, session_name=e, event_name=event_name,
            data_type='qualifying_results', data=data, version='v2'
        )
    return data


if __name__ == "__main__":
    print("Testing V2 Qualifying Results...")
    try:
        plot_path = QualiResultsPlot(2023, 14, "Qualifying")
        print(f"Plot: {plot_path}")
        data = QualiResultsData(2023, 14, "Qualifying")
        print(f"Drivers: {len(data)}")
        if data:
            print(f"P1: {data[0]}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
