"""Debug throttle extraction to find why values exceed 100%"""
from src.data_loader.f1_static_client import F1StaticClient
from collections import defaultdict
import json

client = F1StaticClient()

# Test with 2025 Australian Grand Prix Qualifying (same as user's example)
year = 2025
round_num = 2  # Australian Grand Prix is Round 2 in 2025
session = "Q"

print(f"Testing {year} Round {round_num} {session}")
event_name = client.get_event_name(year, round_num)
print(f"Event: {event_name}")

base_url = client.get_event_session_url(year, event_name, session)
print(f"Base URL: {base_url}")

car_data_url = base_url + "CarData.z.jsonStream"
print(f"\nFetching: {car_data_url}")

telemetry_entries = client.parse_compressed_stream(car_data_url, limit=1000)  # More entries
print(f"Loaded {len(telemetry_entries)} entries")

# Show structure of first entry
print("\nFirst entry structure:")
print(json.dumps(telemetry_entries[0], indent=2, default=str)[:500])

# Extract throttle values
driver_throttle_values = defaultdict(list)

for entry in telemetry_entries[:1000]:  # First 1000 entries
    entries_list = entry.get('Entries', [])
    
    if not isinstance(entries_list, list):
        entries_list = [entries_list]

    for item in entries_list:
        cars = item.get('Cars', {})
        for driver_num, driver_data in cars.items():
            channels = driver_data.get('Channels', {})
            throttle = channels.get('4', 0)  # Channel 4
            
            # Filter out invalid/magic values
            if throttle and throttle > 0 and throttle <= 100:
                driver_throttle_values[driver_num].append(throttle)

# Show stats for each driver
print("\n" + "="*80)
print("Throttle Statistics (first 1000 entries, excluding invalid values >100):")
print("="*80)

for driver_num in sorted(driver_throttle_values.keys()):
    values = driver_throttle_values[driver_num]
    if values:
        avg = sum(values) / len(values)
        print(f"Driver {driver_num:2s}: "
              f"Count={len(values):4d}, "
              f"Min={min(values):6.2f}, "
              f"Max={max(values):6.2f}, "
              f"Avg={avg:6.2f}")
        
        # Show sample values
        sample = values[:10]
        print(f"  First 10 values: {sample}")
        
        # Check if any values exceed 100
        over_100 = [v for v in values if v > 100]
        if over_100:
            print(f"  ⚠️  WARNING: {len(over_100)} values exceed 100%!")
            print(f"      Examples: {over_100[:5]}")
