import sys
from src.data_loader.f1_static_client import F1StaticClient
from src.scripts.simple.v2_throttle_comparison import parse_f1_time

client = F1StaticClient()
base_url = client.get_event_session_url(2025, 'Chinese Grand Prix', 'Qualifying')
timing_url = base_url + "TimingData.jsonStream"
data = client.parse_jsonstream_simple(timing_url)

for entry in data:
    if 'Lines' not in entry: continue
    
    t_str = entry.get('_timestamp', entry.get('T'))
    if not t_str: continue
    t = parse_f1_time(t_str)
    
    if '81' in entry['Lines']:
        line = entry['Lines']['81']
        if isinstance(line, dict):
            last_lap_data = line.get('LastLapTime', {})
            if isinstance(last_lap_data, dict):
                is_pf = last_lap_data.get('PersonalFastest')
                llt = last_lap_data.get('Value', '')
                if llt:
                    print(f"Time={t_str}, PF={is_pf}, V={llt}, parsed={parse_f1_time(llt)}")
