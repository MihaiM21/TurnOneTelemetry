from matplotlib.collections import LineCollection
import matplotlib.pyplot as plt
import numpy as np
import matplotlib.image as mpimg
from matplotlib.colors import LinearSegmentedColormap, ListedColormap
import matplotlib.patches as mpatches

from src.utils import dirOrg
from src.data_loader import data_aqcuisition
from src.utils import setup_theme
from src.utils.teamColorPicker import team_colors, teams, get_team_color, get_driver_color


def print_sector_times(lap, driver_code):
    print(f"Sector times for {driver_code}:")
    lap_number = lap['LapNumber']
    sector1 = lap['Sector1Time']
    sector2 = lap['Sector2Time']
    sector3 = lap['Sector3Time']
    telemetry = lap.get_car_data()
    speed = max(telemetry['Speed'])
    print(f"Lap {lap_number}: Sector 1: {sector1}, Sector 2: {sector2}, Sector 3: {sector3}, Speed: {speed}")
    print("\n")

def _init(y, r, e, d1, d2, session):
    dirOrg.checkForFolder(str(y) + "/" + session.event['EventName'] + "/" + e)
    location = "outputs/plots/" + str(y) + "/" + session.event['EventName'] + "/" + e
    name = session.event['EventName'] + " " + str(session.name) + " " + str(session.event.year) + " " + str(
        d1) + " vs " + str(d2) + ".png"
    name_json = name.replace("png", "json")
    return location, name, name_json

