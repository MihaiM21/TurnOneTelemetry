"""
Comparison Script: FastF1 (v1) vs Custom Client (v2)
=====================================================

This script demonstrates the differences between the FastF1-based
implementation and our custom F1StaticClient implementation.

Run this to see both versions in action.
"""

from src.scripts.simple.top_speed import TopSpeedPlot, TopSpeedData
from src.scripts.simple.v2_top_speed import TopSpeedPlot_V2, TopSpeedData_V2
import time


def compare_implementations():
    """
    Compare both implementations side by side
    """
    print("\n" + "="*80)
    print("TOP SPEED COMPARISON: FastF1 (v1) vs Custom Client (v2)")
    print("="*80)
    
    # Test parameters
    year = 2023
    event = "Italian Grand Prix"
    session = "Race"
    
    print(f"\nTest Session: {year} {event} {session}")
    print("-"*80)
    
    # ========================================================================
    # Version 1: FastF1 Implementation
    # ========================================================================
    print("\n[V1 - FastF1 Implementation]")
    print("This uses the existing FastF1 library")
    print("-"*80)
    
    try:
        start_time = time.time()
        result_v1 = TopSpeedPlot(year, 1, session)  # Note: FastF1 uses round number
        elapsed_v1 = time.time() - start_time
        
        print(f"✓ Success!")
        print(f"  Time: {elapsed_v1:.2f} seconds")
        print(f"  Output: {result_v1}")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        elapsed_v1 = None
    
    # ========================================================================
    # Version 2: Custom F1 Static Client
    # ========================================================================
    print("\n\n[V2 - Custom F1 Static Client]")
    print("This uses our custom implementation (NO FastF1 dependency)")
    print("-"*80)
    
    try:
        start_time = time.time()
        result_v2 = TopSpeedPlot_V2(year, event, session, use_cache=False)
        elapsed_v2 = time.time() - start_time
        
        print(f"✓ Success!")
        print(f"  Time: {elapsed_v2:.2f} seconds")
        print(f"  Output: {result_v2}")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        elapsed_v2 = None
    
    # ========================================================================
    # Summary
    # ========================================================================
    print("\n\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    
    print("\nKey Differences:")
    print("  V1 (FastF1):")
    print("    - Uses FastF1 library")
    print("    - Session loading is automatic")
    print("    - Lap data is structured in DataFrames")
    print("    - Well-tested, stable API")
    
    print("\n  V2 (Custom Client):")
    print("    - Uses our F1StaticClient")
    print("    - Direct access to F1 timing API")
    print("    - Parses raw .z.jsonStream files")
    print("    - No external library dependency")
    print("    - Full control over data processing")
    
    if elapsed_v1 and elapsed_v2:
        print(f"\nPerformance:")
        print(f"  V1 (FastF1):      {elapsed_v1:.2f}s")
        print(f"  V2 (Custom):      {elapsed_v2:.2f}s")
        
        if elapsed_v2 < elapsed_v1:
            diff = ((elapsed_v1 - elapsed_v2) / elapsed_v1) * 100
            print(f"  V2 is {diff:.1f}% faster!")
        else:
            diff = ((elapsed_v2 - elapsed_v1) / elapsed_v2) * 100
            print(f"  V1 is {diff:.1f}% faster")
    
    print("\n" + "="*80)


def test_multiple_sessions():
    """
    Test V2 implementation with multiple sessions
    """
    print("\n\n" + "="*80)
    print("TESTING V2 WITH MULTIPLE SESSIONS")
    print("="*80)
    
    test_cases = [
        (2023, "Monaco Grand Prix", "Qualifying"),
        (2023, "Belgian Grand Prix", "Race"),
        (2023, "British Grand Prix", "Race"),
    ]
    
    results = []
    
    for year, event, session in test_cases:
        print(f"\n[TEST] {year} {event} - {session}")
        print("-"*80)
        
        try:
            start_time = time.time()
            result = TopSpeedPlot_V2(year, event, session, use_cache=False)
            elapsed = time.time() - start_time
            
            print(f"✓ Success in {elapsed:.2f}s")
            print(f"  Output: {result}")
            results.append((event, session, "Success", elapsed))
            
        except Exception as e:
            print(f"✗ Error: {e}")
            results.append((event, session, "Failed", 0))
    
    # Summary
    print("\n\n" + "="*80)
    print("RESULTS SUMMARY")
    print("="*80)
    
    for event, session, status, time_taken in results:
        status_icon = "✓" if status == "Success" else "✗"
        time_str = f"{time_taken:.2f}s" if time_taken > 0 else "N/A"
        print(f"{status_icon} {event:30s} {session:12s} {time_str:>8s}")
    
    success_count = sum(1 for r in results if r[2] == "Success")
    print(f"\nTotal: {success_count}/{len(results)} successful")


def quick_test_v2():
    """
    Quick test of V2 implementation only
    """
    print("\n" + "="*80)
    print("QUICK TEST: V2 Top Speed Plot")
    print("="*80)
    
    year = 2023
    event = "Italian Grand Prix"
    session = "Race"
    
    print(f"\nGenerating: {year} {event} {session}")
    print("-"*80)
    
    try:
        result = TopSpeedPlot_V2(year, event, session)
        print(f"\n✓ Success!")
        print(f"  Plot saved to: {result}")
        
        # Also generate data-only version
        print("\nGenerating data file (JSON)...")
        data_result = TopSpeedData_V2(year, event, session)
        print(f"✓ Data saved to: {data_result}")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "compare":
            compare_implementations()
        elif sys.argv[1] == "multiple":
            test_multiple_sessions()
        elif sys.argv[1] == "quick":
            quick_test_v2()
        else:
            print("Usage:")
            print("  python compare_top_speed.py compare   - Compare v1 vs v2")
            print("  python compare_top_speed.py multiple  - Test v2 with multiple sessions")
            print("  python compare_top_speed.py quick     - Quick test of v2 only")
    else:
        # Default: run quick test
        quick_test_v2()
