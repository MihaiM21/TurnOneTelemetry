import sys
import json
from src.data_loader.f1_static_client import F1StaticClient

client = F1StaticClient()
base_url = client.get_event_session_url(2025, 'Chinese Grand Prix', 'Qualifying')
timing_url = base_url + "TimingData.jsonStream"
data = client.parse_jsonstream_simple(timing_url)

printed = 0
for entry in data:
    if 'Lines' not in entry: continue
    t_str = entry.get('_timestamp', entry.get('T'))
    
    if '81' in entry['Lines']:
        line = entry['Lines']['81']
        if isinstance(line, dict):
            if 'LastLapTime' in line or 'BestLapTime' in line:
                print(f"Time={t_str}, Line={json.dumps(line)}")
                printed += 1
                if printed > 20: break
