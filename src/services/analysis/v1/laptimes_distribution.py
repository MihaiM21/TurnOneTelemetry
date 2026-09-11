import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import json

from src.core.logging import get_logger
from src.services.plotting import output as dirOrg
from src.ingestion import fastf1_client as data_aqcuisition
from src.services.plotting import theme as setup_theme
from src.services.plotting.colors import team_colors, teams
from src.repositories.plots import (
    get_plot_data_from_mongo,
    store_data_dict_to_mongo,
    store_plot_data_to_mongo,
)

logger = get_logger(__name__)


def _format_laptime(laptime_seconds):
    """Format laptime from seconds to F1 format (mm:ss.sss)"""
    if pd.isna(laptime_seconds):
        return None

    minutes = int(laptime_seconds // 60)
    seconds = laptime_seconds % 60
    return f"{minutes:01d}:{seconds:06.3f}"


def _init(y, r, e, d, session):
    dirOrg.checkForFolder(str(y) + "/" + session.event['EventName'] + "/" + e)
    location = "outputs/plots/" + str(y) + "/" + session.event['EventName'] + "/" + e
    name = 'Laptimes distribution ' + str(y) + " " + session.event['EventName'] + ' ' + session.name + " .png"
    name_json = name.replace("png", "json")
    return location, name, name_json

def LatimesDistribution(y, r, e, d):

    # Cache key is parametrized by driver — otherwise the first driver ever
    # queried for a (year, round, session) would be served back for every
    # other driver requested afterward.
    cache_key = f'lap_times_distribution_{d}'

    # Check MongoDB cache first (before loading session)
    cached_result = get_plot_data_from_mongo(y, r, e, cache_key)
    if cached_result:
        # Return cached data directly, no need to save to file
        logger.info("Using cached Lap Times Distribution data from MongoDB")
        return cached_result['data']

    logger.info("No cached Lap Times Distribution data found in MongoDB, generating new data.")

    # If not in cache, load session using data_aqcuisition module
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()

    # If not in cache, continue with normal generation
    #Theme setup
    setup_theme.setup_turnone_theme()

    # Check for existing folder and file
    location, name, name_json = _init(y, r, e, d, session)

    laps = session.laps.pick_driver(d)
    laps['LapTimeSeconds'] = laps['LapTime'].dt.total_seconds()


    # Format lap times to F1 standard format (mm:ss.sss)
    laps['LapTimeFormatted'] = laps['LapTimeSeconds'].apply(_format_laptime)

    # Filter out invalid lap times (NaN values)
    valid_laps = laps.dropna(subset=['LapTimeFormatted'])

    # Generate lap numbers starting from 1
    lap_numbers = list(range(1, len(valid_laps) + 1))

    #Return json with formatted lap times
    data = {
        "driver": d,
        "lap_times_formatted": valid_laps['LapTimeFormatted'].tolist(),
        "lap_times_seconds": valid_laps['LapTimeSeconds'].tolist(),
        "lap_numbers": lap_numbers,
        "compound": valid_laps['Compound'].tolist()
    }
    df = pd.DataFrame(data)
    data_list = df.to_dict(orient='records')
    
    # Store to MongoDB
    try:
        event_name = session.event['EventName']
        store_data_dict_to_mongo(
            year=y,
            round_nr=r,
            session_name=e,
            event_name=event_name,
            data_type=cache_key,
            data=data_list,
            version='v1'
        )
    except Exception as e:
        logger.warning("Failed to store to MongoDB: %s", e)
    
    return data_list  # Return data directly

