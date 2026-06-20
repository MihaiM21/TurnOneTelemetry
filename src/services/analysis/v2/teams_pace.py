from typing import Union
from collections import defaultdict

import matplotlib.pyplot as plt
import matplotlib.image as mpimg

from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.services.plotting.colors import team_colors
from src.repositories.plots import store_data_dict_to_mongo, get_plot_data_from_mongo
from src.ingestion.static_client import F1StaticClient
from src.services.analysis.v2._helpers import get_driver_team_from_list
from src.services.analysis.v2.driver_pace import _extract_all_lap_times, _quartiles, _format_laptime


def _init(y, e, event_name):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{e}")
    location = f"outputs/plots/{y}/{event_folder}/{e}"
    name = f'Teams pace {y} {event_name} {e}.png'
    return location, name


def _render_plot(data_list, y, event_name, e, location, name):
    setup_theme.setup_turnone_theme()
    fig, ax = plt.subplots(figsize=(14, 9), layout='constrained')

    labels = [d['team'] for d in data_list]
    series = [d['lap_times_seconds'] for d in data_list]
    colors = [d['color'] for d in data_list]

    bp = ax.boxplot(
        series, tick_labels=labels, patch_artist=True,
        showfliers=True, widths=0.6, medianprops={'color': '#0d0d0d', 'linewidth': 2},
    )

    for box, color in zip(bp['boxes'], colors):
        box.set_facecolor(color)
        box.set_edgecolor(color)
        box.set_alpha(0.85)
    for whisker, color in zip(bp['whiskers'], [c for c in colors for _ in (0, 1)]):
        whisker.set_color(color)
    for cap, color in zip(bp['caps'], [c for c in colors for _ in (0, 1)]):
        cap.set_color(color)
    for flier, color in zip(bp['fliers'], colors):
        flier.set(marker='o', markerfacecolor=color, markeredgecolor=color, alpha=0.5, markersize=4)

    ax.set_ylabel('Lap time (s)')
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: _format_laptime(v) or f"{v:.1f}"))
    ax.tick_params(axis='x', labelrotation=30)

    try:
        logo = mpimg.imread('assets/images/logo mic.png')
        fig.figimage(logo, 575, 575, zorder=3, alpha=.6)
    except Exception:
        pass

    plt.suptitle(f'Teams pace\n{y} {event_name} {e}')
    setup_theme.add_glow(ax)
    plt.savefig(location + "/" + name)
    plt.close(fig)


def _build_data(y: int, identifier, e: str):
    client = F1StaticClient()
    event_info = client.get_event_info(y, identifier)
    if not event_info:
        raise ValueError(f"Event not found: {y} {identifier}")
    event_name = event_info['name']
    round_nr = event_info['round_nr']

    base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)
    if not base_url:
        raise ValueError(f"Session not found: {y} {event_name} {e}")

    driver_info = get_driver_team_from_list(base_url, client)

    team_times = defaultdict(list)
    for car_num, info in driver_info.items():
        times = _extract_all_lap_times(base_url, client, car_num)
        team_times[info['team']].extend(times)

    if not team_times:
        return [], event_name, round_nr

    # 107% quicklap filter using global session fastest (best-effort; livetiming
    # has no IsAccurate flag).
    all_times = [t for ts in team_times.values() for t in ts]
    fastest = min(all_times)
    cap = fastest * 1.07

    data_list = []
    for team, times in team_times.items():
        filtered = [t for t in times if t <= cap]
        if len(filtered) < 3:
            continue
        entry = {
            'team': team,
            'color': team_colors.get(team, '#FFFFFF'),
            'lap_times_seconds': filtered,
            'lap_count': len(filtered),
        }
        entry.update(_quartiles(filtered))
        data_list.append(entry)

    data_list.sort(key=lambda d: d['median'])
    return data_list, event_name, round_nr


def TeamsPacePlot(y: int, identifier: Union[int, str], e: str) -> str:
    cached_result = get_plot_data_from_mongo(y, identifier, e, 'teams_pace', version='v2')
    if cached_result:
        print("Using cached Teams Pace data from MongoDB (v2)")
        data_list = cached_result['data']
        event_name = cached_result['metadata']['event_name']
        location, name = _init(y, e, event_name)
        _render_plot(data_list, y, event_name, e, location, name)
        return location + "/" + name

    data_list, event_name, round_nr = _build_data(y, identifier, e)
    location, name = _init(y, e, event_name)

    if data_list:
        try:
            store_data_dict_to_mongo(
                year=y, round_nr=round_nr, session_name=e, event_name=event_name,
                data_type='teams_pace', data=data_list, version='v2',
            )
        except Exception as err:
            print(f"Warning: Failed to store to MongoDB: {err}")

    _render_plot(data_list, y, event_name, e, location, name)
    return location + "/" + name


def TeamsPaceData(y: int, identifier: Union[int, str], e: str, store_to_mongo: bool = True) -> list:
    cached_result = get_plot_data_from_mongo(y, identifier, e, 'teams_pace', version='v2')
    if cached_result:
        print("Using cached Teams Pace data from MongoDB (v2)")
        return cached_result['data']

    data_list, event_name, round_nr = _build_data(y, identifier, e)

    if store_to_mongo and data_list:
        try:
            store_data_dict_to_mongo(
                year=y, round_nr=round_nr, session_name=e, event_name=event_name,
                data_type='teams_pace', data=data_list, version='v2',
            )
        except Exception as err:
            print(f"Warning: Failed to store to MongoDB: {err}")

    return data_list


if __name__ == "__main__":
    try:
        data = TeamsPaceData(2023, 14, "Race")
        print(f"Teams returned: {len(data)}")
        if data:
            print(f"Fastest median: {data[0]['team']} {data[0]['median']}s")
    except Exception:
        import traceback
        traceback.print_exc()
