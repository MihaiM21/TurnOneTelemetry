import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import ListedColormap
import matplotlib.patches as mpatches
from typing import Any, Dict, Optional, Tuple, Union

from src.services.plotting import canvas as cv
from src.services.plotting.canvas import add_legacy_watermark
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.repositories.plots import store_data_dict_to_mongo, get_plot_data_from_mongo
from src.services.plotting.colors import get_driver_color, get_driver_team, pair_colors
from src.ingestion.static_client import F1StaticClient
from src.ingestion.circuits_loader import get_circuit_data_file
from src.services.analysis.v2._helpers import (
    get_all_driver_codes, get_fastest_lap_windows,
    extract_telemetry_for_lap, extract_position_for_lap, compute_distance,
    build_session_store,
)
from src.core.logging import get_logger

logger = get_logger(__name__)


def _init(y: int, event_name: str, session_name: str, d1: str, d2: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f'{event_name} {session_name} {y} {d1} vs {d2}.png'
    return location, name, name.replace('png', 'json')


def _get_driver_track_data(base_url: str, client: F1StaticClient,
                            driver_tla: str, store=None) -> pd.DataFrame:
    """Returns X/Y/Distance/Speed DataFrame for a driver's fastest lap."""
    driver_codes = get_all_driver_codes(base_url, client)
    tla_to_num = {v.upper(): k for k, v in driver_codes.items()}

    driver_num = tla_to_num.get(driver_tla.upper())
    if not driver_num:
        logger.warning("Driver %s not found in session", driver_tla)
        return pd.DataFrame()

    df_windows = get_fastest_lap_windows(base_url, client, target_driver_num=driver_num, store=store)
    if df_windows.empty:
        logger.warning("No fastest lap found for %s", driver_tla)
        return pd.DataFrame()

    row = df_windows.iloc[0]
    start_t, end_t = row['StartTime'], row['EndTime']

    df_pos = extract_position_for_lap(base_url, client, driver_num, start_t, end_t, store=store)
    if df_pos.empty:
        logger.warning("No position data for %s", driver_tla)
        return pd.DataFrame()

    df_pos = compute_distance(df_pos)

    df_tel = extract_telemetry_for_lap(base_url, client, driver_num, start_t, end_t,
                                       channels=['2'], store=store)

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
                   d1: str, d2: str, y: int, event_name: str, e: str, store=None) -> Dict:
    color1 = get_driver_color(d1)
    color2 = get_driver_color(d2)

    df1 = _get_driver_track_data(base_url, client, d1, store=store)
    df2 = _get_driver_track_data(base_url, client, d2, store=store)

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

    add_legacy_watermark(plt.gcf(), 575, 575, alpha=0.6, zorder=3)

    plt.savefig(f"{location}/{name}")
    plt.close()


# ----------------------------------------------------------------------
# Social formats (fmt=...)
# ----------------------------------------------------------------------
_SESSION_LONG = {"Q": "Qualifying", "R": "Race", "S": "Sprint", "SQ": "Sprint Qualifying",
                 "FP1": "Practice 1", "FP2": "Practice 2", "FP3": "Practice 3"}
_NUM_MINISECTORS = 25


def _formatted_name(name: str, fmt_name: str) -> str:
    """The legacy file name with the format suffix inserted before ``.png``."""
    return f"{name[:-len('.png')]}{cv.format_suffix(fmt_name)}.png"


