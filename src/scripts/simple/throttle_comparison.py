import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

from src.utils import dirOrg
from src.data_loader import data_aqcuisition
from src.utils import setup_theme
from src.utils.teamColorPicker import team_colors, teams, get_driver_color

def _init(y, r, e, session):
    dirOrg.checkForFolder(str(y) + "/" + session.event['EventName'] + "/" + e)
    location = "outputs/plots/" + str(y) + "/" + session.event['EventName'] + "/" + e
    name = 'Throttle comparison ' + str(y) + " " + session.event['EventName'] + ' ' + session.name + " .png"
    name_json = name.replace("png", "json")
    return location, name, name_json
def ThrottleComp(y,r,e):

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
    # Pana aici

    drivers = pd.unique(session.laps['Driver'])

    valid_drivers = []
    list_telemetry = []
    string_telemetry = []
    list_colors = []
    for drv in drivers:
        try:
            telemetry = session.laps.pick_driver(drv).pick_fastest().get_car_data().add_distance()
            average = sum(telemetry['Throttle'])/len(telemetry['Throttle'])
            average = round(average, 2)
            string_telemetry.append(str(average))
            list_telemetry.append(average)
            valid_drivers.append(drv)
            drivercolor = get_driver_color(drv)
            list_colors.append(drivercolor)
        except Exception as ex:
            print(f"An error occurred for driver {drv}: {ex}")

    # Sort all lists together
    list_telemetry, valid_drivers, list_colors = (list(t) for t in zip(*sorted(zip(list_telemetry, valid_drivers, list_colors))))
    string_telemetry.sort()
    list_telemetry.reverse()
    valid_drivers.reverse()
    list_colors.reverse()
    string_telemetry.reverse()
    fig, ax = plt.subplots(figsize=(13, 13), layout='constrained')
    ax.bar(valid_drivers, list_telemetry, color = list_colors)
    # ax.set(ylim=(0, 100), yticks=np.linspace(0, 100, 11))
    ax.set_ylim(50, 100)
    plt.yticks(range(50, 101, 5))

    for x, drv in enumerate(valid_drivers):
        ax.text(drv, list_telemetry[x]+1, string_telemetry[x] + "%", horizontalalignment='center', color='white')

    plt.suptitle('Throttle comparison\n' + str(y) + " " + session.event['EventName'] + ' ' + session.name)

    # Adding Watermark
    logo = mpimg.imread('lib/logo mic.png')
    fig.figimage(logo, 575, 575, zorder=3, alpha=.6)

    # Glow effect from setup_theme module
    setup_theme.add_glow(ax)

    plt.savefig(location + "/" + name)
    return location + "/" + name

def ThrottleCompData(y,r,e):

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

    valid_drivers = []
    list_telemetry = []
    string_telemetry = []
    list_colors = []
    for drv in drivers:
        try:
            telemetry = session.laps.pick_driver(drv).pick_fastest().get_car_data().add_distance()
            average = sum(telemetry['Throttle']) / len(telemetry['Throttle'])
            average = round(average, 2)
            string_telemetry.append(str(average))
            list_telemetry.append(average)
            valid_drivers.append(drv)
            drivercolor = get_driver_color(drv)
            list_colors.append(drivercolor)
        except Exception as ex:
            print(f"An error occurred for driver {drv}: {ex}")

    # Sort all lists together
    list_telemetry, valid_drivers, list_colors = (list(t) for t in zip(*sorted(zip(list_telemetry, valid_drivers, list_colors))))
    string_telemetry.sort()
    list_telemetry.reverse()
    valid_drivers.reverse()
    list_colors.reverse()
    string_telemetry.reverse()

    # Return data in JSON format - Create list of records manually for consistent output
    json_data = []
    for i in range(len(valid_drivers)):
        json_data.append({
            'Driver': valid_drivers[i],
            'Average Throttle (%)': float(list_telemetry[i]),
            'Color': list_colors[i]
        })

    # Save JSON data directly using json module
    with open(location + "/" + name_json, 'w') as f:
        json.dump(json_data, f, indent=2)

