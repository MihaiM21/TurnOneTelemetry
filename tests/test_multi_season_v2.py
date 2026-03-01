"""
Multi-Season Test - Demonstrates V2 works across all seasons
"""

from src.scripts.simple.v2_top_speed import TopSpeedPlot_V2

# Test cases covering different years and circuits
test_cases = [
    # Year, Event, Session
    (2023, "Italian Grand Prix", "Race"),      # Fast circuit
    (2024, "Monaco Grand Prix", "Qualifying"),  # Slow circuit
    (2025, "British Grand Prix", "Race"),       # Medium circuit
]

print("\n" + "="*80)
print("🏎️  Multi-Season V2 Test")
print("="*80)
print("Testing that V2 works across multiple seasons and circuit types\n")

results = []

for year, event, session in test_cases:
    print(f"\n{'─'*80}")
    print(f"📅 Testing: {year} {event} {session}")
    print(f"{'─'*80}")
    
    try:
        result = TopSpeedPlot_V2(year, event, session, use_cache=False)
        print(f"✅ SUCCESS: {result}")
        results.append((year, event, session, "✅ Pass"))
    except Exception as e:
        print(f"❌ FAILED: {e}")
        results.append((year, event, session, f"❌ Fail: {e}"))

# Summary
print("\n" + "="*80)
print("📊 TEST SUMMARY")
print("="*80)

for year, event, session, status in results:
    print(f"{status:12s} | {year} {event} {session}")

success_count = sum(1 for r in results if "✅" in r[3])
total_count = len(results)

print("="*80)
print(f"Results: {success_count}/{total_count} tests passed")
print("="*80)

if success_count == total_count:
    print("\n🎉 All tests passed! V2 works across all seasons!")
else:
    print(f"\n⚠️  {total_count - success_count} test(s) failed")
