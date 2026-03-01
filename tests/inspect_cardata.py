"""Inspect CarData structure in detail"""
from src.data_loader.f1_static_client import F1StaticClient
import json

client = F1StaticClient()
base_url = "https://livetiming.formula1.com/static/2023/2023-09-03_Italian_Grand_Prix/2023-09-03_Race/"

print("Analyzing CarData.z structure...")
entries = client.parse_compressed_stream(base_url + "CarData.z.jsonStream", limit=10)

print(f"Total entries: {len(entries)}")
print("\nFirst entry (full):")
print(json.dumps(entries[0], indent=2, default=str))

# Analyze the channels
print("\n" + "="*80)
print("Channel Analysis (checking multiple entries for patterns)")
print("="*80)

channel_values = {str(i): [] for i in range(50)}

for entry in entries[:10]:
    if isinstance(entry.get('Entries'), list):
        for item in entry['Entries']:
            cars = item.get('Cars', {})
            for driver_num, driver_data in cars.items():
                channels = driver_data.get('Channels', {})
                for ch_num, value in channels.items():
                    if value > 0:  # Only track non-zero values
                        channel_values[ch_num].append(value)

print("\nChannel value ranges (non-zero only):")
for ch, values in channel_values.items():
    if values:
        print(f"  Channel {int(ch):2d}: min={min(values):5d}, max={max(values):5d}, count={len(values):4d}")

print("\nBest guess at channel meanings:")
print("  Channel  0: Speed (km/h) - range 0-350")
print("  Channel  2: RPM / 10 - range 0-1500 -> 0-15000 RPM")
print("  Channel  3: Gear - range 0-8")
print("  Channel  4: Throttle (%) - range 0-100")
print("  Channel  5: Brake (%) - range 0-100")
print("  Channel 45: DRS - 0=off, 1=on")
