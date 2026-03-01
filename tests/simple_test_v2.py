"""Simple test without MongoDB - Works for ANY season!"""
import sys
from src.scripts.simple.v2_top_speed import TopSpeedPlot_V2

# Default test case
YEAR = 2025
EVENT = "Italian Grand Prix"
SESSION = "Race"

# Allow command line override
if len(sys.argv) > 1:
    YEAR = int(sys.argv[1])
if len(sys.argv) > 2:
    EVENT = sys.argv[2]
if len(sys.argv) > 3:
    SESSION = sys.argv[3]

print("Testing V2 Top Speed Plot (No MongoDB)")
print("="*80)
print(f"Season: {YEAR}")
print(f"Event: {EVENT}")
print(f"Session: {SESSION}")
print("="*80)

try:
    result = TopSpeedPlot_V2(YEAR, EVENT, SESSION, use_cache=False)
    print(f"\n✅ Success! Plot saved to: {result}")
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*80)
print("💡 Tip: Try different seasons!")
print("   python simple_test_v2.py 2024 'Monaco Grand Prix' Race")
print("   python simple_test_v2.py 2023 'British Grand Prix' Qualifying")
print("="*80)
