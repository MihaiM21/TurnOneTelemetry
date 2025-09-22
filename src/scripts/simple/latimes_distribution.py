import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import json

from src.utils import dirOrg
from src.data_loader import data_aqcuisition
from src.utils import setup_theme
from src.utils.teamColorPicker import team_colors, teams


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

    #Load session using data_aqcuisition module
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()

    #Theme setup
    setup_theme.setup_turnone_theme()

    # Check for existing folder and file
    location, name, name_json = _init(y, r, e, d, session)
    path = dirOrg.checkForFile(location, name)
    if (path != "NULL"):
        return path

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
    df.to_json(location + "/" + name_json, orient='records')
    return location + "/" + name_json
