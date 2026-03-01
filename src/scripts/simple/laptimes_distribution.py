import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import json

from src.utils import dirOrg
from src.data_loader import data_aqcuisition
from src.utils import setup_theme
from src.utils.teamColorPicker import team_colors, teams
from src.utils.database.mongo_helper import store_plot_data_to_mongo, get_plot_data_from_mongo
from src.utils.database.mongo_helper import store_plot_data_to_mongo, get_plot_data_from_mongo


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

    # Check MongoDB cache first (before loading session)
    cached_result = get_plot_data_from_mongo(y, r, e, 'lap_times_distribution')
    if cached_result:
        # Return cached data directly, no need to save to file
        print("Using cached Lap Times Distribution data from MongoDB")
        return cached_result['data']

    print("No cached Lap Times Distribution data found in MongoDB, generating new data.")

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
            data_type='lap_times_distribution',
            data=data_list,
            version='v1'
        )
    except Exception as e:
        print(f"Warning: Failed to store to MongoDB: {e}")
    
    return data_list  # Return data directly

