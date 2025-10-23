import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from fastf1.core import Laps
from timple.timedelta import strftimedelta

from src.utils import dirOrg
from src.data_loader import data_aqcuisition
from src.utils import setup_theme
from src.utils.teamColorPicker import team_colors, teams, get_team_color

def _init(y, r, e, session):
    dirOrg.checkForFolder(str(y) + "/" + session.event['EventName'] + "/" + e)
    location = "outputs/plots/" + str(y) + "/" + session.event['EventName'] + "/" + e
    name = str(y) + " " + session.event['EventName'] + ' ' + session.name + ' results.png'
    name_json = name.replace("png", "json")
    return location, name, name_json

def QualiResults(y,r,e):

    # Load session using data_aqcuisition module
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()

    # Theme setup
    setup_theme.setup_turnone_theme()

    # Check for existing folder and file
    location, name, name_json = _init(y, r, e, session)
    path = dirOrg.checkForFile(location, name)
    if (path != "NULL"):
        return path

    drivers = pd.unique(session.laps['Driver'])


    list_fastest_laps = list()
    for drv in drivers:
        try:
            drvs_fastest_lap = session.laps.pick_drivers(drv).pick_fastest()
            if len(drvs_fastest_lap) > 0:
                list_fastest_laps.append(drvs_fastest_lap)
        except Exception as e:
            print(f"Could not retrieve fastest lap for driver {drv}: {e}")

    if not list_fastest_laps:
        raise ValueError("No valid lap times found for any driver.")

    fastest_laps = Laps(list_fastest_laps).sort_values(by='LapTime').reset_index(drop=True)

    pole_lap = fastest_laps.pick_fastest()
    fastest_laps['LapTimeDelta'] = fastest_laps['LapTime'] - pole_lap['LapTime']


    print(fastest_laps[['Driver', 'LapTime', 'LapTimeDelta']])


    team_colors = list()
    for index, lap in fastest_laps.iterlaps():
        color = get_team_color(lap['Team'])
        team_colors.append(color)


    fig, ax = plt.subplots(figsize=(13, 13))
    ax.barh(fastest_laps.index, fastest_laps['LapTimeDelta'],
        color=team_colors, edgecolor='grey')
    ax.set_yticks(fastest_laps.index)
    ax.set_yticklabels(fastest_laps['Driver'])
    max_value = fastest_laps['LapTimeDelta'].max()
    ax.set_xlim(0, max_value * 1.15)


    # Adding time gaps on the plot
    fastest_lap_time = fastest_laps['LapTime'].min()
    for i, lap in fastest_laps.iterrows():
        lap_time = strftimedelta(lap['LapTime'], '%S.%ms')
        pole_time = strftimedelta(fastest_lap_time, '%S.%ms')
        pole_time_full = strftimedelta(fastest_lap_time, '%m:%s.%ms')
        time_difference = abs(round(float(pole_time) - float(lap_time), 3))
        if i==0:
            ax.text(lap['LapTimeDelta'], i, f" {pole_time_full}s", va='center', fontsize=13, weight='bold')
        else:
            ax.text(lap['LapTimeDelta'], i, f" +{time_difference}s", va='center', fontsize=13, weight='bold')



    # show fastest at the top
    ax.invert_yaxis()

    # draw vertical lines behind the bars
    ax.set_axisbelow(True)
    ax.xaxis.grid(True, which='major', linestyle='--', color='black', zorder=-1000)

    lap_time_string = strftimedelta(pole_lap['LapTime'], '%m:%s.%ms')

    plt.suptitle(f"{session.event['EventName']} {session.event.year} {session.name}\n"
             f"Fastest Lap: {lap_time_string} ({pole_lap['Driver']})")

    # Adding Watermark
    logo = mpimg.imread('lib/logo mic.png')
    fig.figimage(logo, 575, 575, zorder=3, alpha=.6)

    # Glow effect from setup_theme module
    setup_theme.add_glow(ax)

    plt.savefig(location + "/" + name)
    return location + "/" + name

def QualiResultsData(y,r,e):

    # Load session using data_aqcuisition module
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()

    # Theme setup
    setup_theme.setup_turnone_theme()

    # Check for existing folder and file
    location, name, name_json = _init(y, r, e, session)
    name = name.replace("png", "json")
    name2 = name.replace("csv", "json")
    path = dirOrg.checkForFile(location, name)
    path2 = dirOrg.checkForFile(location, name2)
    if (path != "NULL" and path2 != "NULL"):
        return path2  # Return JSON file path instead of CSV

    drivers = pd.unique(session.laps['Driver'])


    list_fastest_laps = list()
    for drv in drivers:
        try:
            drvs_fastest_lap = session.laps.pick_drivers(drv).pick_fastest()
            if len(drvs_fastest_lap) > 0:
                list_fastest_laps.append(drvs_fastest_lap)
        except Exception as e:
            print(f"Could not retrieve fastest lap for driver {drv}: {e}")


    if not list_fastest_laps:
        raise ValueError("No valid lap times found for any driver.")

    fastest_laps = Laps(list_fastest_laps).sort_values(by='LapTime').reset_index(drop=True)


    pole_lap = fastest_laps.pick_fastest()
    fastest_laps['LapTimeDelta'] = fastest_laps['LapTime'] - pole_lap['LapTime']


    team_colors = list()
    for index, lap in fastest_laps.iterlaps():
        color = get_team_color(lap['Team'])
        team_colors.append(color)

    # Adding time gaps on the plot
    fastest_lap_time = fastest_laps['LapTime'].min()
    for i, lap in fastest_laps.iterrows():
        lap_time = strftimedelta(lap['LapTime'], '%S.%ms')
        pole_time = strftimedelta(fastest_lap_time, '%S.%ms')

    # Return data in JSON format
    data = {
        'Driver': fastest_laps['Driver'].tolist(),
        'Team': fastest_laps['Team'].tolist(),
        'LapTime': fastest_laps['LapTime'].apply(lambda x: strftimedelta(x, '%m:%s.%ms')).tolist(),
        'LapTimeDelta': fastest_laps['LapTimeDelta'].apply(lambda x: round(x.total_seconds(), 3)).tolist(),
        'Color': team_colors
    }

    df = pd.DataFrame(data)
    df.to_json(location + "/" + name_json, orient='records')

    return location + "/" + name_json
