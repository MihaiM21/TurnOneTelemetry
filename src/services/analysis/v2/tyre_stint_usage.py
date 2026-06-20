"""Tyre stint usage (V2, livetiming-backed).

Stint strategy timeline: one horizontal bar per driver, segmented by compound,
showing lap ranges per stint across a race session.
"""
from typing import Dict, List, Union

import matplotlib.image as mpimg
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

from src.ingestion.static_client import F1StaticClient
from src.repositories.plots import get_plot_data_from_mongo, store_data_dict_to_mongo
from src.services.analysis.v2._helpers import (
    extract_stints,
    get_driver_team_from_list,
    get_finishing_order,
)
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.services.plotting.colors import compound_colors, get_compound_color


DATA_TYPE = "tyre_stint_usage"


def _add_watermark(fig):
    """Place the TurnOne logo faintly in the bottom-right corner.

    The full-width strategy bars cover the whole axes, so a fixed-pixel
    centre watermark tints the bars. Anchor it to the bottom-right corner at
    low opacity instead, computed from the figure size so it scales.
    """
    try:
        logo = mpimg.imread('assets/images/logo mic.png')
        dpi = fig.get_dpi()
        fw_px = fig.get_size_inches()[0] * dpi
        xo = max(0, fw_px - logo.shape[1] - 15)
        fig.figimage(logo, xo, 15, zorder=3, alpha=0.30)
    except Exception:
        pass


def _init(y: int, event_name: str, session_name: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f"Tyre stint usage {y} {event_name} {session_name}.png"
    return location, name


def _build_records(base_url: str, client: F1StaticClient) -> List[Dict]:
    driver_info = get_driver_team_from_list(base_url, client)
    finishing_order = get_finishing_order(base_url, client)

    records: List[Dict] = []
    for driver_num, info in driver_info.items():
        stints = extract_stints(base_url, client, driver_num)
        if not stints:
            continue

        position = finishing_order.get(str(driver_num), 99)
        for stint in stints:
            records.append({
                'driver': info['tla'],
                'team': info['team'],
                'position': position,
                'stint_number': stint['stint_number'],
                'compound': stint['compound'],
                'start_lap': stint['start_lap'],
                'end_lap': stint['end_lap'],
                'lap_count': stint['lap_count'],
                'tyre_life_end': stint['tyre_life_end'],
                'color': get_compound_color(stint['compound']),
            })

    records.sort(key=lambda r: (r['position'], r['driver'], r['stint_number']))
    return records


def _render_plot(records: List[Dict], y: int, event_name: str, session_name: str) -> str:
    setup_theme.setup_turnone_theme()
    location, name = _init(y, event_name, session_name)

    # Drivers ordered by finishing position (P1 first); records are pre-sorted.
    driver_pos: Dict[str, int] = {}
    for r in records:
        driver_pos.setdefault(r['driver'], r.get('position', 99))
    drivers = sorted(driver_pos, key=lambda d: (driver_pos[d], d))

    max_lap = max((r['end_lap'] for r in records), default=1)

    fig, ax = plt.subplots(figsize=(14, max(8, 0.45 * len(drivers) + 2)), layout='constrained')

    # P1 at the top: highest y-position goes to the first finisher.
    n = len(drivers)
    y_positions = {drv: n - 1 - i for i, drv in enumerate(drivers)}
    for r in records:
        y_pos = y_positions[r['driver']]
        width = r['end_lap'] - r['start_lap'] + 1
        ax.barh(y_pos, width, left=r['start_lap'] - 1,
                color=r['color'], edgecolor='#0d0d0d', linewidth=0.5)

    ax.set_yticks([y_positions[d] for d in drivers])
    ax.set_yticklabels(drivers, fontsize=11)
    ax.set_xlabel("Lap", fontsize=12)
    ax.set_xlim(0, max_lap)
    ax.set_axisbelow(True)
    ax.grid(axis='x', alpha=0.3)

    seen = sorted({r['compound'] for r in records})
    legend_handles = [mpatches.Patch(color=compound_colors.get(c, "#777777"), label=c) for c in seen]
    ax.legend(handles=legend_handles, loc='upper center', bbox_to_anchor=(0.5, 1.06),
              ncol=len(legend_handles), frameon=False, fontsize=10)

    _add_watermark(fig)

    plt.suptitle(f"Tyre stint usage\n{y} {event_name} {session_name}")
    plt.savefig(f"{location}/{name}")
    plt.close(fig)
    return f"{location}/{name}"


def TyreStintUsageData(y: int, identifier: Union[int, str], e: str,
                       store_to_mongo: bool = True) -> List[Dict]:
    cached = get_plot_data_from_mongo(y, identifier, e, DATA_TYPE, version='v2')
    if cached:
        print("Using cached Tyre Stint Usage data from MongoDB (v2)")
        return cached['data']

    client = F1StaticClient()
    event_info = client.get_event_info(y, identifier)
    if not event_info:
        raise ValueError(f"Event not found: {y} {identifier}")
    event_name = event_info['name']
    round_nr = event_info['round_nr']

    base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)
    if not base_url:
        raise ValueError(f"Session not found: {y} {event_name} {e}")

    records = _build_records(base_url, client)

    if store_to_mongo and records:
        try:
            store_data_dict_to_mongo(
                year=y, round_nr=round_nr, session_name=e, event_name=event_name,
                data_type=DATA_TYPE, data=records, version='v2',
            )
            print("✓ Tyre stint usage cached to MongoDB (v2)")
        except Exception as err:
            print(f"Warning: Failed to store to MongoDB: {err}")

    return records


def TyreStintUsagePlot(y: int, identifier: Union[int, str], e: str) -> str:
    cached = get_plot_data_from_mongo(y, identifier, e, DATA_TYPE, version='v2')
    if cached:
        event_name = cached['metadata']['event_name']
        return _render_plot(cached['data'], y, event_name, e)

    client = F1StaticClient()
    event_info = client.get_event_info(y, identifier)
    if not event_info:
        raise ValueError(f"Event not found: {y} {identifier}")
    event_name = event_info['name']

    records = TyreStintUsageData(y, identifier, e, store_to_mongo=True)
    return _render_plot(records, y, event_name, e)
