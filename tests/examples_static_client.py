"""
Practical Examples: Real-world usage of the F1 Static Client
=============================================================
These examples show how to use the client for actual data analysis tasks.
"""

from src.data_loader.f1_static_client import F1StaticClient
import json
from typing import Dict, List


def example_1_get_lap_times():
    """
    Example 1: Extract lap times for all drivers from a race
    """
    print("="*80)
    print("EXAMPLE 1: Extract Lap Times from 2023 Italian GP")
    print("="*80)
    
    client = F1StaticClient()
    
    # Get the TimingData URL
    url = client.get_timing_data_url(2023, "Italian Grand Prix", "Race")
    
    if not url:
        print("Session not found")
        return
    
    # Parse the full timing data stream
    print("\nFetching timing data (this may take a few seconds)...")
    timing_data = client.parse_jsonstream_simple(url)
    
    print(f"Loaded {len(timing_data)} timing updates")
    
    # Extract lap completion data
    lap_times = {}
    
    for entry in timing_data:
        lines = entry.get('Lines', {})
        
        for driver_num, driver_data in lines.items():
            # Check if there's a last lap time
            last_lap = driver_data.get('LastLapTime', {})
            if last_lap and 'Value' in last_lap:
                lap_time = last_lap['Value']
                
                if driver_num not in lap_times:
                    lap_times[driver_num] = []
                
                lap_times[driver_num].append({
                    'lap_time': lap_time,
                    'position': driver_data.get('Position', '?'),
                    'timestamp': entry.get('_timestamp', 'unknown')
                })
    
    # Show summary
    print(f"\nFound lap times for {len(lap_times)} drivers")
    
    # Show first driver's lap times
    if lap_times:
        first_driver = list(lap_times.keys())[0]
        print(f"\nDriver #{first_driver} lap times (first 5):")
        for i, lap in enumerate(lap_times[first_driver][:5], 1):
            print(f"  Lap {i}: {lap['lap_time']} (P{lap['position']})")


def example_2_track_positions():
    """
    Example 2: Track position changes throughout the race
    """
    print("\n" + "="*80)
    print("EXAMPLE 2: Track Position Changes")
    print("="*80)
    
    client = F1StaticClient()
    url = client.get_timing_data_url(2023, "Italian Grand Prix", "Race")
    
    if not url:
        return
    
    print("\nFetching timing data...")
    timing_data = client.parse_jsonstream_simple(url, limit=100)  # First 100 updates
    
    # Track positions over time
    position_history = {}
    
    for entry in timing_data:
        lines = entry.get('Lines', {})
        
        for driver_num, driver_data in lines.items():
            position = driver_data.get('Position')
            
            if position:
                if driver_num not in position_history:
                    position_history[driver_num] = []
                
                position_history[driver_num].append({
                    'position': position,
                    'time': entry.get('_timestamp', '')
                })
    
    # Show position changes for top drivers
    print(f"\nTracking {len(position_history)} drivers")
    print("\nPosition history (first 10 updates):")
    
    for driver_num in sorted(position_history.keys())[:3]:
        print(f"\nDriver #{driver_num}:")
        for update in position_history[driver_num][:10]:
            print(f"  {update['time']}: P{update['position']}")


def example_3_session_metadata():
    """
    Example 3: Get session metadata (weather, track info, drivers)
    """
    print("\n" + "="*80)
    print("EXAMPLE 3: Session Metadata")
    print("="*80)
    
    client = F1StaticClient()
    base_url = client.get_event_session_url(2023, "Italian Grand Prix", "Race")
    
    if not base_url:
        return
    
    # Fetch SessionInfo.json
    print("\nFetching SessionInfo.json...")
    try:
        session_info_url = base_url + "SessionInfo.json"
        response = client.session.get(session_info_url)
        response.raise_for_status()
        
        session_info = json.loads(response.content.decode('utf-8-sig'))
        
        print("\n Session Information:")
        print(f"  Meeting: {session_info.get('Meeting', {}).get('Name', 'N/A')}")
        print(f"  Circuit: {session_info.get('Meeting', {}).get('Circuit', {}).get('ShortName', 'N/A')}")
        print(f"  Session: {session_info.get('Name', 'N/A')}")
        print(f"  Start: {session_info.get('StartDate', 'N/A')}")
        
    except Exception as e:
        print(f"  Error fetching session info: {e}")
    
    # Fetch DriverList.json
    print("\nFetching DriverList.json...")
    try:
        driver_list_url = base_url + "DriverList.json"
        response = client.session.get(driver_list_url)
        response.raise_for_status()
        
        drivers = json.loads(response.content.decode('utf-8-sig'))
        
        print(f"\n  {len(drivers)} drivers in session:")
        for driver_num, driver_info in list(drivers.items())[:5]:
            print(f"    #{driver_num}: {driver_info.get('FirstName')} {driver_info.get('LastName')} ({driver_info.get('Tla')})")
        
        if len(drivers) > 5:
            print(f"    ... and {len(drivers) - 5} more")
        
    except Exception as e:
        print(f"  Error fetching driver list: {e}")


