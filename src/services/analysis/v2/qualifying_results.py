import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import to_hex, to_rgb
from typing import List, Dict, Optional, Union

from src.services.plotting import canvas as cv
from src.services.plotting.canvas import add_legacy_watermark
from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.repositories.plots import store_data_dict_to_mongo, get_plot_data_from_mongo
from src.services.plotting.colors import get_team_color
from src.ingestion.static_client import F1StaticClient
from src.services.analysis.v2._helpers import (
    format_lap_time, get_qualifying_classification, get_driver_team_from_list
)
from src.core.logging import get_logger

logger = get_logger(__name__)


def _init(y: int, event_name: str, session_name: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f'{y} {event_name} {session_name} results.png'
    return location, name, name.replace('png', 'json')


def _process_data(base_url: str, client: F1StaticClient) -> List[Dict]:
    driver_info = get_driver_team_from_list(base_url, client)
    df = get_qualifying_classification(base_url, client)

    if df.empty:
        return []

    pole_time = df.iloc[0]['LapTime']

    results = []
    for _, row in df.iterrows():
        drv_num = row['DriverNum']
        info = driver_info.get(drv_num, {})
        tla = info.get('tla', drv_num)
        team = info.get('team', 'Unknown')
        color = get_team_color(team)
        # Ordering comes from classification Position, delta from raw LapTime --
        # a driver eliminated early on a fast banker lap can (rarely) have a
        # quicker recorded LapTime than the actual pole-sitter. Clamp at zero
        # rather than show a nonsensical negative gap.
        delta = max(0.0, round(row['LapTime'] - pole_time, 3))

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

    add_legacy_watermark(fig, 575, 575, alpha=0.6, zorder=3)

    setup_theme.add_glow(ax)
    plt.savefig(f"{location}/{name}")
    plt.close()


# Rows shown per format. The grid is 20-22 cars; a story is watched on a phone,
# so it keeps the part of the order the eye goes to first.
_MAX_ROWS = {"story": 15}

_SESSION_LONG = {
    "Q": "Qualifying", "QUALIFYING": "Qualifying", "SQ": "Sprint Qualifying",
    "SPRINT QUALIFYING": "Sprint Qualifying", "SPRINT SHOOTOUT": "Sprint Shootout",
}


def _session_long(code: str) -> str:
    return _SESSION_LONG.get(str(code).strip().upper(), str(code))


def _formatted_path(location: str, name: str, fmt_name: str) -> str:
    """Legacy file name with the format suffix inserted before ``.png``."""
    stem = name[:-4] if name.lower().endswith(".png") else name
    return f"{location}/{stem}{cv.format_suffix(fmt_name)}.png"


def legible_color(color: str, floor: float = 0.32) -> str:
    """Lift a colour that would vanish on the dark canvas (a near-black team colour) towards white."""
    r, g, b = to_rgb(color)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if lum >= floor:
        return color
    mix = (floor - lum) / (1.0 - lum)
    return to_hex((r + (1 - r) * mix, g + (1 - g) * mix, b + (1 - b) * mix))


def _team_color(row: Dict, y: int) -> str:
    color = get_team_color(row.get("Team", "Unknown"), y)
    return color if color != "#FFFFFF" or not row.get("Color") else row["Color"]


def _render_formatted(data: List[Dict], fmt: str, y: int, event_name: str, session_name: str,
                      location: Optional[str] = None, name: Optional[str] = None) -> str:
    """Social-format gap-to-pole ranking.

    Horizontal bars, pole at the top, the gap at the end of each bar and the
    pole lap time on P1. Codes in team colour. Numbers only, no sentences.
    """
    canvas_fmt = cv.get_format(fmt)
    if location is None or name is None:
        location, name, _ = _init(y, event_name, session_name)

    rows = data[:_MAX_ROWS.get(canvas_fmt.name, len(data))]
    n = len(rows)
    gaps = [float(r["LapTimeDelta"]) for r in rows]
    colors = [_team_color(r, y) for r in rows]

    fig = cv.new_canvas(canvas_fmt)
    cv.add_header(fig, canvas_fmt, "Gap to pole", f"{y} {event_name}  ·  {_session_long(session_name)}")
    cv.add_footer(fig, canvas_fmt)
    cv.add_watermark(fig, canvas_fmt)

    gs = cv.safe_gridspec(fig, canvas_fmt, 1)
    ax = fig.add_subplot(gs[0])
    ax.barh(range(n), gaps, color=colors, height=0.68, zorder=3)

    # Leave room at the right of the longest bar for its "+x.xxx" label.
    base = canvas_fmt.base_fontsize
    axes_px = ax.get_position().width * canvas_fmt.width_px
    reserve_px = 7 * base * cv.DESIGN_DPI / 72.0 * 0.62 + 14
    longest = max(max(gaps, default=0.0), 0.05)
    ax.set_xlim(0, longest * axes_px / max(axes_px - reserve_px, 1.0))
    ax.set_ylim(n - 0.5, -0.5)

    for i, row in enumerate(rows):
        text = row["LapTime"] if i == 0 else f"+{gaps[i]:.3f}"
        ax.annotate(text, (gaps[i], i), xytext=(6, 0), textcoords="offset points", ha="left", va="center",
                    fontsize=base, fontweight="bold", color=cv.TEXT if i else colors[0], zorder=4)

    ax.set_yticks(range(n))
    ax.set_yticklabels([f"{i + 1}  {r['Driver']}" for i, r in enumerate(rows)], fontweight="bold")
    for tick, color in zip(ax.get_yticklabels(), colors):
        tick.set_color(legible_color(color))
    ax.set_xlabel("Gap to pole (s)")
    cv.style_axis(ax, canvas_fmt)
    ax.grid(False, axis="y")
    ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0)

    return cv.save_png(fig, _formatted_path(location, name, canvas_fmt.name), canvas_fmt)


def QualiResultsPlot(y: int, identifier: Union[int, str], e: str, fmt: Optional[str] = None) -> str:
    if fmt is not None:
        cv.get_format(fmt)  # fail fast on a bad name, before any network work
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
        base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)
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

    if fmt is not None:
        return _render_formatted(data, fmt, y, event_name, e, location, name)

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

    base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)
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
    logger.info("Testing V2 Qualifying Results...")
    try:
        plot_path = QualiResultsPlot(2023, 14, "Qualifying")
        logger.info("Plot: %s", plot_path)
        data = QualiResultsData(2023, 14, "Qualifying")
        logger.info("Drivers: %s", len(data))
        if data:
            logger.info("P1: %s", data[0])
    except Exception as e:
        logger.error("Error: %s", e)
        import traceback
        traceback.print_exc()
