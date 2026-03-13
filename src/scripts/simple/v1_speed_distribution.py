import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import numpy as np
from typing import Optional

from src.utils import dirOrg
from src.data_loader import data_aqcuisition
from src.utils import setup_theme
from src.utils.teamColorPicker import get_driver_color, team_colors
from src.utils.database.mongo_helper import store_data_dict_to_mongo, get_plot_data_from_mongo

def _init(y: int, r: int, e: str, session, driver: Optional[str] = None):
    event_folder = session.event['EventName'].replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{e}")
    location = f"outputs/plots/{y}/{event_folder}/{e}"
    drv_str = driver if driver else "Overall"
    name = f'Speed Distribution {y} {event_folder} {e} {drv_str}.png'
    name_json = name.replace("png", "json")
    return location, name, name_json

def SpeedDistributionPlot(y: int, r: int, e: str, driver: Optional[str] = None):
    # Check MongoDB cache
    drv_str = driver if driver else "Overall"
    cache_key = f'speed_distribution_{drv_str}'
    cached_result = get_plot_data_from_mongo(y, r, e, cache_key)
    if cached_result:
        # Load session only for metadata
        sessionloader = data_aqcuisition.SessionLoader(y, r, e)
        session = sessionloader.get_session()

        setup_theme.setup_turnone_theme()
        location, name, _ = _init(y, r, e, session, driver)
        
        cached_data = cached_result.get('data', cached_result)
        df = pd.DataFrame(cached_data)
        
        times = df['Time (s)'].tolist()
        speeds = df['Speed (km/h)'].tolist()
        if len(cached_data) > 0:
            color = cached_data[0].get('Color', '#FFFFFF')
            cached_driver = cached_data[0].get('Driver', 'Unknown')
        else:
            color = '#FFFFFF'
            cached_driver = 'Unknown'
        
        # Plotting
        fig, ax = plt.subplots(figsize=(13, 8), layout='constrained')
        ax.plot(times, speeds, color=color, linewidth=2)
        
        ax.set_xlabel("Time (s)", fontsize=14, color='white')
        ax.set_ylabel("Speed (km/h)", fontsize=14, color='white')
        
        if driver:
            title_driver = driver
        else:
            title_driver = f"Overall: {cached_driver}"
            
        plt.suptitle(f'Speed Distribution - Fastest Lap ({title_driver})\n{y} {session.event["EventName"]} {e}')
        
        try:
            logo = mpimg.imread('lib/logo mic.png')
            fig.figimage(logo, 575, 350, zorder=3, alpha=.6)
        except Exception:
            pass
            
        setup_theme.add_glow(ax)
        plt.savefig(location + "/" + name)
        plt.close()
        return location + "/" + name

    # Normal generation
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()
    
    setup_theme.setup_turnone_theme()
    location, name, _ = _init(y, r, e, session, driver)
    
    path = dirOrg.checkForFile(location, name)
    if path != "NULL":
        return path

    if driver:
        lap = session.laps.pick_driver(driver).pick_fastest()
        color = get_driver_color(driver)
    else:
        lap = session.laps.pick_fastest()
        drv = lap['Driver']
        color = get_driver_color(drv)
        
    try:
        telemetry = lap.get_car_data()
    except Exception as e:
        raise ValueError(f"FastF1 failed to load telemetry data. This affects 2026+ sessions. Please use the V2 endpoint.")
    
    times = telemetry['Time'].dt.total_seconds().tolist()
    speeds = telemetry['Speed'].tolist()
    
    # Plotting
    fig, ax = plt.subplots(figsize=(13, 8), layout='constrained')
    ax.plot(times, speeds, color=color, linewidth=2)
    
    ax.set_xlabel("Time (s)", fontsize=14, color='white')
    ax.set_ylabel("Speed (km/h)", fontsize=14, color='white')
    
    if driver:
        title_driver = driver
    else:
        title_driver = f"Overall: {lap['Driver']}"
        
    plt.suptitle(f'Speed Distribution - Fastest Lap ({title_driver})\n{y} {session.event["EventName"]} {e}')
    
    try:
        logo = mpimg.imread('lib/logo mic.png')
        fig.figimage(logo, 575, 350, zorder=3, alpha=.6)
    except Exception:
        pass
        
    setup_theme.add_glow(ax)
    plt.savefig(location + "/" + name)
    plt.close()
    return location + "/" + name


def SpeedDistributionData(y: int, r: int, e: str, driver: Optional[str] = None, store_to_mongo: bool = True):
    drv_str = driver if driver else "Overall"
    cache_key = f'speed_distribution_{drv_str}'
    cached_result = get_plot_data_from_mongo(y, r, e, cache_key)
    
    if cached_result:
        return cached_result['data']
        
    sessionloader = data_aqcuisition.SessionLoader(y, r, e)
    session = sessionloader.get_session()
    
    if driver:
        lap = session.laps.pick_driver(driver).pick_fastest()
        color = get_driver_color(driver)
        team = lap['Team']
    else:
        lap = session.laps.pick_fastest()
        color = get_driver_color(lap['Driver'])
        team = lap['Team']
        
    try:
        telemetry = lap.get_car_data()
    except Exception as e:
        raise ValueError(f"FastF1 failed to load telemetry data. This affects 2026+ sessions. Please use the V2 endpoint.")
    
    data_list = []
    times = telemetry['Time'].dt.total_seconds().tolist()
    speeds = telemetry['Speed'].tolist()
    
    for i in range(len(times)):
        data_list.append({
            'Time (s)': float(times[i]),
            'Speed (km/h)': float(speeds[i]),
            'Driver': str(lap['Driver']),
            'Color': color
        })
        
    if store_to_mongo:
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
        except Exception as ex:
            print(f"Warning: Failed to store to MongoDB: {ex}")
            
    return data_list

if __name__ == "__main__":
    print("Testing V1 Speed Distribution...")
    try:
        plot_path = SpeedDistributionPlot(2023, 14, "Race", driver="VER")
        print(f"Plot saved to: {plot_path}")
        
        data = SpeedDistributionData(2023, 14, "Race", driver="VER")
        print(f"Data length: {len(data)}")
        
        print("\nTesting V1 Overall Fastest Lap...")
        plot_path_overall = SpeedDistributionPlot(2023, 14, "Race")
        print(f"Plot saved to: {plot_path_overall}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

