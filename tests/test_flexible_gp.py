import os
import sys
import json

# Add src to python path so imports work
sys.path.insert(0, os.path.abspath('.'))

from src.data_loader.f1_static_client import F1StaticClient

def test():
    client = F1StaticClient()
    year = 2026
    
    results = {}
    
    results['int_round_1'] = client.get_event_info(year, 1)
    results['str_round_1'] = client.get_event_info(year, '1')
    results['int_key_1304'] = client.get_event_info(year, 1304)
    results['str_key_1304'] = client.get_event_info(year, '1304')
    results['str_name'] = client.get_event_info(year, 'FORMULA 1 ARAMCO PRE-SEASON TESTING 1 2026')
    
    with open('test_results.json', 'w') as f:
        json.dump(results, f, indent=4)

if __name__ == '__main__':
    test()
