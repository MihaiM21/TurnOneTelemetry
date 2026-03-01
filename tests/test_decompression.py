"""Test the fixed decompression with F1StaticClient"""
from src.data_loader.f1_static_client import F1StaticClient

client = F1StaticClient()
base_url = "https://livetiming.formula1.com/static/2023/2023-09-03_Italian_Grand_Prix/2023-09-03_Race/"

print("Testing fixed CarData.z decompression...")
print("="*80)

entries = client.parse_compressed_stream(base_url + "CarData.z.jsonStream", limit=3)

print(f"\nSuccessfully parsed {len(entries)} entries!")

if entries:
    print("\nFirst entry structure:")
    import json
    print(json.dumps(entries[0], indent=2, default=str)[:1000])
    
    print("\n" + "="*80)
    print("Keys in first entry:", list(entries[0].keys()))
    
    # Check if we have telemetry data
    if 'Entries' in entries[0]:
        print(f"Number of drivers in telemetry: {len(entries[0]['Entries'])}")
        first_driver = list(entries[0]['Entries'].keys())[0]
        print(f"Sample driver data ({first_driver}):", entries[0]['Entries'][first_driver])
