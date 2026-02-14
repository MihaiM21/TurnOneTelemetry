"""
Find the TRUE maximum Channel 0 value across entire CarData
"""

import requests
import json
import base64
import zlib

url = "https://livetiming.formula1.com/static/2023/2023-09-03_Italian_Grand_Prix/2023-09-03_Race/CarData.z.jsonStream"

print("Fetching ALL CarData...")
resp = requests.get(url)
content = resp.content.decode('utf-8-sig')

lines = content.strip().split('\n')
print(f"Total entries: {len(lines)}")

all_ch0_values = []

print("\nScanning ALL entries for Channel 0 values...")
for i, line in enumerate(lines):
    if i % 1000 == 0:
        print(f"  Processing entry {i}/{len(lines)}...")
    
    if '"' in line:
        try:
            parts = line.split('"')
            if len(parts) >= 2:
                base64_data = parts[1]
                decoded = base64.b64decode(base64_data)
                decompressed = zlib.decompress(decoded, wbits=-15)
                data = json.loads(decompressed)
                
                for entry in data.get('Entries', []):
                    for driver_num, car_data in entry.get('Cars', {}).items():
                        channels = car_data.get('Channels', {})
                        ch0 = channels.get('0', 0)
                        
                        if ch0 > 0:
                            all_ch0_values.append(ch0)
        except:
            pass

print(f"\nTotal Channel 0 values collected: {len(all_ch0_values)}")

if all_ch0_values:
    max_ch0 = max(all_ch0_values)
    min_ch0 = min(all_ch0_values)
    
    print(f"\nChannel 0 statistics:")
    print(f"  Min: {min_ch0}")
    print(f"  Max: {max_ch0}")
    
    print(f"\n{'='*80}")
    print("TESTING DIFFERENT SCALE FACTORS:")
    print(f"{'='*80}")
    
    # Monza expected top speed: ~355-360 km/h
    expected_monza = 358.0
    
    print(f"\nExpected Monza top speed: {expected_monza} km/h")
    print(f"Channel 0 maximum: {max_ch0}")
    
    # Test different divisors
    for divisor in [10, 20, 30, 35, 37, 38, 40]:
        result = max_ch0 / divisor
        print(f"  {max_ch0} / {divisor} = {result:.1f} km/h")
    
    # Calculate exact factor
    exact_factor = max_ch0 / expected_monza
    print(f"\nExact factor: {max_ch0} / {expected_monza} = {exact_factor:.2f}")
    print(f"So: Channel_0 / {exact_factor:.2f} = km/h")
    
    # Show top 20 highest values
    top_20 = sorted(all_ch0_values, reverse=True)[:20]
    print(f"\nTop 20 highest Channel 0 values:")
    for i, val in enumerate(top_20, 1):
        print(f"  {i:2d}. {val:5d} → {val/exact_factor:.1f} km/h")