def _circuit_rotation(circuit_key: Any, year: int) -> float:
    """Display rotation (degrees) of the circuit, ``0`` when unknown. Never raises.

    Same lookup ``get_circuit_info_for_session`` performs, keyed by the
    ``circuit_key`` the event resolver already carries, so no session store (and
    no stream download) is needed just to orient the map.
    """
    if circuit_key is None:
        return 0.0
    for candidate in dict.fromkeys((year, 2026, 2025, 2024)):
        try:
            circuit = get_circuit_data_file(circuit_key, candidate)
        except Exception:
            continue
        if isinstance(circuit, dict):
            try:
                return float(circuit.get("rotation") or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _track_geometry(data: Dict, rotation: float) -> Optional[Dict[str, Any]]:
    """Rotated racing line of driver 1 with the winner of every segment.

    ``telemetry`` holds both drivers' points interleaved by distance; one
    driver's trace is enough for the outline, and each point already knows which
    driver won its minisector.
    """
    telemetry = pd.DataFrame(data["telemetry"])
    if telemetry.empty:
        return None
    own = telemetry[telemetry["driver"] == data["driver1"]]
    if len(own) < 2:
        own = telemetry
    own = own.sort_values("distance")
    x = own["x"].to_numpy(dtype=float)
    y = own["y"].to_numpy(dtype=float)
    theta = np.deg2rad(rotation)
    xr = x * np.cos(theta) - y * np.sin(theta)
    yr = x * np.sin(theta) + y * np.cos(theta)
    winners = own["fastest_driver_int"].to_numpy().astype(int)

    per_sector = telemetry.groupby("minisector")["fastest_driver_int"].first()
    wins = (int((per_sector == 1).sum()), int((per_sector == 2).sum()))
    return {"x": xr, "y": yr, "winner": winners, "wins": wins,
            "total": max(len(per_sector), _NUM_MINISECTORS)}


def _draw_track(ax, geo: Dict[str, Any], color1: str, color2: str, lw: float) -> None:
    ax.set_axis_off()
    pts = np.column_stack([geo["x"], geo["y"]]).reshape(-1, 1, 2)
    segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
    colors = [color1 if w == 1 else color2 for w in geo["winner"][:-1]]
    # A dark under-stroke keeps the line legible where the outline crosses itself.
    ax.add_collection(LineCollection(segs, colors="#0d0d0d", linewidths=lw + 4, capstyle="round", zorder=1))
    ax.add_collection(LineCollection(segs, colors=colors, linewidths=lw, capstyle="round", zorder=2))
    ax.plot(geo["x"][0], geo["y"][0], marker="s", color="#E9E9E9", ms=lw * 1.2, zorder=3)
    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.margins(0.05)


def _draw_slot(fig, fmt: cv.CanvasFormat, slot: Tuple[float, float, float, float],
               code: str, color: str, wins: int, total: int) -> None:
    """Driver plate on the left of ``slot`` and the sectors-won number on its right."""
    x0, y0, w, h = slot
    cv.add_driver_plate(fig, fmt, (x0, y0, w * 0.6, h), code, color, detail=f"of {total} sectors")
    fig.text(x0 + w, y0 + h * 0.5, str(wins), ha="right", va="center",
             fontsize=fmt.base_fontsize * (4.0 if fmt.is_vertical else 3.6), fontweight="bold", color=color)


def _render_formatted(data: Dict, y: int, event_name: str, session_name: str,
                      location: str, name: str, fmt_name: str, rotation: float = 0.0) -> str:
    """Track-dominance map recomposed for a social format (numbers and labels only)."""
    fmt = cv.get_format(fmt_name)
    d1, d2 = data["driver1"], data["driver2"]
    color1, color2 = pair_colors(get_driver_color(d1, y), get_driver_color(d2, y), get_driver_team(d2, y))
    geo = _track_geometry(data, rotation)
    if geo is None:
        return ""

    fig = cv.new_canvas(fmt)
    session_long = _SESSION_LONG.get(session_name, session_name)
    cv.add_header(fig, fmt, f"{d1} vs {d2}", f"{y} {event_name}  ·  {session_long}")
    cv.add_footer(fig, fmt)
    cv.add_watermark(fig, fmt)

    left, bottom, right, top = fmt.safe
    content_top = top - cv.header_height(fmt)
    content_bottom = bottom + 0.05 * (top - bottom)
    width = right - left
    height = content_top - content_bottom
    gap = 0.02

    wins1, wins2 = geo["wins"]
    total = geo["total"]
    lw = 7.0 if fmt.is_vertical else 8.0

    if fmt.name == "landscape":
        map_w = width * 0.64
        ax = fig.add_axes((left, content_bottom, map_w, height))
        col_x = left + map_w + width * 0.03
        col_w = right - col_x
        slot_h = height * 0.26
        slots = [(col_x, content_bottom + height * 0.56, col_w, slot_h),
                 (col_x, content_bottom + height * 0.16, col_w, slot_h)]
    elif fmt.name == "story":
        slot_h = height * 0.11
        plates_h = 2 * slot_h + gap
        ax = fig.add_axes((left, content_bottom + plates_h + 0.03, width, height - plates_h - 0.03))
        slots = [(left, content_bottom + slot_h + gap, width, slot_h),
                 (left, content_bottom, width, slot_h)]
    else:
        slot_h = height * (0.2 if fmt.name == "portrait" else 0.24)
        ax = fig.add_axes((left, content_bottom + slot_h + 0.03, width, height - slot_h - 0.03))
        half = (width - 0.03) / 2
        slots = [(left, content_bottom, half, slot_h), (left + half + 0.03, content_bottom, half, slot_h)]

    _draw_track(ax, geo, color1, color2, lw)
    _draw_slot(fig, fmt, slots[0], d1, color1, wins1, total)
    _draw_slot(fig, fmt, slots[1], d2, color2, wins2, total)

    path = f"{location}/{_formatted_name(name, fmt_name)}"
    return cv.save_png(fig, path, fmt)


def TrackComparisonPlot(y: int, identifier: Union[int, str], e: str, d1: str, d2: str,
                        fmt: Optional[str] = None) -> str:
    if fmt:
        cv.get_format(fmt)  # ValueError before any cache or network access
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
        base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)
        if not base_url:
            return ""
        store = build_session_store(y, identifier, e, client)
        data = _process_data(base_url, client, d1, d2, y, event_name, e, store=store)
        if data and data.get('telemetry'):
            store_data_dict_to_mongo(
                year=y, round_nr=round_nr, session_name=e, event_name=event_name,
                data_type=cache_key, data=data, version='v2'
            )

    if not data or not data.get('telemetry'):
        return ""

    setup_theme.setup_turnone_theme()
    if fmt:
        rotation = _circuit_rotation(event_info.get('circuit_key'), y)
        return _render_formatted(data, y, event_name, e, location, name, fmt, rotation)
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

    base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)
    if not base_url:
        return {}

    store = build_session_store(y, identifier, e, client)
    data = _process_data(base_url, client, d1, d2, y, event_name, e, store=store)

    if store_to_mongo and data and data.get('telemetry'):
        store_data_dict_to_mongo(
            year=y, round_nr=round_nr, session_name=e, event_name=event_name,
            data_type=cache_key, data=data, version='v2'
        )
    return data


if __name__ == "__main__":
    logger.info("Testing V2 Track Comparison...")
    try:
        plot_path = TrackComparisonPlot(2023, 14, "Qualifying", "VER", "NOR")
        logger.info("Plot: %s", plot_path)
        data = TrackComparisonData(2023, 14, "Qualifying", "VER", "NOR")
        logger.info("Telemetry points: %s", len(data.get('telemetry', [])))
    except Exception as e:
        logger.error("Error: %s", e)
        import traceback
        traceback.print_exc()
