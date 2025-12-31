"""
Test script for MongoDB integration
Run this to verify your MongoDB setup is working correctly
"""

from src.utils.database import MongoDBManager, test_connection
from src.utils.database.bulk_store import store_custom_data, retrieve_data
import json


def test_basic_connection():
    """Test basic MongoDB connection"""
    print("\n" + "="*60)
    print("TEST 1: Basic Connection")
    print("="*60)

    if test_connection():
        print("✓ MongoDB connection successful!")
        return True
    else:
        print("✗ MongoDB connection failed!")
        return False


def test_create_and_retrieve():
    """Test creating and retrieving data"""
    print("\n" + "="*60)
    print("TEST 2: Create and Retrieve Data")
    print("="*60)

    try:
        # Test data
        test_data = [
            {"Team": "Ferrari", "Top Speed (km/h)": 327.0, "Color": "#E80020"},
            {"Team": "Mercedes", "Top Speed (km/h)": 324.0, "Color": "#27F4D2"},
            {"Team": "McLaren", "Top Speed (km/h)": 324.0, "Color": "#FF8000"}
        ]

        # Store test data
        print("\nStoring test data...")
        success = store_custom_data(
            year=2025,
            round_nr=1,
            gp_name="Test Grand Prix",
            session_type="FP1",
            data_type="test_top_speed",
            data=test_data
        )

        if not success:
            print("✗ Failed to store data")
            return False

        # Retrieve test data
        print("\nRetrieving test data...")
        retrieved = retrieve_data("2025_TES", "FP1", "test_top_speed")

        if retrieved:
            print(f"✓ Successfully retrieved {len(retrieved)} records")
            print(f"\nSample data:")
            print(json.dumps(retrieved[0], indent=2))
            return True
        else:
            print("✗ Failed to retrieve data")
            return False

    except Exception as e:
        print(f"✗ Test failed with error: {e}")
        return False


def test_update_data():
    """Test updating existing data"""
    print("\n" + "="*60)
    print("TEST 3: Update Existing Data")
    print("="*60)

    try:
        # Store initial data
        initial_data = [{"value": 100}]
        print("\nStoring initial data...")
        store_custom_data(
            year=2025,
            round_nr=1,
            gp_name="Test Grand Prix",
            session_type="FP1",
            data_type="test_update",
            data=initial_data
        )

        # Update with new data
        updated_data = [{"value": 200}]
        print("Updating data...")
        store_custom_data(
            year=2025,
            round_nr=1,
            gp_name="Test Grand Prix",
            session_type="FP1",
            data_type="test_update",
            data=updated_data
        )

        # Retrieve and verify
        retrieved = retrieve_data("2025_TES", "FP1", "test_update")

        if retrieved and retrieved[0]["value"] == 200:
            print("✓ Data successfully updated")
            return True
        else:
            print("✗ Update verification failed")
            return False

    except Exception as e:
        print(f"✗ Test failed with error: {e}")
        return False


def test_multiple_sessions():
    """Test storing multiple sessions for same GP"""
    print("\n" + "="*60)
    print("TEST 4: Multiple Sessions")
    print("="*60)

    try:
        db = MongoDBManager()

        # Create GP with multiple sessions
        gp_id = "2025_TES"
        sessions = ["FP1", "FP2", "FP3", "Q"]

        print("\nStoring multiple sessions...")
        for session in sessions:
            db.add_session_data(
                gp_id=gp_id,
                session_type=session,
                data_type="test_data",
                data=[{"session": session}]
            )

        # Retrieve GP data
        gp_data = db.get_all_gp_data(gp_id)

        if gp_data and len(gp_data.get('sessions', [])) >= len(sessions):
            print(f"✓ Successfully stored {len(gp_data['sessions'])} sessions")
            return True
        else:
            print("✗ Failed to store all sessions")
            return False

    except Exception as e:
        print(f"✗ Test failed with error: {e}")
        return False
    finally:
        db.close()


def run_all_tests():
    """Run all tests"""
    print("\n" + "#"*60)
    print("# MongoDB Integration Test Suite")
    print("#"*60)

    tests = [
        ("Connection Test", test_basic_connection),
        ("Create & Retrieve Test", test_create_and_retrieve),
        ("Update Test", test_update_data),
        ("Multiple Sessions Test", test_multiple_sessions)
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n✗ {test_name} crashed: {e}")
            results.append((test_name, False))

    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✓ PASSED" if result else "✗ FAILED"
        print(f"{test_name}: {status}")

    print(f"\nTotal: {passed}/{total} tests passed")
    print("="*60)

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()

    if success:
        print("\n✓ All tests passed! MongoDB integration is working correctly.")
        print("\nYou can now:")
        print("  1. Run bulk_store_plots() to store existing data")
        print("  2. Use TopSpeedData() and ThrottleCompData() - they auto-store")
        print("  3. Add store_plot_data_to_mongo() to any new plot functions")
    else:
        print("\n✗ Some tests failed. Please check the MongoDB connection and credentials.")

