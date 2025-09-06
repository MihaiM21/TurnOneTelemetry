import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

from src.utils import dirOrg
from src.data_loader import data_aqcuisition
from src.utils import setup_theme
from src.utils.teamColorPicker import team_colors, teams

#  python -m src.scripts.simple.top_speed

def _init(y, r, e, session):
    dirOrg.checkForFolder(str(y) + "/" + session.event['EventName'] + "/" + e)
    location = "outputs/plots/" + str(y) + "/" + session.event['EventName'] + "/" + e
    name = 'Top speed comparison ' + str(y) + " " + session.event['EventName'] + ' ' + session.name + " .png"
    return location, name


def TopSpeedFunc(y, r, e):

    #Load session using data_aqcuisition module
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()

    setup_theme.setup_turnone_theme()

    # Verifică dacă folderul pentru ploturi există si daca exista si plotul deja generat
    location, name = _init(y, r, e, session)
    path = dirOrg.checkForFile(location, name)
    if (path != "NULL"):
        return path
    # Pana aici

    teams = pd.unique(session.laps['Team'])
    session.laps.pick_driver('VER').pick_fastest().get_car_data()

    list_top_speed = list()
    string_top_speed = list()
    for tms in teams:
        telemetry = session.laps.pick_team(tms).pick_fastest().get_car_data()
        speed = max(telemetry['Speed'])
        list_top_speed.append(speed)
        string_top_speed.append(str(speed))


    list_colors = list()
    # for tms in teams:
    #     teamcolor = fastf1.plotting.team_color(tms)
    #     list_colors.append(teamcolor)


    list_colors = [team_colors[tms] if tms in team_colors else "#FFFFFF" for tms in teams]



    list_top_speed, teams, list_colors = (list(t) for t in zip(*sorted(zip(list_top_speed, teams, list_colors))))


    string_top_speed.sort()
    list_top_speed.reverse()
    teams.reverse()
    list_colors.reverse()
    string_top_speed.reverse()
    print(list_top_speed)
    print(teams)

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

    setup_theme.add_glow(ax)

    plt.savefig(location + "/" + name)
    plt.show()
    return location + "/" + name


TopSpeedFunc(2025,15, 'Q')