from typing import Dict, List, Optional, Union

from src.services.plotting import output as dirOrg
from src.repositories.plots import store_data_dict_to_mongo, get_plot_data_from_mongo
from src.ingestion.static_client import F1StaticClient
from src.services.analysis.v2._helpers import (
    parse_f1_time,
    get_all_driver_codes,
    format_lap_time,
    extract_stints,
)


DATA_TYPE = "lap_times_distribution"


def _data_type(driver: str) -> str:
    """Stored MongoDB key for one driver's lap-time distribution."""
    return f"{DATA_TYPE}_{driver.strip().upper()}"


def _init(y: int, event_name: str, session_name: str, driver: str):
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f'Laptimes distribution {y} {event_name} {session_name} {driver}.json'
    return location, name


def _extract_all_lap_times(base_url: str, client: F1StaticClient, driver_num: str) -> List[Dict]:
    """Returns [{lap_number, lap_time_s, lap_time_formatted}] in lap order."""
    records = []
    seen_values = []  # ordered list of seen lap time strings to detect new values

    try:
        data = client.parse_jsonstream_simple(base_url + "TimingData.jsonStream")

        for entry in data:
            if 'Lines' not in entry:
                continue
            if driver_num not in entry['Lines']:
                continue

            line = entry['Lines'][driver_num]
            if not isinstance(line, dict):
                continue

            last_lap = line.get('LastLapTime', {})
            if not isinstance(last_lap, dict):
                continue

            val = last_lap.get('Value', '')
            if not val:
                continue

            lap_time = parse_f1_time(val)
            if lap_time <= 0:
                continue

            # A new distinct value means a new lap was completed
            if not seen_values or seen_values[-1] != val:
                if val not in seen_values:
                    seen_values.append(val)
                    records.append({
                        'lap_number': len(records) + 1,
                        'lap_time_s': round(lap_time, 3),
                        'lap_time_formatted': format_lap_time(lap_time)
                    })

        return records
    except Exception as e:
        print(f"Error extracting lap times: {e}")
        return []


def _extract_compounds(base_url: str, client: F1StaticClient, driver_num: str) -> Dict[int, str]:
    """Returns {lap_number: compound} from TimingAppData.jsonStream stints."""
    stints = extract_stints(base_url, client, driver_num)
    compound_map: Dict[int, str] = {}
    for stint in stints:
        end_lap = stint['end_lap'] if stint['end_lap'] is not None else 9999
        for lap_n in range(stint['start_lap'], end_lap + 1):
            compound_map[lap_n] = stint['compound']
    return compound_map


def LaptimesDistribution(y: int, identifier: Union[int, str], e: str, driver: str,
                          store_to_mongo: bool = True) -> list:
    """
    Returns [{driver, lap_number, lap_times_formatted, lap_times_seconds, compound}]
    for a specific driver across a session.
    """
    driver_tla = driver.strip().upper()
    cache_key = _data_type(driver_tla)

    cached = get_plot_data_from_mongo(y, identifier, e, cache_key, version='v2')
    if cached:
        print(f"Using cached Lap Times Distribution from MongoDB (v2) for {driver_tla}")
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

    driver_codes = get_all_driver_codes(base_url, client)
    tla_to_num = {v.upper(): k for k, v in driver_codes.items()}
    driver_num = tla_to_num.get(driver_tla)
    if not driver_num:
        raise ValueError(f"Driver {driver_tla} not found in session")

    lap_records = _extract_all_lap_times(base_url, client, driver_num)
    compound_map = _extract_compounds(base_url, client, driver_num)

    data_list = [
        {
            'driver': driver_tla,
            'lap_number': r['lap_number'],
            'lap_times_formatted': r['lap_time_formatted'],
            'lap_times_seconds': r['lap_time_s'],
            'compound': compound_map.get(r['lap_number'], 'UNKNOWN')
        }
        for r in lap_records
    ]

    if store_to_mongo and data_list:
        try:
            store_data_dict_to_mongo(
                year=y, round_nr=round_nr, session_name=e, event_name=event_name,
                data_type=cache_key, data=data_list, version='v2'
            )
            print(f"✓ Lap times data cached to MongoDB (v2) for {driver_tla}")
        except Exception as err:
            print(f"Warning: Failed to store to MongoDB: {err}")

    return data_list


if __name__ == "__main__":
    print("Testing V2 Laptimes Distribution...")
    try:
        data = LaptimesDistribution(2023, 14, "Race", "VER")
        print(f"Laps returned: {len(data)}")
        if data:
            print(f"First lap: {data[0]}")
            print(f"Last lap: {data[-1]}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
