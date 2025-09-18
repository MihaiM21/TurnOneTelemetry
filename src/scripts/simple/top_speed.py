import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

from src.utils import dirOrg
from src.data_loader import data_aqcuisition
from src.utils import setup_theme
from src.utils.teamColorPicker import team_colors, teams


def _init(y, r, e, session):
    dirOrg.checkForFolder(str(y) + "/" + session.event['EventName'] + "/" + e)
    location = "outputs/plots/" + str(y) + "/" + session.event['EventName'] + "/" + e
    name = 'Top speed comparison ' + str(y) + " " + session.event['EventName'] + ' ' + session.name + " .png"
    name_json = name.replace("png", "json")
    return location, name, name_json


def TopSpeedPlot(y, r, e):

    #Load session using data_aqcuisition module
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()

    #Theme setup
    setup_theme.setup_turnone_theme()

    # Check for existing folder and file
    location, name, name_json = _init(y, r, e, session)
    path = dirOrg.checkForFile(location, name)
    if (path != "NULL"):
        return path


    teams = pd.unique(session.laps['Team'])
    session.laps.pick_driver('VER').pick_fastest().get_car_data()

    list_top_speed = list()
    string_top_speed = list()
    for tms in teams:
        telemetry = session.laps.pick_team(tms).pick_fastest().get_car_data()
        speed = max(telemetry['Speed'])
        list_top_speed.append(speed)
        string_top_speed.append(str(speed))


    # Get team colors from teamColorPicker module
    list_colors = [team_colors[tms] if tms in team_colors else "#FFFFFF" for tms in teams]


    list_top_speed, teams, list_colors = (list(t) for t in zip(*sorted(zip(list_top_speed, teams, list_colors))))

    string_top_speed.sort()
    list_top_speed.reverse()
    teams.reverse()
    list_colors.reverse()
    string_top_speed.reverse()
    print(list_top_speed)
    print(teams)

    # Plotting
    fig, ax = plt.subplots(figsize=(13, 13), layout='constrained')
    ax.bar(teams, list_top_speed, color=list_colors)

    # Set Y-axis limits and ticks
    # 400 is the best for now, check for 380
    ax.set_ylim(280, 390)
    plt.yticks(range(280, 391, 10))


    x = 0
    for tms in teams:
        ax.text(tms, int(list_top_speed[x]) + 1, f"{int(list_top_speed[x])}km/h", verticalalignment='bottom',
            horizontalalignment='center', color='white', fontsize=16, fontweight="bold")
        x += 1

    # Adding Watermark
    logo = mpimg.imread('lib/logo mic.png')
    fig.figimage(logo, 575, 575, zorder=3, alpha=.6)
    plt.suptitle('Top speed comparison\n' + str(y) + " " + session.event['EventName'] + ' ' + session.name)
    plt.tight_layout()

    # Glow effect from setup_theme module
    setup_theme.add_glow(ax)

    plt.savefig(location + "/" + name)
    return location + "/" + name

def TopSpeedData(y, r, e):

    #Load session using data_aqcuisition module
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()
    print(y , r, e)


    # Check for existing folder and file
    location, name, name_json = _init(y,r, e, session)
    name = name.replace("png", "json")
    name2 = name.replace("csv", "json")
    path = dirOrg.checkForFile(location, name)
    path2 = dirOrg.checkForFile(location, name2)
    if (path != "NULL" and path2 != "NULL"):
        return path2  # Return JSON file path instead of CSV

    teams = pd.unique(session.laps['Team'])
    session.laps.pick_driver('VER').pick_fastest().get_car_data()

    list_top_speed = list()
    string_top_speed = list()
    for tms in teams:
        telemetry = session.laps.pick_team(tms).pick_fastest().get_car_data()
        speed = max(telemetry['Speed'])
        list_top_speed.append(speed)
        string_top_speed.append(str(speed))


    # Get team colors from teamColorPicker module
    list_colors = [team_colors[tms] if tms in team_colors else "#FFFFFF" for tms in teams]


    list_top_speed, teams, list_colors = (list(t) for t in zip(*sorted(zip(list_top_speed, teams, list_colors))))

    string_top_speed.sort()
    list_top_speed.reverse()
    teams.reverse()
    list_colors.reverse()
    string_top_speed.reverse()
    print(list_top_speed)
    print(teams)


    # Return data in JSON format
    data = {
        'Team': teams,
        'Top Speed (km/h)': list_top_speed,
        'Color': list_colors
    }
    df = pd.DataFrame(data)
    df.to_json(location + "/" + name_json, orient='records')
    return location + "/" + name_json  # Return JSON file path
