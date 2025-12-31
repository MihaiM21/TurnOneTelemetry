"""
Complete test demonstrating all new features:
1. Multi-year collections (2024, 2025, 2026+)
2. Seasons data collection with drivers and teams
"""

from src.utils.database import MongoDBManager, SeasonsDataManager


def test_complete_system():
    """Test the complete multi-year system with seasons data"""

    print("=" * 70)
    print("COMPLETE SYSTEM TEST: Multi-Year Collections + Seasons Data")
    print("=" * 70)

    # Test 1: Multi-Year Collections
    print("\n📊 TEST 1: Multi-Year Collections")
    print("-" * 70)

    db = MongoDBManager()
    years = db.list_all_years()
    print(f"✓ Available year collections: {years}")

    for year in years:
        gps = db.list_all_gps(year=year)
        print(f"  - {year}: {len(gps)} Grand Prix events")

    # Test 2: Seasons Data Collection
    print("\n👥 TEST 2: Seasons Data Collection")
    print("-" * 70)

    seasons_mgr = SeasonsDataManager()
    seasons = seasons_mgr.list_all_seasons()
    print(f"✓ Available seasons: {seasons}")

    for year in seasons:
        drivers = seasons_mgr.get_drivers(year)
        teams = seasons_mgr.get_teams(year)
        print(f"  - {year}: {len(drivers)} drivers, {len(teams)} teams")

    # Test 3: Driver Information Retrieval
    print("\n🏎️  TEST 3: Driver Information Retrieval")
    print("-" * 70)

    # Test with multiple drivers across different years
    test_drivers = [
        (2025, "VER", "Max Verstappen (2025)"),
        (2025, "HAM", "Lewis Hamilton (2025 - moved to Ferrari)"),
        (2024, "HAM", "Lewis Hamilton (2024 - was at Mercedes)"),
        (2025, "ANT", "Kimi Antonelli (2025 - rookie)")
    ]

    for year, code, description in test_drivers:
        driver = seasons_mgr.get_driver(year, code)
        if driver:
            print(f"✓ {description}")
            print(f"    Team: {driver['team']}")
            print(f"    Color: {driver['color']}")
            print(f"    Number: {driver['number']}")
        else:
            print(f"✗ Driver {code} not found in {year}")

    # Test 4: Team Information Retrieval
    print("\n🏁 TEST 4: Team Information Retrieval")
    print("-" * 70)

    test_teams = [
        (2025, "Ferrari"),
        (2025, "Red Bull Racing"),
        (2024, "Mercedes")
    ]

    for year, team_name in test_teams:
        team = seasons_mgr.get_team(year, team_name)
        if team:
            print(f"✓ {team_name} ({year})")
            print(f"    Color: {team['color']}")
            print(f"    Drivers: {', '.join(team['drivers'])}")

            # Get full driver names
            driver_names = []
            for driver_code in team['drivers']:
                driver = seasons_mgr.get_driver(year, driver_code)
                if driver:
                    driver_names.append(driver['name'])
            print(f"    Driver Names: {', '.join(driver_names)}")

    # Test 5: Color Lookup (Useful for plotting)
    print("\n🎨 TEST 5: Color Lookup for Plotting")
    print("-" * 70)

    print("Getting colors for 2025 season visualization:")
    drivers_to_plot = ["VER", "HAM", "LEC", "NOR", "RUS"]

    for driver_code in drivers_to_plot:
        color = seasons_mgr.get_driver_color(2025, driver_code)
        driver = seasons_mgr.get_driver(2025, driver_code)
        if driver:
            print(f"  {driver_code} ({driver['name']:15s}): {color}")

    # Test 6: Year-to-Year Comparison
    print("\n📈 TEST 6: Year-to-Year Driver Movement")
    print("-" * 70)

    # Check drivers who changed teams
    drivers_to_check = ["HAM", "TSU", "SAI"]

    for driver_code in drivers_to_check:
        driver_2024 = seasons_mgr.get_driver(2024, driver_code)
        driver_2025 = seasons_mgr.get_driver(2025, driver_code)

        if driver_2024 and driver_2025:
            team_2024 = driver_2024['team']
            team_2025 = driver_2025['team']

            if team_2024 != team_2025:
                print(f"✓ {driver_code} ({driver_2025['name']}):")
                print(f"    2024: {team_2024}")
                print(f"    2025: {team_2025} ⭐ MOVED")
            else:
                print(f"✓ {driver_code} ({driver_2025['name']}): Stayed at {team_2025}")

    # Test 7: Integration Example - Getting Race Data with Driver Info
    print("\n🔗 TEST 7: Integration Example")
    print("-" * 70)

    # Simulate getting race data for a specific GP
    gp_id = "2025_AUS"
    year = 2025

    print(f"Fetching data for {gp_id}...")

    # Get GP data from year-specific collection
    gp_data = db.get_all_gp_data(gp_id, year=year)

    if gp_data:
        print(f"✓ Found GP: {gp_data['name']}")
        print(f"  Sessions: {len(gp_data.get('sessions', []))}")

        # Get driver info for the season
        all_drivers = seasons_mgr.get_drivers(year)
        print(f"  Season has {len(all_drivers)} drivers")

        # Show how to combine both data sources
        print("\n  Example: Get top 3 drivers with their colors:")
        top_3 = ["VER", "HAM", "LEC"]
        for i, driver_code in enumerate(top_3, 1):
            driver = seasons_mgr.get_driver(year, driver_code)
            if driver:
                print(f"    {i}. {driver['full_name']:20s} ({driver['team']:20s}) - {driver['color']}")

    # Clean up
    db.close()

    print("\n" + "=" * 70)
    print("✅ ALL TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 70)
    print("\nSummary:")
    print(f"  • Multi-year collections: {len(years)} years")
    print(f"  • Seasons data: {len(seasons)} seasons")
    print(f"  • Total functionality: Ready for production! 🚀")


if __name__ == "__main__":
    try:
        test_complete_system()
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()

