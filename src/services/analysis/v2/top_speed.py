import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from typing import Dict, List, Tuple, Optional, Union
from collections import defaultdict

from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme
from src.services.plotting.colors import team_colors, teams
from src.repositories.plots import store_plot_data_to_mongo, get_plot_data_from_mongo
from src.ingestion.static_client import F1StaticClient
from src.services.analysis.v2._helpers import build_session_store
from src.domain.mappings import get_driver_team_mapping
from src.core.exceptions import (
    DataNotAvailableError,
    SessionNotFoundError,
    UpstreamUnavailableError,
)
from src.core.logging import get_logger

logger = get_logger(__name__)


def _init(y: int, event_name: str, session_name: str, data_source: str) -> Tuple[str, str, str]:
    """
    Initialize paths and filenames for output files.
    
    Args:
        y: Year
        event_name: Event name (e.g., "Italian Grand Prix")
        session_name: Session name (e.g., "Race")
        data_source: Either "Telemetry" or "SpeedTrap"
    """
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f'Top speed comparison {data_source} {y} {event_name} {session_name}.png'
    name_json = name.replace("png", "json")
    return location, name, name_json


# ============================================================================
# DATA EXTRACTION FUNCTIONS
# ============================================================================
def extract_top_speeds_from_telemetry(
    base_url: str,
    client: F1StaticClient,
    driver_to_team: Dict[str, str],
    store=None,
) -> Dict[str, float]:
    """
    Extract maximum speed from CarData.z.jsonStream (telemetry)

    When a ``SessionDataStore`` is passed as ``store``, the CarData stream is
    served from its durable cache instead of being re-downloaded.
    """
    car_data_url = base_url + "CarData.z.jsonStream"

    print(f"\nExtracting Telemetry data (CarData): {car_data_url}")
    print("Processing compressed stream (may take 10-20 seconds)...")

    try:
        telemetry_entries = store.car_data() if store is not None else client.parse_compressed_stream(car_data_url)
        
        team_max_speeds = defaultdict(lambda: 0)
        
        for entry in telemetry_entries:
            entries_list = entry.get('Entries', [])
            
            # Normalize: sometimes Entries is a list, sometimes a single object
            if not isinstance(entries_list, list):
                entries_list = [entries_list]

            for item in entries_list:
                cars = item.get('Cars', {})
                for driver_num, driver_data in cars.items():
                    channels = driver_data.get('Channels', {})
                    speed_kmh = channels.get('2', 0)  # Channel 2 = Speed
                    
                    if speed_kmh and speed_kmh > 0:
                        if driver_num in driver_to_team:
                            team = driver_to_team[driver_num]
                            if speed_kmh > team_max_speeds[team]:
                                team_max_speeds[team] = speed_kmh
        
        return dict(team_max_speeds)
        
    except (DataNotAvailableError, SessionNotFoundError, UpstreamUnavailableError):
        raise
    except Exception as e:
        logger.exception("Error extracting telemetry from %s", car_data_url)
        raise UpstreamUnavailableError(
            source="livetiming",
            reason=f"Failed to extract telemetry: {e}",
        ) from e


def extract_top_speeds_from_speed_trap(
    base_url: str,
    client: F1StaticClient,
    driver_to_team: Dict[str, str]
) -> Dict[str, float]:
    """
    Extract official Speed Trap data from TimingData.jsonStream
    """
    timing_url = base_url + "TimingData.jsonStream"
    print(f"\nExtracting Speed Trap data (TimingData): {timing_url}")
    
    try:
        entries = client.parse_jsonstream_simple(timing_url)
        
        team_st_speeds = defaultdict(lambda: 0.0)
        
        for entry in entries:
            if 'Lines' not in entry:
                continue
                
            lines = entry['Lines']
            
            for car_number, data in lines.items():
                # Look for structure: Lines -> Car -> Speeds -> ST (Speed Trap)
                if 'Speeds' in data and 'ST' in data['Speeds']:
                    st_data = data['Speeds']['ST']
                    
                    if 'Value' in st_data:
                        val = st_data['Value']
                        if val and val != "":
                            try:
                                speed = float(val)
                                if car_number in driver_to_team:
                                    team = driver_to_team[car_number]
                                    if speed > team_st_speeds[team]:
                                        team_st_speeds[team] = speed
                            except ValueError:
                                continue

        return dict(team_st_speeds)

    except (DataNotAvailableError, SessionNotFoundError, UpstreamUnavailableError):
        raise
    except Exception as e:
        logger.exception("Error extracting Speed Trap from %s", timing_url)
        raise UpstreamUnavailableError(
            source="livetiming",
            reason=f"Failed to extract Speed Trap data: {e}",
        ) from e


