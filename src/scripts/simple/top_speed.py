import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

from src.utils import dirOrg
from src.data_loader import data_aqcuisition
from src.utils import setup_theme
from src.utils.teamColorPicker import team_colors, teams
from src.utils.database.mongo_helper import store_plot_data_to_mongo, get_plot_data_from_mongo


def _init(y, r, e, session):
    dirOrg.checkForFolder(str(y) + "/" + session.event['EventName'] + "/" + e)
    location = "outputs/plots/" + str(y) + "/" + session.event['EventName'] + "/" + e
    name = 'Top speed comparison ' + str(y) + " " + session.event['EventName'] + ' ' + session.name + " .png"
    name_json = name.replace("png", "json")
    return location, name, name_json


def TopSpeedPlot(y, r, e):

    # Check MongoDB cache first (before loading session)
    cached_result = get_plot_data_from_mongo(y, r, e, 'top_speed')
    if cached_result:
        # Use cached data without loading session
        cached_data = cached_result['data']
        metadata = cached_result['metadata']
        
        # Generate plot from cached data
        setup_theme.setup_turnone_theme()
        
        # Build paths using metadata (no session needed)
        event_name = metadata['event_name'].replace(' ', '')
        dirOrg.checkForFolder(str(y) + "/" + event_name + "/" + e)
        location = "outputs/plots/" + str(y) + "/" + event_name + "/" + e
        name = 'Top speed comparison ' + str(y) + " " + event_name + ' ' + e + " .png"
        
        # Convert cached data to DataFrame
        df = pd.DataFrame(cached_data)
        teams_list = df['Team'].tolist()
        list_top_speed = df['Top Speed (km/h)'].tolist()
        list_colors = df['Color'].tolist()
        
        # Plotting
        fig, ax = plt.subplots(figsize=(13, 13), layout='constrained')
        ax.bar(teams_list, list_top_speed, color=list_colors)
        ax.set_ylim(280, 390)
        plt.yticks(range(280, 391, 10))

        x = 0
        for tms in teams_list:
            ax.text(tms, int(list_top_speed[x]) + 1, f"{int(list_top_speed[x])}km/h", 
                   verticalalignment='bottom', horizontalalignment='center', 
                   color='white', fontsize=16, fontweight="bold")
            x += 1

        # Adding Watermark
        logo = mpimg.imread('lib/logo mic.png')
        fig.figimage(logo, 575, 575, zorder=3, alpha=.6)
        plt.suptitle('Top speed comparison\n' + str(y) + " " + event_name + ' ' + e)
        plt.tight_layout()
        setup_theme.add_glow(ax)
        plt.savefig(location + "/" + name)
        return location + "/" + name

    # If not in cache, load session and continue with normal generation
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

    list_top_speed = list()
    string_top_speed = list()
    for tms in teams:
        try:
            telemetry = session.laps.pick_team(tms).pick_fastest().get_car_data()
            speed = max(telemetry['Speed'])
            list_top_speed.append(speed)
            string_top_speed.append(str(speed))
        except Exception as ex:
            print(f"An error occurred for team {tms}: {ex}")


    # Get team colors from teamColorPicker module
    list_colors = [team_colors[tms] if tms in team_colors else "#FFFFFF" for tms in teams]


    list_top_speed, teams, list_colors = (list(t) for t in zip(*sorted(zip(list_top_speed, teams, list_colors))))

    string_top_speed.sort()
    list_top_speed.reverse()
    teams.reverse()
    list_colors.reverse()
    string_top_speed.reverse()

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

def TopSpeedData(y, r, e, store_to_mongo=True):

    # Check MongoDB cache first (before loading session)
    cached_result = get_plot_data_from_mongo(y, r, e, 'top_speed')
    if cached_result:
        # Return cached data directly, no need to save to file
        print("Using cached Top Speed data from MongoDB")
        return cached_result['data']
    
    print("No cached Top Speed data found in MongoDB, generating new data.")

    # If not in cache, load session and continue with normal data generation
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()
    
    # Check for existing folder and file
    location, name, name_json = _init(y,r, e, session)
    name = name.replace("png", "json")
    name2 = name.replace("csv", "json")
    path = dirOrg.checkForFile(location, name)
    path2 = dirOrg.checkForFile(location, name2)
    # if (path != "NULL" and path2 != "NULL"):
    #     return path2  # Return JSON file path instead of CSV

    teams = pd.unique(session.laps['Team'])

    list_top_speed = list()
    string_top_speed = list()
    for tms in teams:
        try:
            telemetry = session.laps.pick_team(tms).pick_fastest().get_car_data()
            speed = max(telemetry['Speed'])
            list_top_speed.append(speed)
            string_top_speed.append(str(speed))
        except Exception as ex:
            print(f"An error occurred for team {tms}: {ex}")


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
    json_path = location + "/" + name_json
    df.to_json(json_path, orient='records')

    # Store to MongoDB if requested
    if store_to_mongo:
        try:
            store_plot_data_to_mongo(session, 'top_speed', json_path)
        except Exception as e:
            print(f"Warning: Failed to store to MongoDB: {e}")

    return json_path  # Return JSON file path
