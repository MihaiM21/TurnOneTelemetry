"""
Lap Time Analysis (V1 / FastF1).

Three stacked panels comparing two drivers' fastest laps along distance:
  1. Speed
  2. Delta time (cumulative gap of d2 relative to d1; reference = d1)
  3. Throttle

The delta time is computed manually by interpolating each driver's
cumulative lap time onto the reference driver's distance axis. This avoids
the deprecated `fastf1.utils.delta_time` and keeps the math identical to the
V2 (livetiming) sibling.
"""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

from src.services.plotting import output as dirOrg
from src.ingestion import fastf1_client as data_aqcuisition
from src.services.plotting import theme as setup_theme
from src.services.plotting.colors import get_driver_color
from src.repositories.plots import store_data_dict_to_mongo, get_plot_data_from_mongo

DATA_TYPE = 'lap_time_analysis'


def _init(y, e, d1, d2, session):
    event_name = session.event['EventName']
    dirOrg.checkForFolder(str(y) + "/" + event_name + "/" + e)
    location = "outputs/plots/" + str(y) + "/" + event_name + "/" + e
    name = f"Lap Time Analysis {y} {event_name} {session.name} {d1} vs {d2}.png"
    return location, name, name.replace("png", "json")


def _lap_time_seconds(telemetry: pd.DataFrame) -> pd.Series:
    """Cumulative lap time (seconds from the start of the lap)."""
    return (telemetry['Time'] - telemetry['Time'].iloc[0]).dt.total_seconds()


def _compute_delta(ref_dist, ref_time, cmp_dist, cmp_time):
    """
    Delta time of the compare lap relative to the reference lap, sampled on the
    reference distance axis. Positive => compare driver is behind (slower).
    """
    ref_dist = np.asarray(ref_dist, dtype=float)
    ref_time = np.asarray(ref_time, dtype=float)
    cmp_dist = np.asarray(cmp_dist, dtype=float)
    cmp_time = np.asarray(cmp_time, dtype=float)

    # np.interp needs ascending x; lap distance is monotonic along the lap.
    cmp_on_ref = np.interp(ref_dist, cmp_dist, cmp_time)
    return cmp_on_ref - ref_time


def _build_data(session, d1, d2, y, e):
    laps = session.laps
    color1 = get_driver_color(d1)
    color2 = get_driver_color(d2)

    fastest1 = laps.pick_driver(d1).pick_fastest()
    fastest2 = laps.pick_driver(d2).pick_fastest()

    tel1 = fastest1.get_car_data().add_distance()
    tel2 = fastest2.get_car_data().add_distance()

    tel1['LapTime'] = _lap_time_seconds(tel1)
    tel2['LapTime'] = _lap_time_seconds(tel2)

    telemetry_data = []
    for tel, drv in [(tel1, d1), (tel2, d2)]:
        for _, row in tel.iterrows():
            telemetry_data.append({
                'distance': float(row['Distance']),
                'speed': float(row['Speed']),
                'throttle': float(row['Throttle']),
                'lap_time': float(row['LapTime']),
                'driver': drv,
            })

    # Reference is d1; delta is the gap of d2 vs d1 along d1's distance axis.
    delta = _compute_delta(
        tel1['Distance'], tel1['LapTime'], tel2['Distance'], tel2['LapTime']
    )
    delta_data = [
        {'distance': float(dist), 'delta': float(dlt)}
        for dist, dlt in zip(tel1['Distance'], delta)
    ]

    return {
        'driver1': d1,
        'driver2': d2,
        'driver1_color': color1,
        'driver2_color': color2,
        'driver1_laptime': float(fastest1['LapTime'].total_seconds())
        if pd.notna(fastest1['LapTime']) else None,
        'driver2_laptime': float(fastest2['LapTime'].total_seconds())
        if pd.notna(fastest2['LapTime']) else None,
        'reference_driver': d1,
        'telemetry': telemetry_data,
        'delta': delta_data,
        'session_info': {
            'year': y,
            'event': e,
            'event_name': session.event['EventName'],
            'session_name': session.name,
        },
    }


def _generate_plot(data, location, name):
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

    plt.savefig(location + "/" + name)
    plt.close()


def LapTimeAnalysisPlot(y, r, e, d1, d2):
    cached = get_plot_data_from_mongo(y, r, e, DATA_TYPE, version='v1')

    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()
    setup_theme.setup_turnone_theme()
    location, name, _ = _init(y, e, d1, d2, session)

    if cached:
        data = cached['data']
        # Only reuse cache if it's the same driver pairing.
        if data.get('driver1') != d1 or data.get('driver2') != d2:
            data = None
    else:
        data = None

    if data is None:
        data = _build_data(session, d1, d2, y, e)
        try:
            store_data_dict_to_mongo(
                year=y, round_nr=r, session_name=e,
                event_name=session.event['EventName'],
                data_type=DATA_TYPE, data=data, version='v1',
            )
        except Exception as ex:
            print(f"Warning: Failed to store to MongoDB: {ex}")

    _generate_plot(data, location, name)
    return location + "/" + name


def LapTimeAnalysisData(y, r, e, d1, d2, store_to_mongo=True):
    cached = get_plot_data_from_mongo(y, r, e, DATA_TYPE, version='v1')
    if cached and cached['data'].get('driver1') == d1 and cached['data'].get('driver2') == d2:
        return cached['data']

    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()
    setup_theme.setup_turnone_theme()

    data = _build_data(session, d1, d2, y, e)

    if store_to_mongo:
        try:
            store_data_dict_to_mongo(
                year=y, round_nr=r, session_name=e,
                event_name=session.event['EventName'],
                data_type=DATA_TYPE, data=data, version='v1',
            )
        except Exception as ex:
            print(f"Warning: Failed to store to MongoDB: {ex}")

    return data