# ============================================================================
# PLOT GENERATION HELPER
# ============================================================================
def _generate_plot(
    teams_list: List[str],
    speeds_list: List[float],
    colors_list: List[str],
    year: int,
    event_name: str,
    session_name: str,
    location: str,
    name: str,
    data_source: str
):
    """
    Generate and save the bar plot
    """
    fig, ax = plt.subplots(figsize=(13, 13), layout='constrained')
    ax.bar(teams_list, speeds_list, color=colors_list)
    
    # Set Y-axis limits (dynamic)
    if speeds_list:
        y_min = min(speeds_list) - 5
        y_max = max(speeds_list) + 5
        ax.set_ylim(y_min, y_max)
    
    # Add speed labels on bars
    for i, (team, speed) in enumerate(zip(teams_list, speeds_list)):
        ax.text(team, int(speed) + 0.5, f"{int(speed)}",
                verticalalignment='bottom',
                horizontalalignment='center',
                color='white', fontsize=16, fontweight="bold")
    
    # Add watermark
    try:
        logo = mpimg.imread('assets/images/logo mic.png')
        fig.figimage(logo, 575, 575, zorder=3, alpha=.6)
    except Exception:
        pass
    
    # Set title
    plt.suptitle(f'Top speed comparison ({data_source})\n{year} {event_name} {session_name}')
    plt.tight_layout()
    
    setup_theme.add_glow(ax)
    plt.savefig(location + "/" + name)
    plt.close()


# ============================================================================
# TELEMETRY FUNCTIONS
# ============================================================================
def TopSpeedPlot_Telemetry(y: int, identifier: Union[int, str], e: str, store_to_mongo: bool = True) -> str:
    """
    Generate top speed plot from telemetry data (CarData)
    
    Args:
        y: Year
        identifier: Round number, Event Key, or Official Name
        e: Session name (e.g., "Race", "Qualifying")
    
    Returns:
        Path to the generated plot
    """
    # Check MongoDB cache first (v2 collection)
    cached_result = get_plot_data_from_mongo(y, identifier, e, 'top_speed_telemetry', version='v2')
    if cached_result:
        cached_data = cached_result['data']
        metadata = cached_result['metadata']
        
        # Generate plot from cached data
        setup_theme.setup_turnone_theme()
        
        event_name = metadata['event_name']
        event_folder = event_name.replace(' ', '')
        dirOrg.checkForFolder(f"{y}/{event_folder}/{e}")
        location = f"outputs/plots/{y}/{event_folder}/{e}"
        name = f'Top speed comparison Telemetry {y} {event_name} {e}.png'
        
        # Convert cached data to DataFrame
        df = pd.DataFrame(cached_data)
        teams_list = df['Team'].tolist()
        speeds_list = df['Top Speed (km/h)'].tolist()
        colors_list = df['Color'].tolist()
        
        _generate_plot(
            teams_list, speeds_list, colors_list,
            y, event_name, e,
            location, name, "Telemetry"
        )
        
        return location + "/" + name
    
    # If not in cache, generate new data
    client = F1StaticClient()
    
    print(f"\nFetching {y} Identifier {identifier} {e}...")
    event_info = client.get_event_info(y, identifier)
    if not event_info:
        raise ValueError(f"Could not find event info for: {y} Identifier {identifier}")
        
    event_name = event_info['name']
    round_nr = event_info['round_nr']

    base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)

    if not base_url:
        raise ValueError(f"Could not find session: {y} Event {event_name} {e}")

    # Setup
    setup_theme.setup_turnone_theme()
    location, name, name_json = _init(y, event_name, e, "Telemetry")

    # Check for existing file
    # path = dirOrg.checkForFile(location, name)
    # if path != "NULL":
    #     return path
    
    # Get driver mapping and extract data
    driver_to_team = get_driver_team_mapping(base_url, client)
    store = build_session_store(y, identifier, e, client)
    telemetry_speeds = extract_top_speeds_from_telemetry(base_url, client, driver_to_team, store=store)
    
    # Prepare plot data
    plot_data = []
    for team, speed in telemetry_speeds.items():
        color = team_colors.get(team, "#FFFFFF")
        plot_data.append({
            'Team': team,
            'Speed': speed,
            'Color': color
        })
    
    # Sort by speed (descending)
    plot_data.sort(key=lambda x: x['Speed'], reverse=True)
    
    teams_list = [d['Team'] for d in plot_data]
    speeds_list = [d['Speed'] for d in plot_data]
    colors_list = [d['Color'] for d in plot_data]
    
    # Store to MongoDB (always when generating new data)
    try:
        from src.repositories.plots import store_data_dict_to_mongo
        store_data_dict_to_mongo(
            year=y,
            round_nr=round_nr,
            session_name=e,
            event_name=event_name,
            data_type='top_speed_telemetry',
            data=plot_data,
            version='v2'
        )
        print(f"✓ Telemetry data cached to MongoDB (v2 collection)")
    except Exception as e:
        print(f"Warning: Failed to store to MongoDB: {e}")

    # Generate plot
    _generate_plot(
        teams_list, speeds_list, colors_list,
        y, event_name, e,
        location, name, "Telemetry"
    )
    
    print(f"\n✓ Telemetry plot saved to: {location}/{name}")
    
    return location + "/" + name


