import sys
from src.data_loader.f1_static_client import F1StaticClient
from src.scripts.simple.v2_throttle_comparison import get_fastest_lap_windows_pandas, extract_telemetry_pandas

client = F1StaticClient()
base_url = client.get_event_session_url(2025, 'Chinese Grand Prix', 'Qualifying')
print("Base URL:", base_url)

df_windows = get_fastest_lap_windows_pandas(base_url, client)
print("Windows:\n", df_windows[df_windows['Driver'].isin(['81', '44', '1'])])

df_telemetry = extract_telemetry_pandas(base_url, client)
print("Telemetry Piastri len:", len(df_telemetry[df_telemetry['Driver'] == '81']))

import pandas as pd
df_piastri_window = df_windows[df_windows['Driver'] == '81']
if not df_piastri_window.empty:
    st = df_piastri_window.iloc[0]['StartTime']
    et = df_piastri_window.iloc[0]['EndTime']
    print("Piastri window:", st, et)
    drv_tel = df_telemetry[(df_telemetry['Driver'] == '81') & (df_telemetry['Time'] >= st) & (df_telemetry['Time'] <= et)]
    print("Lap tel len:", len(drv_tel))
    if not drv_tel.empty:
        print("Avg Throttle:", drv_tel['Throttle'].clip(0, 100).mean())
        print(drv_tel.describe())
