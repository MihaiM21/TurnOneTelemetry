"""
Check TimingStats.jsonStream for speed data
"""

import requests
import json

# Check a session we know has data
url_base = "https://livetiming.formula1.com/static/2023/2023-09-03_Italian_Grand_Prix/2023-09-03_Race/"

files_to_check = [
    ("TimingStats.jsonStream", "Timing Statistics"),
    ("TimingAppData.jsonStream", "Timing App Data"),
    ("LapSeries.json", "Lap Series Data")
]

for filename, description in files_to_check:
    print("\n" + "="*80)
    print(f"{description}: {filename}")
    print("="*80)
    
    url = url_base + filename
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            content = resp.content.decode('utf-8-sig')
            
            # Parse first few lines
            lines = content.strip().split('\n')
            print(f"Total lines: {len(lines)}")
            print(f"\nFirst few entries:")
            
            for i, line in enumerate(lines[:10]):
                # Try to parse
                parts = line.split('{"')
                if len(parts) >= 2:
                    timestamp = parts[0]
                    json_data = '{"' + parts[1]
                    
                    try:
                        data = json.loads(json_data)
                        print(f"\n  Entry {i+1} (timestamp: {timestamp}):")
                        
                        # Show structure (first 300 chars)
                        json_str = json.dumps(data, indent=2)
                        print(f"    {json_str[:500]}...")
                        
                        # Look for speed-related fields
                        def find_speed_fields(obj, path=""):
                            """Recursively find fields containing 'speed' or 'Speed'"""
                            if isinstance(obj, dict):
                                for key, value in obj.items():
                                    if 'speed' in key.lower():
                                        print(f"    → Found speed field: {path}.{key} = {value}")
                                    if isinstance(value, (dict, list)):
                                        find_speed_fields(value, f"{path}.{key}")
                            elif isinstance(obj, list) and len(obj) > 0:
                                find_speed_fields(obj[0], f"{path}[0]")
                        
                        find_speed_fields(data)
                        
                        if i >= 2:  # Show first 3 entries only
                            break
                    except:
                        pass
        else:
            print(f"  File not found (HTTP {resp.status_code})")
    except Exception as e:
        print(f"  Error: {e}")