def TopSpeedData_Telemetry(y: int, identifier: Union[int, str], e: str, store_to_mongo: bool = True) -> list:
    """
    Generate top speed JSON data from telemetry (CarData)
    
    Args:
        y: Year
        identifier: Round number, Event Key, or Official Name
        e: Session name (e.g., "Race", "Qualifying")
        store_to_mongo: Whether to store data to MongoDB
    
    Returns:
        List of dictionaries containing top speed data
    """
    # Check MongoDB cache first (v2 collection)
    cached_result = get_plot_data_from_mongo(y, identifier, e, 'top_speed_telemetry', version='v2')
    if cached_result:
        print("Using cached Telemetry Top Speed data from MongoDB (v2 collection)")
        return cached_result['data']
    
    print("No cached Telemetry Top Speed data found in MongoDB, generating new data.")
    
    # Generate new data
    client = F1StaticClient()
    
    print(f"\nFetching {y} Identifier {identifier} {e}...")
    event_info = client.get_event_info(y, identifier)
    if not event_info:
        raise ValueError(f"Could not find event info for: {y} Identifier {identifier}")
        
    event_name = event_info['name']
    round_nr = event_info['round_nr']
    
    base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)

    if not base_url:
        raise ValueError(f"Could not find session: {y} Event {event_name} {e}")
    
    # Setup
    location, name, name_json = _init(y, event_name, e, "Telemetry")
    
    # Get driver mapping and extract data
    driver_to_team = get_driver_team_mapping(base_url, client)
    store = build_session_store(y, identifier, e, client)
    telemetry_speeds = extract_top_speeds_from_telemetry(base_url, client, driver_to_team, store=store)
    
    # Prepare data
    plot_data = []
    for team, speed in telemetry_speeds.items():
        color = team_colors.get(team, "#FFFFFF")
        plot_data.append({
            'Team': team,
            'Top Speed (km/h)': speed,
            'Color': color
        })
    
    # Sort by speed (descending)
    plot_data.sort(key=lambda x: x['Top Speed (km/h)'], reverse=True)
    
    # Store to MongoDB (always when generating new data)
    try:
        from src.repositories.plots import store_data_dict_to_mongo
        store_data_dict_to_mongo(
            year=y,
            round_nr=round_nr,
            session_name=e,
            event_name=event_name,
            data_type='top_speed_telemetry',
            data=plot_data,
            version='v2'
        )
        print(f"✓ Telemetry data cached to MongoDB (v2 collection)")
    except Exception as e:
        print(f"Warning: Failed to store to MongoDB: {e}")
    
    print(f"✓ Telemetry data generated successfully")
    
    return plot_data


