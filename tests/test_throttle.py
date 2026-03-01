import sys
import json
import fastf1
sys.path.append('.')

from src.data_loader.f1_static_client import F1StaticClient
from src.scripts.simple.v2_throttle_comparison import process_throttle_data
from src.scripts.simple.throttle_comparison import ThrottleCompData

y = 2025
r = 2
e = "Qualifying"

out = {}
try:
    v1_data = ThrottleCompData(y, r, e, store_to_mongo=False)
    out['v1'] = v1_data
except Exception as ex:
    out['v1_err'] = str(ex)

client = F1StaticClient()
try:
    v2_data = process_throttle_data(y, r, e, client)
    out['v2'] = v2_data
except Exception as ex:
    out['v2_err'] = str(ex)

with open('test_output.json', 'w') as f:
    json.dump(out, f, indent=2)

