import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

from src.services.plotting import output as dirOrg
from src.ingestion import fastf1_client as data_aqcuisition
from src.services.plotting import theme as setup_theme
from src.services.plotting.colors import get_driver_color
from src.repositories.plots import store_data_dict_to_mongo, get_plot_data_from_mongo


def _format_laptime(seconds):
    if seconds is None or (isinstance(seconds, float) and np.isnan(seconds)):
        return None
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}:{secs:06.3f}"


def _init(y, e, event_name):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{e}")
    location = f"outputs/plots/{y}/{event_folder}/{e}"
    name = f'Driver pace {y} {event_name} {e}.png'
    return location, name


def _quartiles(times):
    arr = np.array(times, dtype=float)
    return {
        'min': round(float(arr.min()), 3),
        'q1': round(float(np.quantile(arr, 0.25)), 3),
        'median': round(float(np.quantile(arr, 0.50)), 3),
        'q3': round(float(np.quantile(arr, 0.75)), 3),
        'max': round(float(arr.max()), 3),
    }


def _render_plot(data_list, y, event_name, e, location, name):
    setup_theme.setup_turnone_theme()
    fig, ax = plt.subplots(figsize=(14, 9), layout='constrained')

    labels = [d['driver'] for d in data_list]
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
    ax.tick_params(axis='x', labelrotation=45)

    try:
        logo = mpimg.imread('assets/images/logo mic.png')
        fig.figimage(logo, 575, 575, zorder=3, alpha=.6)
    except Exception:
        pass

    plt.suptitle(f'Driver pace\n{y} {event_name} {e}')
    setup_theme.add_glow(ax)
    plt.savefig(location + "/" + name)
    plt.close(fig)


def DriverPacePlot(y, r, e):
    cached_result = get_plot_data_from_mongo(y, r, e, 'driver_pace')
    if cached_result:
        print("Using cached Driver Pace data from MongoDB (v1)")
        data_list = cached_result['data']
        event_name = cached_result['metadata']['event_name']
        location, name = _init(y, e, event_name)
        _render_plot(data_list, y, event_name, e, location, name)
        return location + "/" + name

    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()
    event_name = session.event['EventName']
    location, name = _init(y, e, event_name)

    laps = session.laps.pick_quicklaps(threshold=1.07)
    try:
        laps = laps.pick_accurate()
    except Exception:
        pass
    laps = laps.copy()
    laps['LapTimeSeconds'] = laps['LapTime'].dt.total_seconds()
    laps = laps.dropna(subset=['LapTimeSeconds'])

    data_list = []
    for drv in pd.unique(laps['Driver']):
        drv_laps = laps[laps['Driver'] == drv]
        times = drv_laps['LapTimeSeconds'].tolist()
        if len(times) < 3:
            continue
        team = drv_laps['Team'].iloc[0] if 'Team' in drv_laps.columns else ''
        entry = {
            'driver': drv,
            'team': team,
            'color': get_driver_color(drv),
            'lap_times_seconds': [round(float(t), 3) for t in times],
            'lap_count': len(times),
        }
        entry.update(_quartiles(times))
        data_list.append(entry)

    data_list.sort(key=lambda d: d['median'])

    try:
        store_data_dict_to_mongo(
            year=y, round_nr=r, session_name=e, event_name=event_name,
            data_type='driver_pace', data=data_list, version='v1',
        )
    except Exception as err:
        print(f"Warning: Failed to store to MongoDB: {err}")

    _render_plot(data_list, y, event_name, e, location, name)
    return location + "/" + name


def DriverPaceData(y, r, e, store_to_mongo=True):
    cached_result = get_plot_data_from_mongo(y, r, e, 'driver_pace')
    if cached_result:
        print("Using cached Driver Pace data from MongoDB (v1)")
        return cached_result['data']

    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()
    event_name = session.event['EventName']

    laps = session.laps.pick_quicklaps(threshold=1.07)
    try:
        laps = laps.pick_accurate()
    except Exception:
        pass
    laps = laps.copy()
    laps['LapTimeSeconds'] = laps['LapTime'].dt.total_seconds()
    laps = laps.dropna(subset=['LapTimeSeconds'])

    data_list = []
    for drv in pd.unique(laps['Driver']):
        drv_laps = laps[laps['Driver'] == drv]
        times = drv_laps['LapTimeSeconds'].tolist()
        if len(times) < 3:
            continue
        team = drv_laps['Team'].iloc[0] if 'Team' in drv_laps.columns else ''
        entry = {
            'driver': drv,
            'team': team,
            'color': get_driver_color(drv),
            'lap_times_seconds': [round(float(t), 3) for t in times],
            'lap_count': len(times),
        }
        entry.update(_quartiles(times))
        data_list.append(entry)

    data_list.sort(key=lambda d: d['median'])

    if store_to_mongo:
        try:
            store_data_dict_to_mongo(
                year=y, round_nr=r, session_name=e, event_name=event_name,
                data_type='driver_pace', data=data_list, version='v1',
            )
        except Exception as err:
            print(f"Warning: Failed to store to MongoDB: {err}")

    return data_list
