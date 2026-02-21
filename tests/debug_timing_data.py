"""Debug script to understand TimingData structure and find fastest laps"""
from src.data_loader.f1_static_client import F1StaticClient
import json
from collections import defaultdict
from datetime import datetime, timedelta

client = F1StaticClient()

# Test with 2025 Australian Grand Prix Qualifying
year = 2025
round_num = 2
session = "Q"

print(f"Testing {year} Round {round_num} {session}")
event_name = client.get_event_name(year, round_num)
print(f"Event: {event_name}")

base_url = client.get_event_session_url(year, event_name, session)
print(f"Base URL: {base_url}\n")

# Fetch TimingData
timing_url = base_url + "TimingData.jsonStream"
print(f"Fetching TimingData: {timing_url}")

timing_entries = client.parse_jsonstream_simple(timing_url, limit=100)
print(f"Loaded {len(timing_entries)} timing entries\n")

# Show structure of first few entries
print("=" * 80)
print("First 3 Timing Entries Structure:")
print("=" * 80)
for i, entry in enumerate(timing_entries[:3]):
    print(f"\nEntry {i}:")
    print(json.dumps(entry, indent=2, default=str)[:500])

# Look for lap time data
print("\n" + "=" * 80)
print("Searching for fastest lap data...")
print("=" * 80)

driver_laps = defaultdict(list)

for entry in timing_entries:
    lines = entry.get('Lines', {})
    
    for driver_num, driver_data in lines.items():
        # Look for lap time info
        if 'LastLapTime' in driver_data:
            lap_time = driver_data.get('LastLapTime', {})
            if 'Value' in lap_time:
                print(f"Driver {driver_num}: LastLapTime = {lap_time}")
        
        if 'BestLapTime' in driver_data:
            best_lap = driver_data.get('BestLapTime', {})
            if 'Value' in best_lap:
                print(f"Driver {driver_num}: BestLapTime = {best_lap}")
                driver_laps[driver_num].append(best_lap)
                
        if 'Sectors' in driver_data:
            sectors = driver_data.get('Sectors', [])
            print(f"Driver {driver_num}: Sectors = {sectors}")

print("\n" + "=" * 80)
print("Driver Best Lap Times Found:")
print("=" * 80)
for driver, laps in driver_laps.items():
    print(f"Driver {driver}: {len(laps)} lap time updates")
    if laps:
        print(f"  Sample: {laps[0]}")
