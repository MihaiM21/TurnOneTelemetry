"""
Quick test script for the F1 Static Client
Run this to verify the implementation works correctly
"""

from src.data_loader.f1_static_client import (
    F1StaticClient,
    demo_task_1_scraper,
    demo_task_2_parser,
    demo_task_3_decompressor,
    run_all_demos
)


def quick_test():
    """Quick test of basic functionality"""
    print("Testing F1 Static Client...\n")
    
    client = F1StaticClient()
    
    # Test 1: Fetch 2025 season index
    print("1. Fetching 2025 season index...")
    try:
        season_data = client.fetch_season_index(2025)
        print(f"   [OK] Found {len(season_data.get('Meetings', []))} races in 2025 season\n")
    except Exception as e:
        print(f"   [FAIL] Error: {e}\n")
        return
    
    # Test 2: Get Italian GP Race URL
    print("2. Finding 2025 Italian Grand Prix Race...")
    try:
        url = client.get_timing_data_url(2025, "Italian Grand Prix", "Race")
        if url:
            print(f"   [OK] Found: {url}\n")
        else:
            print("   [FAIL] Could not find session\n")
            return
    except Exception as e:
        print(f"   [FAIL] Error: {e}\n")
        return
    
    # Test 3: Parse first 3 timing entries
    print("3. Parsing first 3 TimingData entries...")
    try:
        entries = client.parse_jsonstream_simple(url, limit=3)
        print(f"   [OK] Parsed {len(entries)} entries")
        if entries:
            print(f"   First entry keys: {list(entries[0].keys())}\n")
    except Exception as e:
        print(f"   [FAIL] Error: {e}\n")
        return
    
    print("[SUCCESS] Basic functionality test passed!\n")
    print("Run the full demo with: python -c \"from src.data_loader.f1_static_client import run_all_demos; run_all_demos()\"")


if __name__ == "__main__":
    # Run quick test
    quick_test()
    
    # Uncomment below to run full demonstration
    print("\n" + "="*80 + "\n")
    run_all_demos()
