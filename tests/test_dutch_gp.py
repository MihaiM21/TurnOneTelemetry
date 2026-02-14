"""
Test to compare console output vs plot data for Dutch GP
"""

from src.scripts.simple.v2_top_speed import TopSpeedPlot_V2, extract_top_speeds_from_telemetry, get_driver_team_mapping
from src.data_loader.f1_static_client import F1StaticClient

# Test with the exact same session from the screenshot
year = 2025
event = "Dutch Grand Prix"  
session = "Qualifying"

print(f"Testing: {year} {event} {session}")
print("="*80)

client = F1StaticClient()
base_url = client.get_event_session_url(year, event, session)

if base_url:
    print(f"Session found: {base_url}\n")
    
    # Get driver-team mapping
    driver_to_team = get_driver_team_mapping(base_url, client)
    
    # Extract speeds
    team_speeds = extract_top_speeds_from_telemetry(base_url, client, driver_to_team)
    
    if team_speeds:
        print("\n" + "="*80)
        print("CONSOLE OUTPUT (what user sees):")
        print("="*80)
        sorted_teams = sorted(team_speeds.items(), key=lambda x: x[1], reverse=True)
        for team, speed in sorted_teams:
            print(f"  {team:20s}: {int(speed)} km/h")
        
        print("\n" + "="*80)
        print("EXPECTED (from plot image):")
        print("="*80)
        print("  Williams            : 334 km/h")
        print("  Red Bull Racing     : 333 km/h")
        print("  Ferrari             : 332 km/h")
        print("  Aston Martin        : 332 km/h")
        print("  McLaren             : 330 km/h")
        print("  Haas F1 Team        : 330 km/h")
        print("  Kick Sauber         : 330 km/h")
        print("  Alpine              : 329 km/h")
        print("  Racing Bulls        : 327 km/h")
        print("  Mercedes            : 326 km/h")
        
        print("\n" + "="*80)
        print("ANALYSIS:")
        print("="*80)
        # Compare console vs expected
        expected = {"Mercedes": 326, "Racing Bulls": 327, "Alpine": 329}
        for team, expected_speed in expected.items():
            if team in team_speeds:
                console_speed = int(team_speeds[team])
                diff = console_speed - expected_speed
                match = "✓ MATCH" if abs(diff) <= 2 else f"✗ OFF BY {diff} km/h"
                print(f"  {team:20s}: Console={console_speed:3d} km/h, Expected={expected_speed:3d} km/h → {match}")
else:
    print("Session not found (data not available yet)")
