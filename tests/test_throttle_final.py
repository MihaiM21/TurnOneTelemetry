import sys
import logging
logging.getLogger("src.data_loader.f1_static_client").setLevel(logging.CRITICAL)

from src.data_loader.f1_static_client import F1StaticClient
from src.scripts.simple.v2_throttle_comparison import process_throttle_data
import json

client = F1StaticClient()
try:
    res = process_throttle_data(2025, 'Chinese Grand Prix', 'Qualifying', client)
    print("NEW THROTTLE DATA:")
    for d in res:
        if d['Driver'] == 'PIA':
            print("PIASTRI:", json.dumps(d))
except Exception as e:
    print(e)
