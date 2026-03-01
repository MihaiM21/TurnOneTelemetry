"""
Analyze CarData to understand the correct speed scaling
"""

import requests
import json
import base64
import zlib

def analyze_cardata_speeds():
    """Analyze CarData channel values to determine correct speed scaling"""
    
    # Use 2023 Italian GP (Monza - high speed circuit)
    url = "https://livetiming.formula1.com/static/2023/2023-09-03_Italian_Grand_Prix/2023-09-03_Race/CarData.z.jsonStream"
    
    print("Fetching CarData...")
    resp =requests.get(url)
    content = resp.content.decode('utf-8-sig')
    
    # Parse the stream
    lines = content.strip().split('\n')
    print(f"Total entries: {len(lines)}")
    
    # Collect all Channel 0 values (supposedly speed)
    all_channel_0 = []
    all_channel_2 = []
    all_channel_3 = []
    all_channel_4 = []
    all_channel_5 = []
    
    sample_count = 0
    max_sample = 1000  # Sample first 1000 entries
    
    for line in lines[:max_sample]:
        if '"' in line:
            try:
                parts = line.split('"')
                if len(parts) >= 2:
                    base64_data = parts[1]
                    decoded = base64.b64decode(base64_data)
                    decompressed = zlib.decompress(decoded, wbits=-15)
                    data = json.loads(decompressed)
                    
                    for entry in data.get('Entries', []):
                        for driver_num, car_data in entry.get('Cars', {}).items():
                            channels = car_data.get('Channels', {})
                            
                            ch0 = channels.get('0', 0)
                            ch2 = channels.get('2', 0)
                            ch3 = channels.get('3', 0)
                            ch4 = channels.get('4', 0)
                            ch5 = channels.get('5', 0)
                            
                            if ch0 > 0:
                                all_channel_0.append(ch0)
                            if ch2 > 0:
                                all_channel_2.append(ch2)
                            if ch3 > 0:
                                all_channel_3.append(ch3)
                            if ch4 > 0:
                                all_channel_4.append(ch4)
                            if ch5 > 0:
                                all_channel_5.append(ch5)
                    
                    sample_count += 1
            except Exception as e:
                pass
    
    print(f"\nAnalyzed {sample_count} entries")
    print("\n" + "="*80)
    print("CHANNEL ANALYSIS:")
    print("="*80)
    
    if all_channel_0:
        print(f"\nChannel 0 (Speed?):")
        print(f"  Min: {min(all_channel_0)}")
        print(f"  Max: {max(all_channel_0)}")
        print(f"  Avg: {sum(all_channel_0)/len(all_channel_0):.1f}")
        print(f"  Max/10: {max(all_channel_0)/10:.1f} km/h")
        print(f"  Sample values: {sorted(set(list(all_channel_0)[-20:]))}")
    
    if all_channel_2:
        print(f"\nChannel 2 (RPM/10?):")
        print(f"  Min: {min(all_channel_2)}")
        print(f"  Max: {max(all_channel_2)}")
        print(f"  Avg: {sum(all_channel_2)/len(all_channel_2):.1f}")
        print(f"  Max*10: {max(all_channel_2)*10} RPM")
    
    if all_channel_3:
        print(f"\nChannel 3 (Gear?):")
        print(f"  Min: {min(all_channel_3)}")
        print(f"  Max: {max(all_channel_3)}")
        print(f"  Values: {sorted(set(all_channel_3))}")
    
    if all_channel_4:
        print(f"\nChannel 4 (Throttle %?):")
        print(f"  Min: {min(all_channel_4)}")
        print(f"  Max: {max(all_channel_4)}")
        print(f"  Avg: {sum(all_channel_4)/len(all_channel_4):.1f}")
        if max(all_channel_4) <= 100:
            print(f"  ✓ Values look like percentages (0-100)")
        else:
            print(f"  ✗ Values exceed 100% - might not be throttle")
    
    if all_channel_5:
        print(f"\nChannel 5 (Brake %?):")
        print(f"  Min: {min(all_channel_5)}")
        print(f"  Max: {max(all_channel_5)}")
        print(f"  Avg: {sum(all_channel_5)/len(all_channel_5):.1f}")
        if max(all_channel_5) <= 100:
            print(f"  ✓ Values look like percentages (0-100)")
        else:
            print(f"  ✗ Values exceed 100% - might not be brake")
    
    # Check if Channel 0 divided by 10 gives reasonable F1 speeds
    print("\n" + "="*80)
    print("SPEED VALIDATION:")
    print("="*80)
    
    if all_channel_0:
        max_ch0 = max(all_channel_0)
        speed_div_10 = max_ch0 / 10.0
        
        print(f"\nMonza (Italian GP) expected top speed: ~355-360 km/h")
        print(f"Channel 0 max value: {max_ch0}")
        print(f"Channel 0 max / 10: {speed_div_10:.1f} km/h")
        
        if 350 <= speed_div_10 <= 370:
            print(f"  ✓ MATCH! Channel 0 / 10 gives realistic Monza speed")
        else:
            print(f"  ✗ MISMATCH! Expected ~355-360 km/h, got {speed_div_10:.1f} km/h")
    
    # Now check Position.z for comparison
    print("\n" + "="*80)
    print("COMPARING WITH Position.z.jsonStream:")
    print("="*80)
    
    position_url = "https://livetiming.formula1.com/static/2023/2023-09-03_Italian_Grand_Prix/2023-09-03_Race/Position.z.jsonStream"
    
    try:
        print("Fetching Position data...")
        resp = requests.get(position_url)
        content = resp.content.decode('utf-8-sig')
        
        lines = content.strip().split('\n')
        
        position_speeds = []
        
        for line in lines[:500]:  # Sample first 500
            if '"' in line:
                try:
                    parts = line.split('"')
                    if len(parts) >= 2:
                        base64_data = parts[1]
                        decoded = base64.b64decode(base64_data)
                        decompressed = zlib.decompress(decoded, wbits=-15)
                        data = json.loads(decompressed)
                        
                        for pos_entry in data.get('Position', []):
                            entries = pos_entry.get('Entries', {})
                            for driver_num, pos_data in entries.items():
                                speed = pos_data.get('Speed', 0)
                                if speed > 0:
                                    position_speeds.append(speed)
                except:
                    pass
        
        if position_speeds:
            print(f"\nPosition data 'Speed' field:")
            print(f"  Min: {min(position_speeds)}")
            print(f"  Max: {max(position_speeds)}")
            print(f"  Avg: {sum(position_speeds)/len(position_speeds):.1f}")
            print(f"\n  ✓ Position.z has explicit 'Speed' field in km/h")
        else:
            print("\n  No Speed field found in Position data")
    
    except Exception as e:
        print(f"Error analyzing Position data: {e}")


if __name__ == "__main__":
    analyze_cardata_speeds()