# ============================================================================
# SPEED TRAP FUNCTIONS
# ============================================================================
def TopSpeedPlot_SpeedTrap(y: int, identifier: Union[int, str], e: str) -> str:
    """
    Generate top speed plot from Speed Trap data (TimingData)
    
    Args:
        y: Year
        identifier: Round number, Event Key, or Official Name
        e: Session name (e.g., "Race", "Qualifying")
    
    Returns:
        Path to the generated plot
    """
    # Check MongoDB cache first (v2 collection)
    cached_result = get_plot_data_from_mongo(y, identifier, e, 'top_speed_speedtrap', version='v2')
    if cached_result:
        cached_data = cached_result['data']
        metadata = cached_result['metadata']
        
        # Generate plot from cached data
        setup_theme.setup_turnone_theme()
        
        event_name = metadata['event_name']
        event_folder = event_name.replace(' ', '')
        dirOrg.checkForFolder(f"{y}/{event_folder}/{e}")
        location = f"outputs/plots/{y}/{event_folder}/{e}"
        name = f'Top speed comparison SpeedTrap {y} {event_name} {e}.png'
        
        # Convert cached data to DataFrame
        df = pd.DataFrame(cached_data)
        teams_list = df['Team'].tolist()
        speeds_list = df['Top Speed (km/h)'].tolist()
        colors_list = df['Color'].tolist()
        
        _generate_plot(
            teams_list, speeds_list, colors_list,
            y, event_name, e,
            location, name, "Speed Trap"
        )
        
        return location + "/" + name
    
    # If not in cache, generate new data
    client = F1StaticClient()
    
    print(f"\nFetching {y} Identifier {identifier} {e}...")
    event_info = client.get_event_info(y, identifier)
    if not event_info:
        raise ValueError(f"Could not find event info for: {y} Identifier {identifier}")
        
    event_name = event_info['name']
    round_nr = event_info['round_nr']
    
    base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)

    if not base_url:
        raise ValueError(f"Could not find session: {y} Event {event_name} {e}")
    
    # Setup
    setup_theme.setup_turnone_theme()
    location, name, name_json = _init(y, event_name, e, "SpeedTrap")
    
    # Check for existing file
    # path = dirOrg.checkForFile(location, name)
    # if path != "NULL":
    #     return path
    
    # Get driver mapping and extract data
    driver_to_team = get_driver_team_mapping(base_url, client)
    st_speeds = extract_top_speeds_from_speed_trap(base_url, client, driver_to_team)
    
    # Prepare plot data
    plot_data = []
    for team, speed in st_speeds.items():
        color = team_colors.get(team, "#FFFFFF")
        plot_data.append({
            'Team': team,
            'Speed': speed,
            'Color': color
        })
    
    # Sort by speed (descending)
    plot_data.sort(key=lambda x: x['Speed'], reverse=True)
    
    teams_list = [d['Team'] for d in plot_data]
    speeds_list = [d['Speed'] for d in plot_data]
    colors_list = [d['Color'] for d in plot_data]
    
    # Store to MongoDB (always when generating new data)
    try:
        from src.repositories.plots import store_data_dict_to_mongo
        store_data_dict_to_mongo(
            year=y,
            round_nr=round_nr,
            session_name=e,
            event_name=event_name,
            data_type='top_speed_speedtrap',
            data=plot_data,
            version='v2'
        )
        print(f"✓ Speed Trap data cached to MongoDB (v2 collection)")
    except Exception as e:
        print(f"Warning: Failed to store to MongoDB: {e}")

    # Generate plot
    _generate_plot(
        teams_list, speeds_list, colors_list,
        y, event_name, e,
        location, name, "Speed Trap"
    )
    
    print(f"\n✓ Speed Trap plot saved to: {location}/{name}")
    
    return location + "/" + name


