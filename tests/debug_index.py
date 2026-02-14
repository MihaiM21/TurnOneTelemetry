"""Debug script to inspect F1 Index.json structure"""

import requests
import json

# Fetch and inspect the 2023 season index
url = "https://livetiming.formula1.com/static/2023/Index.json"
response = requests.get(url)
content = response.content.decode('utf-8-sig')
data = json.loads(content)

print("Top-level keys:", list(data.keys()))
print("\n" + "="*80)

if 'Meetings' in data:
    print(f"\nFound {len(data['Meetings'])} meetings")
    
    # Find Italian GP
    italian_gp = None
    for meeting in data['Meetings']:
        if 'italian' in meeting.get('Name', '').lower():
            italian_gp = meeting
            break
    
    if italian_gp:
        print("\nItalian Grand Prix structure:")
        print(json.dumps(italian_gp, indent=2))
    else:
        print("\n❌ Italian GP not found")
        print("\nFirst meeting structure:")
        print(json.dumps(data['Meetings'][0], indent=2))
else:
    print("Full structure:")
    print(json.dumps(data, indent=2)[:2000])
