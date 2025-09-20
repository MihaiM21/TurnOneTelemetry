import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import json

from src.utils import dirOrg
from src.data_loader import data_aqcuisition
from src.utils import setup_theme
from src.utils.teamColorPicker import team_colors, teams, get_team_color, get_driver_color

def _init(y, r, e, d1, d2, session):
    dirOrg.checkForFolder(str(y) + "/" + session.event['EventName'] + "/" + e)
    location = "outputs/plots/" + str(y) + "/" + session.event['EventName'] + "/" + e
    name = str(y) + " " + session.event['EventName'] + " " + d1 + " vs " + d2 + " Throttle graph.png"
    name_json = name.replace("png", "json")
    return location, name, name_json

def throttle_graph(y,r,e,d1,d2):
    # Load session using data_aqcuisition module
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()

    # Theme setup
    setup_theme.setup_turnone_theme()

    driver1 = d1
    driver2 = d2
    year = y
    round = r
    event = e

    session.load()
    laps = session.laps

    # Check for existing folder and file
    location, name, name_json = _init(y, r, e, d1, d2, session)
    path = dirOrg.checkForFile(location, name)
    if (path != "NULL"):
        return path

    # Getting laps from the drivers
    laps_driver1 = laps.pick_driver(driver1)
    laps_driver2 = laps.pick_driver(driver2)

    # Extract the fastest laps
    fastest_driver1 = laps_driver1.pick_fastest()
    fastest_driver2 = laps_driver2.pick_fastest()

    # Get telemetry from fastest laps
    telemetry_driver1 = fastest_driver1.get_car_data().add_distance()
    telemetry_driver2 = fastest_driver2.get_car_data().add_distance()

    # 4 subplots in the same image
    fig, ax = plt.subplots(3 ,figsize=(13, 13), clear = "True")
    fig.suptitle("Fastest Lap Telemetry Comparison")

    # Plot for Speed and Distance (axis)
    ax[0].plot(telemetry_driver1['Distance'], telemetry_driver1['Speed'], label=driver1)
    ax[0].plot(telemetry_driver2['Distance'], telemetry_driver2['Speed'], label=driver2)
    ax[0].set(ylabel='Speed')
    ax[0].legend(loc="lower right")

    ax[1].plot(telemetry_driver1['Distance'], telemetry_driver1['Throttle'], label=driver1)
    ax[1].plot(telemetry_driver2['Distance'], telemetry_driver2['Throttle'], label=driver2)
    ax[1].set(ylabel='Throttle')

    ax[2].plot(telemetry_driver1['Distance'], telemetry_driver1['Brake'], label=driver1)
    ax[2].plot(telemetry_driver2['Distance'], telemetry_driver2['Brake'], label=driver2)
    ax[2].set(ylabel='Brakes')

    # Obține datele de timp pentru cel mai rapid tur
    telemetry_driver1['LapTime(s)'] = (telemetry_driver1['Time'] - telemetry_driver1['Time'].iloc[0]).dt.total_seconds()
    telemetry_driver2['LapTime(s)'] = (telemetry_driver2['Time'] - telemetry_driver2['Time'].iloc[0]).dt.total_seconds()

    # Plot pentru timpul pe tur în funcție de distanță
    # ax[3].plot(telemetry_driver1['Distance'], telemetry_driver1['LapTime(s)'], label=driver1)
    # ax[3].plot(telemetry_driver2['Distance'], telemetry_driver2['LapTime(s)'], label=driver2)
    #
    # ax[3].set(ylabel='Lap Time (s)', xlabel='Distance (m)')
    # ax[3].legend(loc="lower right")

    # NO NEED
    #ax[3].plot(telemetry_driver1['Distance'], telemetry_driver1['Brake'], label=driver1)
    #ax[3].plot(telemetry_driver1['Distance'], telemetry_driver1['Throttle'], label=driver1)
    #ax[3].set(ylabel='Comparison')

    # Hide x labels and tick labels for top plots and y ticks for right plots.
    for a in ax.flat:
        a.label_outer()

    # Adding Watermark
    logo = mpimg.imread('lib/logo mic.png')
    fig.figimage(logo, 575, 575, zorder=3, alpha=.6)

    plt.suptitle('Throttle graph\n' + str(y) + " " + session.event['EventName'] + ' ' + session.name)

    plt.savefig(location + "/" + name)
    plt.close()

    return location + "/" + name

def throttle_graph_data(y,r,e,d1,d2):
    # Load session using data_aqcuisition module
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()

    # Theme setup
    setup_theme.setup_turnone_theme()
    driver1 = d1
    driver2 = d2
    year = y
    round = r
    event = e

    session.load()
    laps = session.laps

    # Check for existing folder and file
    location, name, name_json = _init(y, r, e, d1, d2, session)
    name = name.replace("png", "json")
    name2 = name.replace("csv", "json")
    json_path = location + "/" + name_json

    # Check if JSON file already exists
    path = dirOrg.checkForFile(location, name_json)
    if (path != "NULL"):
        return path
    laps_driver1 = laps.pick_driver(driver1)
    try:
        # Getting laps from the drivers
        laps_driver1 = laps.pick_driver(driver1)
        laps_driver2 = laps.pick_driver(driver2)
        fastest_driver1 = laps_driver1.pick_fastest()
        # Extract the fastest laps
        fastest_driver1 = laps_driver1.pick_fastest()
        fastest_driver2 = laps_driver2.pick_fastest()
        telemetry_driver1 = fastest_driver1.get_car_data().add_distance()
        # Get telemetry from fastest laps
        telemetry_driver1 = fastest_driver1.get_car_data().add_distance()
        telemetry_driver2 = fastest_driver2.get_car_data().add_distance()
        # Calculate lap time
        telemetry_driver1['LapTime(s)'] = (telemetry_driver1['Time'] - telemetry_driver1['Time'].iloc[0]).dt.total_seconds()
        telemetry_driver2['LapTime(s)'] = (telemetry_driver2['Time'] - telemetry_driver2['Time'].iloc[0]).dt.total_seconds()
        # Get driver colors
        driver1_color = get_driver_color(driver1)
        driver2_color = get_driver_color(driver2)

        # Prepare telemetry data for JSON
        telemetry_data = []
        # Add driver 1 telemetry points
        for idx, row in telemetry_driver1.iterrows():
            telemetry_data.append({
                "distance": float(row['Distance']),
                "speed": float(row['Speed']),
                "throttle": float(row['Throttle']),
                "brake": float(row['Brake']),
                "lap_time": float(row['LapTime(s)']),
                "driver": driver1
            })

        # Add driver 2 telemetry points
        for idx, row in telemetry_driver2.iterrows():
            telemetry_data.append({
                "distance": float(row['Distance']),
                "speed": float(row['Speed']),
                "throttle": float(row['Throttle']),
                "brake": float(row['Brake']),
                "lap_time": float(row['LapTime(s)']),
                "driver": driver2
            })

        # Create JSON data structure
        json_data = {
            "driver1": driver1,
            "driver2": driver2,
            "driver1_color": driver1_color,
            "driver2_color": driver2_color,
            "telemetry": telemetry_data,
            "session_info": {
                "year": year,
                "race": session.event['EventName'],
                "event": event,
                "event_name": session.event['EventName'],
                "session_name": session.name
            }
        }
        # Save to JSON file
        with open(location + "/" + name, "w") as f:
            json.dump(json_data, f, indent=2)
        return location + "/" + name

    except Exception as e:
        print(f"Error generating telemetry data for drivers")