def TopSpeedData_SpeedTrap(y: int, identifier: Union[int, str], e: str, store_to_mongo: bool = True) -> list:
    """
    Generate top speed JSON data from Speed Trap (TimingData)
    
    Args:
        y: Year
        identifier: Round number, Event Key, or Official Name
        e: Session name (e.g., "Race", "Qualifying")
        store_to_mongo: Whether to store data to MongoDB
    
    Returns:
        List of dictionaries containing top speed data
    """
    # Check MongoDB cache first (v2 collection)
    cached_result = get_plot_data_from_mongo(y, identifier, e, 'top_speed_speedtrap', version='v2')
    if cached_result:
        print("Using cached Speed Trap Top Speed data from MongoDB (v2 collection)")
        return cached_result['data']
    
    print("No cached Speed Trap Top Speed data found in MongoDB, generating new data.")
    
    # Generate new data
    client = F1StaticClient()
    
    print(f"\nFetching {y} Identifier {identifier} {e}...")
    event_info = client.get_event_info(y, identifier)
    if not event_info:
        raise ValueError(f"Could not find event info for: {y} Identifier {identifier}")
        
    event_name = event_info['name']
    round_nr = event_info['round_nr']
    
    base_url = client.get_event_session_url(y, event_name, e, round_nr=round_nr)

    if not base_url:
        raise ValueError(f"Could not find session: {y} Event {event_name} {e}")
    
    # Setup
    location, name, name_json = _init(y, event_name, e, "SpeedTrap")
    
    # Get driver mapping and extract data
    driver_to_team = get_driver_team_mapping(base_url, client)
    st_speeds = extract_top_speeds_from_speed_trap(base_url, client, driver_to_team)
    
    # Prepare data
    plot_data = []
    for team, speed in st_speeds.items():
        color = team_colors.get(team, "#FFFFFF")
        plot_data.append({
            'Team': team,
            'Top Speed (km/h)': speed,
            'Color': color
        })
    
    # Sort by speed (descending)
    plot_data.sort(key=lambda x: x['Top Speed (km/h)'], reverse=True)
    
    # Store to MongoDB (always when generating new data)
    try:
        from src.repositories.plots import store_data_dict_to_mongo
        store_data_dict_to_mongo(
            year=y,
            round_nr=round_nr,
            session_name=e,
            event_name=event_name,
            data_type='top_speed_speedtrap',
            data=plot_data,
            version='v2'
        )
        print(f"✓ Speed Trap data cached to MongoDB (v2 collection)")
    except Exception as e:
        print(f"Warning: Failed to store to MongoDB: {e}")
    
    print(f"✓ Speed Trap data generated successfully")
    
    return plot_data


if __name__ == "__main__":
    # Test both data sources
    print("Testing Top Speed functions with dual sources...")
    
    year = 2023
    round_nr = 14  # Italian GP
    session = "Race"
    
    try:
        # Generate Telemetry plot and data
        print("\n" + "="*80)
        print("TELEMETRY SOURCE (CarData)")
        print("="*80)
        plot_path_tel = TopSpeedPlot_Telemetry(year, round_nr, session)
        data_path_tel = TopSpeedData_Telemetry(year, round_nr, session)
        print(f"\n✓ Telemetry Plot: {plot_path_tel}")
        print(f"✓ Telemetry Data: {data_path_tel}")
        
        # Generate Speed Trap plot and data
        print("\n" + "="*80)
        print("SPEED TRAP SOURCE (TimingData)")
        print("="*80)
        plot_path_st = TopSpeedPlot_SpeedTrap(year, round_nr, session)
        data_path_st = TopSpeedData_SpeedTrap(year, round_nr, session)
        print(f"\n✓ Speed Trap Plot: {plot_path_st}")
        print(f"✓ Speed Trap Data: {data_path_st}")
        
        print("\n" + "="*80)
        print("ALL OPERATIONS COMPLETED SUCCESSFULLY")
        print("="*80)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