def example_4_telemetry_data():
    """
    Example 4: Access telemetry data (Speed, RPM, Gear)
    """
    print("\n" + "="*80)
    print("EXAMPLE 4: Telemetry Data (CarData.z)")
    print("="*80)
    
    client = F1StaticClient()
    base_url = client.get_event_session_url(2023, "Italian Grand Prix", "Race")
    
    if not base_url:
        return
    
    car_data_url = base_url + "CarData.z.jsonStream"
    
    print(f"\nFetching compressed telemetry data...")
    print("Note: This file is large and compressed, limiting to first 5 entries")
    
    try:
        telemetry = client.parse_compressed_stream(car_data_url, limit=5)
        
        print(f"Loaded {len(telemetry)} telemetry updates")
        
        if telemetry:
            print("\nFirst telemetry entry:")
            print(json.dumps(telemetry[0], indent=2, default=str)[:500] + "...")
        
    except Exception as e:
        print(f"Error: {e}")


def example_5_compare_qualifying_sessions():
    """
    Example 5: Compare data from different sessions
    """
    print("\n" + "="*80)
    print("EXAMPLE 5: Compare Qualifying vs Race")
    print("="*80)
    
    client = F1StaticClient()
    
    # Get Qualifying data
    quali_url = client.get_timing_data_url(2023, "Italian Grand Prix", "Qualifying")
    
    # Get Race data  
    race_url = client.get_timing_data_url(2023, "Italian Grand Prix", "Race")
    
    if quali_url and race_url:
        print("\n[OK] Found both sessions:")
        print(f"  Qualifying: {quali_url.split('/')[-2]}")
        print(f"  Race: {race_url.split('/')[-2]}")
        print("\nYou can now compare data from both sessions!")
    else:
        print("\n[FAIL] Could not find one or both sessions")


def example_6_list_available_races():
    """
    Example 6: List all available races in a season
    """
    print("\n" + "="*80)
    print("EXAMPLE 6: List All 2023 Races")
    print("="*80)
    
    client = F1StaticClient()
    season = client.fetch_season_index(2023)
    
    meetings = season.get('Meetings', [])
    
    print(f"\nFound {len(meetings)} races in 2023:\n")
    
    for i, meeting in enumerate(meetings, 1):
        name = meeting.get('Name', 'Unknown')
        location = meeting.get('Location', 'Unknown')
        
        # Count sessions
        sessions = meeting.get('Sessions', [])
        
        print(f"{i:2d}. {name:30s} ({location})")
        print(f"    Sessions: {', '.join([s.get('Name', '') for s in sessions])}")


def run_all_examples():
    """Run all examples sequentially"""
    print("\n")
    print("="*80)
    print("         F1 STATIC CLIENT - PRACTICAL EXAMPLES")
    print("="*80)
    
    try:
        # example_1_get_lap_times()       # Takes ~5 seconds
        # example_2_track_positions()     # Takes ~3 seconds
        example_3_session_metadata()      # Fast
        example_4_telemetry_data()        # Fast (limited entries)
        example_5_compare_qualifying_sessions()  # Fast
        example_6_list_available_races()  # Fast
        
    except KeyboardInterrupt:
        print("\n\nExamples interrupted by user")
    except Exception as e:
        print(f"\n\nError: {e}")
    
    print("\n" + "="*80)
    print("Examples completed!")
    print("\nNote: Examples 1 & 2 are commented out as they fetch large datasets.")
    print("Uncomment them in the code to see full lap time and position analysis.")
    print("="*80)


if __name__ == "__main__":
    # Run all examples (some are commented out for speed)
    run_all_examples()
    
    # Or run individual examples:
    # example_3_session_metadata()
    # example_6_list_available_races()
