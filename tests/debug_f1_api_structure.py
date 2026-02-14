"""
Debug script to investigate the actual F1 API structure
and verify which data sources contain speed information
"""

import requests
import json
import base64
import zlib

def investigate_session_data(year, event_partial, session_partial):
    """Investigate what data is available for a session"""
    
    # 1. Fetch Index.json
    base_url = f"https://livetiming.formula1.com/static/"
    index_url = base_url + f"{year}/Index.json"
    
    print(f"Fetching: {index_url}")
    response = requests.get(index_url)
    index_data = json.loads(response.content.decode('utf-8-sig'))
    
    # 2. Find the event
    event_data = None
    for meeting in index_data.get('Meetings', []):
        if event_partial.lower() in meeting.get('Name', '').lower():
            event_data = meeting
            print(f"\nFound event: {meeting.get('Name')}")
            print(f"Location: {meeting.get('Location')}")
            break
    
    if not event_data:
        print(f"Event '{event_partial}' not found!")
        return
    
    # 3. Find the session
    session_data = None
    for session in event_data.get('Sessions', []):
        if session_partial.lower() in session.get('Name', '').lower():
            session_data = session
            print(f"Found session: {session.get('Name')}")
            break
    
    if not session_data:
        print(f"Session '{session_partial}' not found!")
        return
    
    # 4. Build session URL
    session_path = session_data.get('Path')
    session_url = base_url + session_path
    print(f"\nSession URL: {session_url}")
    
    # 5. List all available files by trying common ones
    print("\n" + "="*80)
    print("AVAILABLE DATA FILES:")
    print("="*80)
    
    test_files = [
        "CarData.z.jsonStream",
        "Position.z.jsonStream",
        "TimingData.jsonStream",
        "DriverList.json",
        "SessionInfo.json",
        "TrackStatus.jsonStream",
        "WeatherData.jsonStream",
        "RaceControlMessages.json",
        "SessionData.json",
        "LapSeries.json",
        "TimingAppData.jsonStream",
        "TimingStats.jsonStream"
    ]
    
    available_files = []
    for filename in test_files:
        url = session_url + filename
        try:
            resp = requests.head(url, timeout=5)
            if resp.status_code == 200:
                size_kb = int(resp.headers.get('content-length', 0)) / 1024
                print(f"  ✓ {filename:30s} ({size_kb:.1f} KB)")
                available_files.append(filename)
            else:
                print(f"  ✗ {filename:30s} (not found)")
        except:
            print(f"  ✗ {filename:30s} (error)")
    
    # 6. Examine CarData structure
    print("\n" + "="*80)
    print("EXAMINING CarData.z.jsonStream STRUCTURE:")
    print("="*80)
    
    if "CarData.z.jsonStream" in available_files:
        cardata_url = session_url + "CarData.z.jsonStream"
        resp = requests.get(cardata_url)
        content = resp.content.decode('utf-8-sig')
        
        # Parse first few entries
        lines = content.strip().split('\nL')
        print(f"Total lines: {len(lines)}")
        print(f"\nFirst line format: {lines[0][:200]}...")
        
        # Try to parse first entry
        first_line = lines[0]
        if '"' in first_line:
            # Pattern A: TIMESTAMP"BASE64DATA"
            parts = first_line.split('"')
            if len(parts) >= 2:
                timestamp = parts[0]
                base64_data = parts[1]
                
                print(f"\nTimestamp: {timestamp}")
                print(f"Base64 data length: {len(base64_data)}")
                
                # Decompress
                try:
                    decoded = base64.b64decode(base64_data)
                    decompressed = zlib.decompress(decoded, wbits=-15)
                    data = json.loads(decompressed)
                    
                    print(f"\nDecompressed data structure:")
                    print(json.dumps(data, indent=2)[:1000])
                    
                    # Examine channels
                    if 'Entries' in data:
                        for entry in data['Entries'][:1]:  # First entry only
                            if 'Cars' in entry:
                                for driver_num, car_data in entry['Cars'].items():
                                    print(f"\n Driver {driver_num} channels:")
                                    channels = car_data.get('Channels', {})
                                    for ch_id, ch_value in channels.items():
                                        print(f"   Channel {ch_id}: {ch_value}")
                                    break  # Just show one driver
                            break
                except Exception as e:
                    print(f"Error decompressing: {e}")
    
    # 7. Examine Position data (which definitely has speed)
    print("\n" + "="*80)
    print("EXAMINING Position.z.jsonStream STRUCTURE:")
    print("="*80)
    
    if "Position.z.jsonStream" in available_files:
        position_url = session_url + "Position.z.jsonStream"
        resp = requests.get(position_url)
        content = resp.content.decode('utf-8-sig')
        
        lines = content.strip().split('\n')
        print(f"Total lines: {len(lines)}")
        
        # Try to parse a few entries
        for i, line in enumerate(lines[:3]):
            if '"' in line:
                parts = line.split('"')
                if len(parts) >= 2:
                    timestamp = parts[0]
                    base64_data = parts[1]
                    
                    try:
                        decoded = base64.b64decode(base64_data)
                        decompressed = zlib.decompress(decoded, wbits=-15)
                        data = json.loads(decompressed)
                        
                        print(f"\nEntry {i+1}:")
                        print(json.dumps(data, indent=2)[:500])
                        
                        # Look for speed field
                        if 'Position' in data:
                            for driver_num, pos_data in list(data['Position'].items())[:1]:
                                print(f"\nDriver {driver_num} position data:")
                                for key, value in pos_data.items():
                                    print(f"  {key}: {value}")
                                break
                        break
                    except Exception as e:
                        print(f"Error: {e}")

    # 8. Check TimingData
    print("\n" + "="*80)
    print("EXAMINING TimingData.jsonStream:")
    print("="*80)
    
    if "TimingData.jsonStream" in available_files:
        timing_url = session_url + "TimingData.jsonStream"
        resp = requests.get(timing_url)
        content = resp.content.decode('utf-8-sig')
        
        lines = content.strip().split('\n')
        print(f"Total lines: {len(lines)}")
        print(f"\nFirst few entries:")
        
        for i, line in enumerate(lines[:5]):
            # This is plain JSON, not compressed
            parts = line.split('{"')
            if len(parts) >= 2:
                try:
                    timestamp = parts[0]
                    json_data = '{"' + parts[1]
                    data = json.loads(json_data)
                    print(f"\nEntry {i+1} (timestamp: {timestamp}):")
                    print(json.dumps(data, indent=2)[:300])
                except:
                    pass


# Run investigation
if __name__ == "__main__":
    print("F1 API STRUCTURE INVESTIGATION")
    print("="*80)
    
    investigate_session_data(2023, "Italian", "Race")
