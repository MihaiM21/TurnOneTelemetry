"""
Example usage of MongoDB storage for F1 telemetry data

This script demonstrates common usage patterns for the MongoDB integration.
"""

# Example 1: Using existing plot functions (now with auto-storage)
def example_1_automatic_storage():
    """Existing plot functions now automatically store to MongoDB"""
    print("\n" + "="*60)
    print("EXAMPLE 1: Automatic Storage")
    print("="*60)

    from src.scripts.simple.top_speed import TopSpeedData
    from src.scripts.simple.throttle_comparison import ThrottleCompData

    # These now automatically store to MongoDB!
    print("\nGenerating top speed data (auto-stores to MongoDB)...")
    TopSpeedData(2025, 1, 'FP1')

    print("\nGenerating throttle comparison data (auto-stores to MongoDB)...")
    ThrottleCompData(2025, 1, 'FP1')

    print("\n✓ Data automatically stored to MongoDB!")


# Example 2: Bulk store all existing JSON files
def example_2_bulk_storage():
    """Store all existing JSON files from outputs/plots"""
    print("\n" + "="*60)
    print("EXAMPLE 2: Bulk Storage")
    print("="*60)

    from src.utils.database.bulk_store import bulk_store_plots

    # Store all data from all years
    print("\nStoring all existing plot data to MongoDB...")
    bulk_store_plots()

    # Or store only specific year
    # bulk_store_plots(year=2025)


# Example 3: Add custom data type to a new plot function
def example_3_new_plot_type():
    """Template for adding MongoDB storage to a new plot function"""
    print("\n" + "="*60)
    print("EXAMPLE 3: New Plot Type Template")
    print("="*60)

    print("""
    # In your new plot file (e.g., my_new_plot.py):
    
    from src.utils.database.mongo_helper import store_plot_data_to_mongo
    import pandas as pd
    
    def MyNewPlotData(y, r, e, store_to_mongo=True):
        # Load session
        sessionloader = data_aqcuisition.SessionLoader(y, r, e)
        session = sessionloader.get_session()
        
        # ... your analysis code ...
        
        # Create your data
        data = {
            'metric1': values1,
            'metric2': values2
        }
        df = pd.DataFrame(data)
        
        # Save JSON file
        json_path = location + "/" + name_json
        df.to_json(json_path, orient='records')
        
        # Store to MongoDB (JUST ADD THESE LINES!)
        if store_to_mongo:
            try:
                store_plot_data_to_mongo(session, 'my_new_plot_type', json_path)
            except Exception as e:
                print(f"Warning: Failed to store to MongoDB: {e}")
        
        return json_path
    """)


# Example 4: Store custom data without a plot function
def example_4_direct_storage():
    """Store data directly without generating plots"""
    print("\n" + "="*60)
    print("EXAMPLE 4: Direct Custom Data Storage")
    print("="*60)

    from src.utils.database.bulk_store import store_custom_data

    # Custom analysis data
    custom_data = [
        {"driver": "VER", "consistency_score": 95.5, "avg_pace": "1:18.234"},
        {"driver": "HAM", "consistency_score": 93.2, "avg_pace": "1:18.456"},
        {"driver": "LEC", "consistency_score": 92.8, "avg_pace": "1:18.567"}
    ]

    print("\nStoring custom analysis data...")
    store_custom_data(
        year=2025,
        round_nr=1,
        gp_name="Australian Grand Prix",
        session_type="FP1",
        data_type="driver_consistency_analysis",
        data=custom_data
    )

    print("✓ Custom data stored!")


# Example 5: Retrieve data from MongoDB
def example_5_retrieve_data():
    """Retrieve stored data from MongoDB"""
    print("\n" + "="*60)
    print("EXAMPLE 5: Retrieve Data")
    print("="*60)

    from src.utils.database.bulk_store import retrieve_data, list_all_data
    import json

    # Get all data for a session
    print("\nRetrieving all FP1 data for Australian GP...")
    session_data = retrieve_data("2025_AUS", "FP1")

    if session_data:
        print(f"✓ Found session with {len(session_data.get('data', []))} data types")
        for data_entry in session_data.get('data', []):
            print(f"  - {data_entry['data_type']}")

    # Get specific data type
    print("\nRetrieving only top speed data...")
    top_speed = retrieve_data("2025_AUS", "FP1", "top_speed")

    if top_speed:
        print(f"✓ Retrieved top speed data with {len(top_speed)} entries")
        print(f"\nSample: {json.dumps(top_speed[0], indent=2)}")

    # List all stored GPs
    print("\nListing all stored Grand Prix events...")
    all_gps = list_all_data(year=2025)

    for gp in all_gps:
        print(f"  - {gp['name']} (Round {gp['round_nr']})")


# Example 6: Advanced - Direct database access
def example_6_advanced_usage():
    """Advanced database operations"""
    print("\n" + "="*60)
    print("EXAMPLE 6: Advanced Usage")
    print("="*60)

    from src.utils.database import MongoDBManager

    db = MongoDBManager()

    try:
        # Get complete GP document
        print("\nRetrieving complete Australian GP data...")
        gp_data = db.get_all_gp_data("2025_AUS")

        if gp_data:
            print(f"✓ GP: {gp_data['name']}")
            print(f"  Year: {gp_data['year']}")
            print(f"  Round: {gp_data['round_nr']}")
            print(f"  Sessions: {len(gp_data.get('sessions', []))}")

            # Count total data entries
            total_data = sum(
                len(session.get('data', []))
                for session in gp_data.get('sessions', [])
            )
            print(f"  Total data types: {total_data}")

        # List all GPs across all years
        print("\nListing all GPs in database...")
        all_gps = db.list_all_gps()
        print(f"✓ Total GPs stored: {len(all_gps)}")

    finally:
        db.close()


def print_menu():
    """Print example menu"""
    print("\n" + "#"*60)
    print("# MongoDB Storage Examples")
    print("#"*60)
    print("\nAvailable examples:")
    print("  1. Automatic storage (existing functions)")
    print("  2. Bulk store existing JSON files")
    print("  3. Template for new plot types")
    print("  4. Direct custom data storage")
    print("  5. Retrieve data from MongoDB")
    print("  6. Advanced database operations")
    print("  0. Exit")


if __name__ == "__main__":
    import sys

    examples = {
        '1': example_1_automatic_storage,
        '2': example_2_bulk_storage,
        '3': example_3_new_plot_type,
        '4': example_4_direct_storage,
        '5': example_5_retrieve_data,
        '6': example_6_advanced_usage
    }

    if len(sys.argv) > 1:
        # Run specific example from command line
        choice = sys.argv[1]
        if choice in examples:
            examples[choice]()
        else:
            print(f"Unknown example: {choice}")
    else:
        # Interactive mode
        while True:
            print_menu()
            choice = input("\nEnter example number (or 0 to exit): ").strip()

            if choice == '0':
                print("\nGoodbye!")
                break
            elif choice in examples:
                try:
                    examples[choice]()
                    input("\nPress Enter to continue...")
                except Exception as e:
                    print(f"\n✗ Error: {e}")
                    input("\nPress Enter to continue...")
            else:
                print("\n✗ Invalid choice. Please try again.")

