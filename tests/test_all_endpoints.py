import sys
import os
import json
import time
import argparse

sys.path.insert(0, os.path.abspath('.'))

from fastapi.testclient import TestClient
from server import app
from src.data_loader.f1_static_client import F1StaticClient

def get_meetings(year):
    client = F1StaticClient()
    try:
        index = client.fetch_season_index(year)
        return index.get('Meetings', [])
    except Exception as e:
        print(f"Error fetching meetings for {year}: {e}")
        return []

def main():
    parser = argparse.ArgumentParser(description="F1 Telemetry Endpoint Validation Tool")
    parser.add_argument('--v1', action='store_true', help='Test only V1 endpoints')
    parser.add_argument('--v2', action='store_true', help='Test only V2 endpoints')
    parser.add_argument('--both', action='store_true', help='Test both V1 and V2 endpoints (Default behavior)')
    args = parser.parse_args()
    
    # Determine what to test based on args
    test_v1 = args.v1 or args.both or (not args.v1 and not args.v2)
    test_v2 = args.v2 or args.both or (not args.v1 and not args.v2)

    print("="*60)
    print("       F1 TELEMETRY ENDPOINT VALIDATION TOOL")
    print(f"       TESTING: {'V1 ' if test_v1 else ''}{'& ' if test_v1 and test_v2 else ''}{'V2' if test_v2 else ''}")
    print("="*60)
    print("\nInitializing TestClient...")
    
    headers = {"X-API-Key": "mishu"}
    
    results = {
        "passed": [],
        "failed": [],
        "errors": []
    }
    
    # ------------------ DEFINE ENDPOINTS ------------------
    v1_data = [
        ('/api/v1/top-speed-data', {}),
        ('/api/v1/throttle-comparison-data', {}),
        ('/api/v1/qualifying-results-data', {}),
        ('/api/v1/laptimes', {'driver': 'VER'}),
        ('/api/v1/track-comparison-2drivers-data', {'driver1': 'VER', 'driver2': 'HAM'}),
        ('/api/v1/throttleBrake-comparison-2drivers-data', {'driver1': 'VER', 'driver2': 'HAM'})
    ]
    
    v1_plots = [
        ('/api/v1/top-speed-plot', {}),
        ('/api/v1/throttle-comparison-plot', {}),
        ('/api/v1/qualifying-results-plot', {}),
        ('/api/v1/track-comparison-2drivers-plot', {'driver1': 'VER', 'driver2': 'HAM'}),
        ('/api/v1/throttleBrake-comparison-2drivers-plot', {'driver1': 'VER', 'driver2': 'HAM'})
    ]
    
    v2_data = [
        ('/api/v2/top-speed-telemetry-data', {}),
        ('/api/v2/top-speed-st-data', {}),
        ('/api/v2/throttle-comparison-data', {})
    ]
    
    v2_plots = [
        ('/api/v2/top-speed-telemetry-plot', {}),
        ('/api/v2/top-speed-st-plot', {}),
        ('/api/v2/throttle-comparison-plot', {})
    ]
    
    all_endpoints = []
    if test_v1:
        all_endpoints.extend(v1_data + v1_plots)
    if test_v2:
        all_endpoints.extend(v2_data + v2_plots)
        
    years = [2025]
    
    print(f"\nTotal endpoints to test per GP: {len(all_endpoints)}")
    print("Starting tests... Please be patient, generating fresh plots across multiple GPs will take significant time.")
    
    with TestClient(app) as test_client:
        for year in years:
            meetings = get_meetings(year)
            print(f"\n\n{'='*20} Testing Year {year} ({len(meetings)} GPs) {'='*20}")
            
            for meeting in meetings:
                chronological_round = meetings.index(meeting) + 1
                gp_name = meeting.get('Name')
                session_to_test = "Day 1" if "Testing" in gp_name else "Q"
                
                print(f"\n--- Testing GP: {gp_name} (Round {chronological_round}, Session {session_to_test}) ---")
                
                for endpoint, extra_params in all_endpoints:
                    # Use 'Q' (Qualifying) session which is most consistently complete, or 'Day 1' for testing
                    params = {"year": year, "gp": chronological_round, "session": session_to_test}
                    params.update(extra_params)
                    
                    print(f"  -> GET {endpoint.ljust(50)}", end="", flush=True)
                    start_time = time.time()
                    try:
                        response = test_client.get(endpoint, params=params, headers=headers)
                        duration = time.time() - start_time
                        if response.status_code == 200:
                            print(f" [PASS] ({duration:.2f}s)")
                            results["passed"].append({
                                "year": year, "gp": gp_name, "endpoint": endpoint, "duration": duration
                            })
                        else:
                            try:
                                error_msg = response.json()
                            except:
                                error_msg = response.text
                            print(f" [FAIL] ({response.status_code}) - {str(error_msg)[:100]}...")
                            results["failed"].append({
                                "year": year, "gp": gp_name, "endpoint": endpoint, "status": response.status_code, "error": error_msg
                            })
                    except Exception as e:
                        duration = time.time() - start_time
                        print(f" [ERROR] {e} ({duration:.2f}s)")
                        results["errors"].append({
                            "year": year, "gp": gp_name, "endpoint": endpoint, "error": str(e)
                        })
                        
    # ------------------ PRINT SUMMARY ------------------
    print("\n\n" + "="*50)
    print("TEST SUMMARY")
    print("="*50)
    print(f"✅ Passed: {len(results['passed'])}")
    print(f"❌ Failed: {len(results['failed'])}")
    print(f"⚠️ Errors: {len(results['errors'])}")
    
    if results['failed'] or results['errors']:
        print("\n--- FAILED ENDPOINTS ---")
        for fail in results['failed']:
            err_str = str(fail['error'])
            print(f"❌ [{fail['year']} {fail['gp']}] {fail['endpoint']} - Status {fail['status']}\n   Error: {err_str[:150]}")
        for err in results['errors']:
            print(f"⚠️ [{err['year']} {err['gp']}] {err['endpoint']}\n   Exception: {err['error']}")

        with open("failed_endpoints.txt", "w", encoding="utf-8") as f:
            f.write("FAILED ENDPOINTS LOG\n")
            f.write("="*50 + "\n\n")
            for fail in results['failed']:
                error_display = json.dumps(fail['error'], indent=2) if isinstance(fail['error'], (dict, list)) else fail['error']
                f.write(f"GP: {fail['year']} {fail['gp']}\nEndpoint: {fail['endpoint']}\nStatus: {fail['status']}\nError:\n{error_display}\n\n{'-'*50}\n\n")
            if results['errors']:
                f.write("\nEXCEPTIONS LOG\n")
                f.write("="*50 + "\n\n")
                for err in results['errors']:
                    f.write(f"GP: {err['year']} {err['gp']}\nEndpoint: {err['endpoint']}\nException: {err['error']}\n\n{'-'*50}\n\n")
        print("\nReadable failed endpoints log saved to failed_endpoints.txt")

    with open("endpoint_test_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4)
        
    print(f"Detailed JSON results saved to endpoint_test_results.json")

if __name__ == '__main__':
    main()
