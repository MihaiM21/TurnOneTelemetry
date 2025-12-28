"""
Test script for multi-year MongoDB collections
Demonstrates how to work with different year collections
"""

from src.utils.database import MongoDBManager


def test_multi_year_collections():
    """Test multi-year collection functionality"""

    print("=" * 60)
    print("Testing Multi-Year MongoDB Collections")
    print("=" * 60)

    # Test 1: Initialize with different years
    print("\n1. Testing initialization with different years...")

    db_2025 = MongoDBManager(year=2025)
    print(f"   ✓ Created manager for 2025: {db_2025.collection.name}")

    db_2024 = MongoDBManager(year=2024)
    print(f"   ✓ Created manager for 2024: {db_2024.collection.name}")

    db_2026 = MongoDBManager(year=2026)
    print(f"   ✓ Created manager for 2026: {db_2026.collection.name}")

    # Test 2: List all available years
    print("\n2. Listing all available years in database...")
    years = db_2025.list_all_years()
    print(f"   Available years: {years}")

    # Test 3: Switch between years
    print("\n3. Testing year switching...")
    db = MongoDBManager(year=2025)
    print(f"   Current year: {db.get_current_year()}")

    db.set_year(2024)
    print(f"   Switched to: {db.get_current_year()}")

    db.set_year(2026)
    print(f"   Switched to: {db.get_current_year()}")

    # Test 4: Create GP documents in different years
    print("\n4. Testing GP document creation across years...")

    # 2024 GP
    gp_2024 = db_2024.get_or_create_gp(
        year=2024,
        round_nr=1,
        gp_name="BahrainGP",
        gp_id="2024_BHR"
    )
    print(f"   ✓ Created/Retrieved 2024 GP: {gp_2024['gp_id']}")

    # 2025 GP
    gp_2025 = db_2025.get_or_create_gp(
        year=2025,
        round_nr=1,
        gp_name="AustralianGP",
        gp_id="2025_AUS"
    )
    print(f"   ✓ Created/Retrieved 2025 GP: {gp_2025['gp_id']}")

    # 2026 GP
    gp_2026 = db_2026.get_or_create_gp(
        year=2026,
        round_nr=1,
        gp_name="BahrainGP",
        gp_id="2026_BHR"
    )
    print(f"   ✓ Created/Retrieved 2026 GP: {gp_2026['gp_id']}")

    # Test 5: Add session data to different years
    print("\n5. Testing session data addition across years...")

    sample_data = [
        {"driver": "VER", "speed": 335.2},
        {"driver": "HAM", "speed": 332.1}
    ]

    # Add data to 2024
    success_2024 = db_2024.add_session_data(
        gp_id="2024_BHR",
        session_type="Q",
        data_type="top_speed",
        data=sample_data
    )
    print(f"   ✓ Added data to 2024: {success_2024}")

    # Add data to 2025
    success_2025 = db_2025.add_session_data(
        gp_id="2025_AUS",
        session_type="Q",
        data_type="top_speed",
        data=sample_data
    )
    print(f"   ✓ Added data to 2025: {success_2025}")

    # Add data to 2026
    success_2026 = db_2026.add_session_data(
        gp_id="2026_BHR",
        session_type="Q",
        data_type="top_speed",
        data=sample_data
    )
    print(f"   ✓ Added data to 2026: {success_2026}")

    # Test 6: Retrieve data from different years
    print("\n6. Testing data retrieval across years...")

    data_2024 = db_2024.get_session_data("2024_BHR", "Q", "top_speed")
    print(f"   ✓ Retrieved 2024 data: {len(data_2024) if data_2024 else 0} records")

    data_2025 = db_2025.get_session_data("2025_AUS", "Q", "top_speed")
    print(f"   ✓ Retrieved 2025 data: {len(data_2025) if data_2025 else 0} records")

    data_2026 = db_2026.get_session_data("2026_BHR", "Q", "top_speed")
    print(f"   ✓ Retrieved 2026 data: {len(data_2026) if data_2026 else 0} records")

    # Test 7: List GPs for each year
    print("\n7. Listing all GPs for each year...")

    for year in [2024, 2025, 2026]:
        gps = db.list_all_gps(year=year)
        print(f"   Year {year}: {len(gps)} Grand Prix events")
        for gp in gps:
            print(f"      - Round {gp['round_nr']}: {gp['name']} ({gp['gp_id']})")

    # Test 8: Test automatic year extraction from gp_id
    print("\n8. Testing automatic year extraction from GP ID...")

    # Create a single manager and test automatic routing
    db_auto = MongoDBManager()

    # Should automatically use 2024 collection
    data_auto = db_auto.get_session_data("2024_BHR", "Q", "top_speed")
    print(f"   ✓ Auto-retrieved from 2024: {len(data_auto) if data_auto else 0} records")

    # Should automatically use 2025 collection
    data_auto = db_auto.get_session_data("2025_AUS", "Q", "top_speed")
    print(f"   ✓ Auto-retrieved from 2025: {len(data_auto) if data_auto else 0} records")

    # Close all connections
    print("\n9. Closing all database connections...")
    db_2024.close()
    db_2025.close()
    db_2026.close()
    db.close()
    db_auto.close()
    print("   ✓ All connections closed")

    print("\n" + "=" * 60)
    print("Multi-Year Collection Tests Completed Successfully!")
    print("=" * 60)


if __name__ == "__main__":
    try:
        test_multi_year_collections()
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()