def TrackComparisonPlot(y, r, e, d1, d2):

    # Load session using data_aqcuisition module
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()
    year = y
    # Theme setup
    setup_theme.setup_turnone_theme()

    color_team1 = get_driver_color(d1)
    color_team2 = get_driver_color(d2)

    laps = session.laps

    # Check for existing folder and file
    location, name, name_json = _init(y, r, e, d1, d2, session)
    path = dirOrg.checkForFile(location, name)
    if (path != "NULL"):
        return path

    # Select the laps from drivers
    laps_driver1 = laps.pick_driver(d1)
    laps_driver2 = laps.pick_driver(d2)

    # Get the telemetry data from their fastest lap
    fastest_driver1 = laps_driver1.pick_fastest().get_telemetry().add_distance()
    fastest_driver2 = laps_driver2.pick_fastest().get_telemetry().add_distance()

    # Since the telemetry data does not have a variable that indicates the driver,
    # we need to create that column
    fastest_driver1['Driver'] = d1
    fastest_driver2['Driver'] = d2

    # Merge both lap telemetries so we have everything in one DataFrame
    telemetry = fastest_driver1._append(fastest_driver2)

    # 25 mini-sectors (this can be adjusted)
    num_minisectors = 25

    # Grab the maximum value of distance that is known in the telemetry
    total_distance = total_distance = max(telemetry['Distance'])

    # Generate equally sized mini-sectors
    minisector_length = total_distance / num_minisectors

    # Initiate minisector variable, with 0 (meters) as a starting point.
    minisectors = [0]

    # Add multiples of minisector_length to the minisectors
    for i in range(0, (num_minisectors - 1)):
        minisectors.append(minisector_length * (i + 1))

    telemetry['Minisector'] = telemetry['Distance'].apply(
        lambda dist: (
            int((dist // minisector_length) + 1)
        )
    )

    # Calculate avg. speed per driver per mini sector
    average_speed = telemetry.groupby(['Minisector', 'Driver'])['Speed'].mean().reset_index()

    # Select the driver with the highest average speed
    fastest_driver = average_speed.loc[average_speed.groupby(['Minisector'])['Speed'].idxmax()]

    # Get rid of the speed column and rename the driver column
    fastest_driver = fastest_driver[['Minisector', 'Driver']].rename(columns={'Driver': 'Fastest_driver'})

    # Join the fastest driver per minisector with the full telemetry
    telemetry = telemetry.merge(fastest_driver, on=['Minisector'])

    # Order the data by distance to make matploblib does not get confused
    telemetry = telemetry.sort_values(by=['Distance'])

    # Convert driver name to integer
    telemetry.loc[telemetry['Fastest_driver'] == d1, 'Fastest_driver_int'] = 1
    telemetry.loc[telemetry['Fastest_driver'] == d2, 'Fastest_driver_int'] = 2

    x = np.array(telemetry['X'].values)
    y = np.array(telemetry['Y'].values)

    points = np.array([x, y]).T.reshape(-1, 1, 2)
    segments = np.concatenate([points[:-1], points[1:]], axis=1)
    fastest_driver_array = telemetry['Fastest_driver_int'].to_numpy().astype(float)


    cmap = ListedColormap([color_team1, color_team2])
    lc_comp = LineCollection(segments, norm=plt.Normalize(1, cmap.N + 1), cmap=cmap)
    lc_comp.set_array(fastest_driver_array)
    lc_comp.set_linewidth(5)

    # Setting the size of the image
    fig, ax = plt.subplots(figsize=(13, 13))


    plt.gca().add_collection(lc_comp)
    plt.axis('equal')
    plt.tick_params(labelleft=False, left=False, labelbottom=False, bottom=False)

    # New legend model
    legend_patches = [mpatches.Patch(color=color_team1, label=d1),
                      mpatches.Patch(color=color_team2, label=d2)]

    plt.legend(handles=legend_patches, loc='upper right')


    plt.suptitle(str(d1) + " vs " + str(d2) + " " + str(year) + " " + session.event['EventName'] + ' ' + session.name)

    # Adding Watermark
    logo = mpimg.imread('lib/logo mic.png')
    plt.figimage(logo, 575, 575, zorder=3, alpha=.6)


    plt.savefig(location + "/" + name)
    plt.close()

    return location + "/" + name

def TrackComparisonData(y, r, e, d1, d2):

    import json
    # Load session using data_aqcuisition module
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()
    year = y
    # Theme setup
    setup_theme.setup_turnone_theme()

    color_team1 = get_driver_color(d1)
    color_team2 = get_driver_color(d2)

    laps = session.laps

    # Check for existing folder and file
    location, name, name_json = _init(y, r, e, d1, d2, session)
    name = name.replace("png", "json")
    name2 = name.replace("csv", "json")
    path = dirOrg.checkForFile(location, name)
    path2 = dirOrg.checkForFile(location, name2)
    if (path != "NULL" and path2 != "NULL"):
        return path2  # Return JSON file path instead of CSV

    # Select the laps from drivers
    laps_driver1 = laps.pick_driver(d1)
    laps_driver2 = laps.pick_driver(d2)

    # Get the telemetry data from their fastest lap
    fastest_driver1 = laps_driver1.pick_fastest().get_telemetry().add_distance()
    fastest_driver2 = laps_driver2.pick_fastest().get_telemetry().add_distance()

    # Since the telemetry data does not have a variable that indicates the driver,
    # we need to create that column
    fastest_driver1['Driver'] = d1
    fastest_driver2['Driver'] = d2

    # Merge both lap telemetries so we have everything in one DataFrame
    telemetry = fastest_driver1._append(fastest_driver2)

    # 25 mini-sectors (this can be adjusted)
    num_minisectors = 25

    # Grab the maximum value of distance that is known in the telemetry
    total_distance = max(telemetry['Distance'])

    # Generate equally sized mini-sectors
    minisector_length = total_distance / num_minisectors

    # Initiate minisector variable, with 0 (meters) as a starting point.
    minisectors = [0]

    # Add multiples of minisector_length to the minisectors
    for i in range(0, (num_minisectors - 1)):
        minisectors.append(minisector_length * (i + 1))

    telemetry['Minisector'] = telemetry['Distance'].apply(
        lambda dist: (
            int((dist // minisector_length) + 1)
        )
    )

    # Calculate avg. speed per driver per mini sector
    average_speed = telemetry.groupby(['Minisector', 'Driver'])['Speed'].mean().reset_index()

    # Select the driver with the highest average speed
    fastest_driver = average_speed.loc[average_speed.groupby(['Minisector'])['Speed'].idxmax()]

    # Get rid of the speed column and rename the driver column
    fastest_driver = fastest_driver[['Minisector', 'Driver']].rename(columns={'Driver': 'Fastest_driver'})

    # Join the fastest driver per minisector with the full telemetry
    telemetry = telemetry.merge(fastest_driver, on=['Minisector'])

    # Order the data by distance to make matploblib does not get confused
    telemetry = telemetry.sort_values(by=['Distance'])

    # Convert driver name to integer
    telemetry.loc[telemetry['Fastest_driver'] == d1, 'Fastest_driver_int'] = 1
    telemetry.loc[telemetry['Fastest_driver'] == d2, 'Fastest_driver_int'] = 2

    # Build telemetry list for JSON
    telemetry_list = []
    for _, row in telemetry.iterrows():
        telemetry_list.append({
            'x': float(row['X']),
            'y': float(row['Y']),
            'distance': float(row['Distance']),
            'speed': float(row['Speed']),
            'driver': row['Driver'],
            'minisector': int(row['Minisector']),
            'fastest_driver': row['Fastest_driver'],
            'fastest_driver_int': int(row['Fastest_driver_int'])
        })

    # Build session info
    session_info = {
        'year': year,
        'race': r,
        'event': e,
        'event_name': session.event['EventName'],
        'session_name': session.name
    }

    # Build final JSON structure
    result = {
        'driver1': d1,
        'driver2': d2,
        'driver1_color': color_team1,
        'driver2_color': color_team2,
        'telemetry': telemetry_list,
        'session_info': session_info
    }

    # Save to JSON file
    with open(location + "/" + name, "w") as f:
        json.dump(result, f, indent=2)

    return location + "/" + name