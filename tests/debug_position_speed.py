"""
Deep dive into Position.z to find speed data
"""

import requests
import json
import base64
import zlib

url = "https://livetiming.formula1.com/static/2023/2023-09-03_Italian_Grand_Prix/2023-09-03_Race/Position.z.jsonStream"

print("Fetching Position.z...")
resp = requests.get(url)
content = resp.content.decode('utf-8-sig')

lines = content.strip().split('\n')
print(f"Total lines: {len(lines)}")

# Parse several entries to understand structure
for i, line in enumerate(lines[5000:5010]):  # Mid-race where speeds are high
    if '"' in line:
        try:
            parts = line.split('"')
            if len(parts) >= 2:
                timestamp = parts[0]
                base64_data = parts[1]
                decoded = base64.b64decode(base64_data)
                decompressed = zlib.decompress(decoded, wbits=-15)
                data = json.loads(decompressed)
                
                print(f"\n{'='*80}")
                print(f"Entry {i+5000} (timestamp: {timestamp}):")
                print(f"{'='*80}")
                print(json.dumps(data, indent=2))
                
                # Only show first 2 entries to see structure
                if i >= 2:
                    break
        except Exception as e:
            print(f"Error: {e}")
