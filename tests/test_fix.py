import sys
import pandas as pd
from src.data_loader.f1_static_client import F1StaticClient
from src.scripts.simple.v2_throttle_comparison import parse_f1_time

def get_fastest_lap_windows_pandas_test(base_url: str, client: F1StaticClient) -> pd.DataFrame:
    columns = ['Driver', 'StartTime', 'EndTime', 'LapTime']
    timing_url = base_url + "TimingData.jsonStream"
    
    data = client.parse_jsonstream_simple(timing_url)
    best_laps = {}

    for entry in data:
        if 'Lines' not in entry: continue
        
        t_str = entry.get('_timestamp', entry.get('T'))
        if not t_str: continue
        t = parse_f1_time(t_str)
        
        for drv, line in entry['Lines'].items():
            if isinstance(line, dict):
                last_lap_data = line.get('LastLapTime', {})
                if not isinstance(last_lap_data, dict):
                    continue
                    
                llt = last_lap_data.get('Value', '')
                if llt and llt != '':
                    lap_time = parse_f1_time(llt)
                    if lap_time <= 0: continue
                    
                    if drv not in best_laps or lap_time < best_laps[drv]['LapTime']:
                        best_laps[drv] = {
                            'Driver': str(drv),
                            'StartTime': t - lap_time,
                            'EndTime': t,
                            'LapTime': lap_time
                        }
    return pd.DataFrame(list(best_laps.values()))

client = F1StaticClient()
base_url = client.get_event_session_url(2025, 'Chinese Grand Prix', 'Qualifying')
df_w = get_fastest_lap_windows_pandas_test(base_url, client)
print("Windows:\n", df_w[df_w['Driver'].isin(['81', '44', '1'])])
