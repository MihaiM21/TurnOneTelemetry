"""Debug CarData.z structure"""
import requests
import json
import base64
import zlib

url = "https://livetiming.formula1.com/static/2023/2023-09-03_Italian_Grand_Prix/2023-09-03_Race/CarData.z.jsonStream"

print("Fetching CarData.z.jsonStream...")
response = requests.get(url)
content = response.content.decode('utf-8-sig')

# Split into lines
lines = content.replace('\r\n', '\n').strip().split('\n')
print(f"Total lines: {len(lines)}")

# Show first few lines
print("\nFirst 3 lines (raw):")
for i in range(min(3, len(lines))):
    line = lines[i]
    print(f"\nLine {i} (length {len(line)}):")
    print(repr(line[:200]))
    
    # Try to extract timestamp and JSON
    json_start = -1
    for idx, char in enumerate(line):
        if char in ('{', '['):
            json_start = idx
            break
    
    if json_start >= 0:
        timestamp = line[:json_start].strip()
        json_str = line[json_start:]
        
        print(f"  Timestamp: {timestamp}")
        
        try:
            obj = json.loads(json_str)
            print(f"  JSON keys: {list(obj.keys())}")
            
            # Show structure
            for key, value in obj.items():
                if isinstance(value, str) and len(value) > 50:
                    print(f"    {key}: <string length {len(value)}> (possibly Base64)")
                    
                    # Try to decompress
                    try:
                        compressed = base64.b64decode(value)
                        decompressed = zlib.decompress(compressed, wbits=-15)
                        decompressed_str = decompressed.decode('utf-8-sig')
                        print(f"      Decompressed to {len(decompressed_str)} chars")
                        print(f"      First 200 chars: {repr(decompressed_str[:200])}")
                    except Exception as e:
                        print(f"      Decompression failed: {e}")
                elif isinstance(value, dict):
                    print(f"    {key}: <dict with {len(value)} keys>")
                elif isinstance(value, list):
                    print(f"    {key}: <list with {len(value)} items>")
                else:
                    print(f"    {key}: {value}")
                    
        except Exception as e:
            print(f"  JSON parse error: {e}")
