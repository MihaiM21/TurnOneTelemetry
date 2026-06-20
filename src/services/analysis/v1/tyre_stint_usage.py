"""Tyre stint usage (V1, FastF1-backed).

Horizontal stint strategy chart: per-driver stacked bars showing each stint's
compound and lap range, built from `session.laps[['Driver','Stint','Compound',
'LapNumber','TyreLife']]`.
"""
import pandas as pd
import matplotlib.image as mpimg
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

from src.ingestion import fastf1_client as data_aqcuisition
from src.repositories.plots import get_plot_data_from_mongo, store_data_dict_to_mongo
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


def _init(y, e, session):
    event_name = session.event['EventName']
    dirOrg.checkForFolder(f"{y}/{event_name}/{e}")
    location = f"outputs/plots/{y}/{event_name}/{e}"
    name = f"Tyre stint usage {y} {event_name} {e}.png"
    return location, name, event_name


def _finishing_order(session) -> dict:
    """Returns {driver_abbreviation: finishing_position} from session.results."""
    order = {}
    try:
        results = session.results
        if results is not None and not results.empty:
            for _, row in results.iterrows():
                abbr = row.get('Abbreviation')
                pos = row.get('Position')
                if abbr is not None and pos is not None and not pd.isna(pos):
                    order[str(abbr)] = int(pos)
    except Exception as err:
        print(f"Warning: could not read finishing order: {err}")
    return order


def _build_records(session) -> list:
    laps = session.laps
    if laps is None or laps.empty:
        return []

    finishing_order = _finishing_order(session)

    records = []
    for driver, drv_laps in laps.groupby('Driver'):
        team = drv_laps['Team'].iloc[0] if 'Team' in drv_laps.columns else 'Unknown'
        position = finishing_order.get(str(driver), 99)
        for stint_nr, stint_laps in drv_laps.groupby('Stint'):
            compound = str(stint_laps['Compound'].dropna().iloc[0]).upper() \
                if stint_laps['Compound'].notna().any() else 'UNKNOWN'
            start_lap = int(stint_laps['LapNumber'].min())
            end_lap = int(stint_laps['LapNumber'].max())
            tyre_life_end = stint_laps['TyreLife'].dropna()
            tyre_life_end = int(tyre_life_end.max()) if not tyre_life_end.empty else None
            records.append({
                'driver': str(driver),
                'team': str(team),
                'position': position,
                'stint_number': int(stint_nr),
                'compound': compound,
                'start_lap': start_lap,
                'end_lap': end_lap,
                'lap_count': end_lap - start_lap + 1,
                'tyre_life_end': tyre_life_end,
                'color': get_compound_color(compound),
            })

    records.sort(key=lambda r: (r['position'], r['driver'], r['stint_number']))
    return records


def _render_plot(records: list, y: int, event_name: str, session_name: str) -> str:
    setup_theme.setup_turnone_theme()

    event_folder = event_name.replace(' ', '') if isinstance(event_name, str) else event_name
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f"Tyre stint usage {y} {event_name} {session_name}.png"

    # Drivers ordered by finishing position (P1 first); records are pre-sorted.
    driver_pos = {}
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


def TyreStintUsageData(y, r, e, store_to_mongo=True):
    cached = get_plot_data_from_mongo(y, r, e, DATA_TYPE, version='v1')
    if cached:
        print("Using cached Tyre Stint Usage data from MongoDB (v1)")
        return cached['data']

    session = data_aqcuisition.SessionLoader(y, r, e).get_session()
    records = _build_records(session)

    if store_to_mongo and records:
        try:
            store_data_dict_to_mongo(
                year=y, round_nr=r, session_name=e,
                event_name=session.event['EventName'],
                data_type=DATA_TYPE, data=records, version='v1',
            )
            print("✓ Tyre stint usage cached to MongoDB (v1)")
        except Exception as err:
            print(f"Warning: Failed to store to MongoDB: {err}")

    return records


def TyreStintUsagePlot(y, r, e):
    cached = get_plot_data_from_mongo(y, r, e, DATA_TYPE, version='v1')
    if cached:
        event_name = cached['metadata']['event_name']
        return _render_plot(cached['data'], y, event_name, e)

    session = data_aqcuisition.SessionLoader(y, r, e).get_session()
    event_name = session.event['EventName']
    records = _build_records(session)

    if records:
        try:
            store_data_dict_to_mongo(
                year=y, round_nr=r, session_name=e, event_name=event_name,
                data_type=DATA_TYPE, data=records, version='v1',
            )
        except Exception as err:
            print(f"Warning: Failed to store to MongoDB: {err}")

    return _render_plot(records, y, event_name, e)
