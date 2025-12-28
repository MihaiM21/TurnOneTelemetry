"""
Plot Data Generator and MongoDB Storage
This module generates telemetry data for all available plot types and stores them in MongoDB.
Supports batch generation for multiple sessions and drivers.
"""

import sys
import os
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import traceback
import fastf1

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import plot data functions
from src.scripts.simple.top_speed import TopSpeedData
from src.scripts.simple.throttle_comparison import ThrottleCompData
from src.scripts.simple.laptimes_distribution import LatimesDistribution
from src.scripts.quali_practice.qulifying_results import QualiResultsData
from src.scripts.quali_practice.track_comparison_2drivers import TrackComparisonData
from src.scripts.quali_practice.throttleBrake_comparison_2drivers import throttle_graph_data

# Import database utilities
from src.utils.database.mongo import MongoDBManager
from src.utils.database.mongo_helper import store_plot_data_to_mongo, get_gp_id_from_session
from src.data_loader import data_aqcuisition


class PlotDataGenerator:
    """
    Generate telemetry data for all plot types and store in MongoDB
    """

    def __init__(self, year: int = None, use_mongo: bool = True):
        """
        Initialize the PlotDataGenerator

        Args:
            year: The year for the F1 season (defaults to current year)
            use_mongo: Whether to store data in MongoDB
        """
        self.year = year or datetime.now().year
        self.use_mongo = use_mongo
        self.db_manager = MongoDBManager(year=self.year) if use_mongo else None
        self.results = {
            'success': [],
            'failed': []
        }

    def generate_top_speed(self, year: int, round_num: int, session_name: str) -> bool:
        """
        Generate top speed data for a session

        Args:
            year: Race year
            round_num: Round number
            session_name: Session name (e.g., 'Qualifying', 'Race')

        Returns:
            True if successful, False otherwise
        """
        try:
            print(f"  → Generating top speed data...")
            json_path = TopSpeedData(year, round_num, session_name, store_to_mongo=self.use_mongo)
            if json_path:
                self.results['success'].append(f"Top Speed - Y{year} R{round_num} {session_name}")
                return True
            return False
        except Exception as e:
            print(f"    ✗ Error: {e}")
            self.results['failed'].append(f"Top Speed - Y{year} R{round_num} {session_name}: {str(e)}")
            return False

    def generate_throttle_comparison(self, year: int, round_num: int, session_name: str) -> bool:
        """
        Generate throttle comparison data for a session

        Args:
            year: Race year
            round_num: Round number
            session_name: Session name

        Returns:
            True if successful, False otherwise
        """
        try:
            print(f"  → Generating throttle comparison data...")
            json_path = ThrottleCompData(year, round_num, session_name, store_to_mongo=self.use_mongo)
            if json_path:
                self.results['success'].append(f"Throttle Comparison - Y{year} R{round_num} {session_name}")
                return True
            return False
        except Exception as e:
            print(f"    ✗ Error: {e}")
            self.results['failed'].append(f"Throttle Comparison - Y{year} R{round_num} {session_name}: {str(e)}")
            return False

    def generate_qualifying_results(self, year: int, round_num: int, session_name: str) -> bool:
        """
        Generate qualifying results data

        Args:
            year: Race year
            round_num: Round number
            session_name: Session name (should be 'Qualifying' or similar)

        Returns:
            True if successful, False otherwise
        """
        try:
            print(f"  → Generating qualifying results data...")
            json_path = QualiResultsData(year, round_num, session_name, store_to_mongo=self.use_mongo)
            if json_path:
                self.results['success'].append(f"Qualifying Results - Y{year} R{round_num} {session_name}")
                return True
            return False
        except Exception as e:
            print(f"    ✗ Error: {e}")
            self.results['failed'].append(f"Qualifying Results - Y{year} R{round_num} {session_name}: {str(e)}")
            return False

    def generate_lap_times_distribution(self, year: int, round_num: int, session_name: str, driver: str) -> bool:
        """
        Generate lap times distribution data for a specific driver

        Args:
            year: Race year
            round_num: Round number
            session_name: Session name
            driver: Driver code (e.g., 'VER', 'HAM')

        Returns:
            True if successful, False otherwise
        """
        try:
            print(f"  → Generating lap times distribution for {driver}...")
            json_path = LatimesDistribution(year, round_num, session_name, driver)
            if json_path:
                self.results['success'].append(f"Lap Times Distribution - {driver} - Y{year} R{round_num} {session_name}")
                return True
            return False
        except Exception as e:
            print(f"    ✗ Error: {e}")
            self.results['failed'].append(f"Lap Times Distribution - {driver} - Y{year} R{round_num} {session_name}: {str(e)}")
            return False

    def generate_track_comparison(self, year: int, round_num: int, session_name: str, driver1: str, driver2: str) -> bool:
        """
        Generate track comparison data for two drivers

        Args:
            year: Race year
            round_num: Round number
            session_name: Session name
            driver1: First driver code
            driver2: Second driver code

        Returns:
            True if successful, False otherwise
        """
        try:
            print(f"  → Generating track comparison for {driver1} vs {driver2}...")
            json_path = TrackComparisonData(year, round_num, session_name, driver1, driver2)
            if json_path:
                self.results['success'].append(f"Track Comparison - {driver1} vs {driver2} - Y{year} R{round_num} {session_name}")
                return True
            return False
        except Exception as e:
            print(f"    ✗ Error: {e}")
            self.results['failed'].append(f"Track Comparison - {driver1} vs {driver2} - Y{year} R{round_num} {session_name}: {str(e)}")
            return False

    def generate_throttle_brake_comparison(self, year: int, round_num: int, session_name: str, driver1: str, driver2: str) -> bool:
        """
        Generate throttle and brake comparison data for two drivers

        Args:
            year: Race year
            round_num: Round number
            session_name: Session name
            driver1: First driver code
            driver2: Second driver code

        Returns:
            True if successful, False otherwise
        """
        try:
            print(f"  → Generating throttle/brake comparison for {driver1} vs {driver2}...")
            json_path = throttle_graph_data(year, round_num, session_name, driver1, driver2)
            if json_path:
                self.results['success'].append(f"Throttle/Brake Comparison - {driver1} vs {driver2} - Y{year} R{round_num} {session_name}")
                return True
            return False
        except Exception as e:
            print(f"    ✗ Error: {e}")
            self.results['failed'].append(f"Throttle/Brake Comparison - {driver1} vs {driver2} - Y{year} R{round_num} {session_name}: {str(e)}")
            return False

    def generate_all_session_data(self, year: int, round_num: int, session_name: str, 
                                  include_driver_comparisons: bool = False,
                                  driver_pairs: Optional[List[Tuple[str, str]]] = None,
                                  drivers_for_laptimes: Optional[List[str]] = None) -> Dict[str, int]:
        """
        Generate all available data types for a session

        Args:
            year: Race year
            round_num: Round number
            session_name: Session name
            include_driver_comparisons: Whether to include driver-specific comparisons
            driver_pairs: List of driver pairs for comparisons (e.g., [('VER', 'HAM'), ('LEC', 'SAI')])
            drivers_for_laptimes: List of drivers for lap time distributions

        Returns:
            Dictionary with counts of successful and failed generations
        """
        print(f"\n{'='*60}")
        print(f"Generating data for Y{year} R{round_num} - {session_name}")
        print(f"{'='*60}")

        success_count = 0
        failed_count = 0

        # Generate session-level data (no driver needed)
        session_generators = [
            self.generate_top_speed,
            self.generate_throttle_comparison,
        ]

        # Add qualifying results for qualifying sessions
        if 'qualif' in session_name.lower() or session_name.lower() in ['q', 'sq']:
            session_generators.append(self.generate_qualifying_results)

        for generator in session_generators:
            try:
                if generator(year, round_num, session_name):
                    success_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                print(f"    ✗ Unexpected error: {e}")
                failed_count += 1

        # Generate driver-specific data if requested
        if include_driver_comparisons:
            # Driver pair comparisons
            if driver_pairs:
                for d1, d2 in driver_pairs:
                    try:
                        if self.generate_track_comparison(year, round_num, session_name, d1, d2):
                            success_count += 1
                        else:
                            failed_count += 1
                    except Exception as e:
                        print(f"    ✗ Unexpected error: {e}")
                        failed_count += 1

                    try:
                        if self.generate_throttle_brake_comparison(year, round_num, session_name, d1, d2):
                            success_count += 1
                        else:
                            failed_count += 1
                    except Exception as e:
                        print(f"    ✗ Unexpected error: {e}")
                        failed_count += 1

            # Lap time distributions
            if drivers_for_laptimes:
                for driver in drivers_for_laptimes:
                    try:
                        if self.generate_lap_times_distribution(year, round_num, session_name, driver):
                            success_count += 1
                        else:
                            failed_count += 1
                    except Exception as e:
                        print(f"    ✗ Unexpected error: {e}")
                        failed_count += 1

        print(f"\n  Summary: {success_count} succeeded, {failed_count} failed")
        return {'success': success_count, 'failed': failed_count}

    def get_session_drivers(self, year: int, round_num: int, session_name: str) -> List[str]:
        """
        Get list of drivers from a session

        Args:
            year: Race year
            round_num: Round number
            session_name: Session name

        Returns:
            List of driver codes
        """
        try:
            sessionloader = data_aqcuisition.SessionLoader(year, round_num, session_name)
            session = sessionloader.get_session()
            import pandas as pd
            drivers = pd.unique(session.laps['Driver'])
            return list(drivers)
        except Exception as e:
            print(f"Error getting drivers: {e}")
            return []

    def generate_default_driver_comparisons(self, year: int, round_num: int, session_name: str, 
                                           top_n_drivers: int = 5) -> Dict[str, int]:
        """
        Automatically generate driver comparisons for top N drivers in a session

        Args:
            year: Race year
            round_num: Round number
            session_name: Session name
            top_n_drivers: Number of top drivers to generate comparisons for

        Returns:
            Dictionary with counts of successful and failed generations
        """
        try:
            sessionloader = data_aqcuisition.SessionLoader(year, round_num, session_name)
            session = sessionloader.get_session()
            
            # Get top N fastest drivers
            import pandas as pd
            drivers = pd.unique(session.laps['Driver'])
            
            # Get fastest lap per driver
            fastest_laps = []
            for drv in drivers:
                try:
                    fastest_lap = session.laps.pick_driver(drv).pick_fastest()
                    if len(fastest_lap) > 0:
                        fastest_laps.append({'driver': drv, 'time': fastest_lap['LapTime']})
                except:
                    pass
            
            # Sort by lap time
            fastest_laps_sorted = sorted(fastest_laps, key=lambda x: x['time'])
            top_drivers = [lap['driver'] for lap in fastest_laps_sorted[:top_n_drivers]]
            
            # Generate pairs (consecutive drivers)
            driver_pairs = [(top_drivers[i], top_drivers[i+1]) for i in range(len(top_drivers)-1)]
            
            return self.generate_all_session_data(
                year, round_num, session_name,
                include_driver_comparisons=True,
                driver_pairs=driver_pairs,
                drivers_for_laptimes=top_drivers
            )
            
        except Exception as e:
            print(f"Error generating default comparisons: {e}")
            traceback.print_exc()
            return {'success': 0, 'failed': 0}

    def print_summary(self):
        """Print generation summary"""
        print(f"\n{'='*60}")
        print("GENERATION SUMMARY")
        print(f"{'='*60}")
        print(f"✓ Successful: {len(self.results['success'])}")
        print(f"✗ Failed: {len(self.results['failed'])}")
        
        if self.results['failed']:
            print(f"\nFailed operations:")
            for failure in self.results['failed']:
                print(f"  - {failure}")

    def generate_year_data(self, year: int, include_comparisons: bool = False, 
                          session_types: Optional[List[str]] = None,
                          skip_practice: bool = True) -> Dict[str, int]:
        """
        Generate data for all sessions in a given year

        Args:
            year: Race year
            include_comparisons: Whether to include driver comparisons (auto-detect top drivers)
            session_types: List of session types to include (e.g., ['Qualifying', 'Race']). 
                          If None, includes all sessions.
            skip_practice: Whether to skip practice sessions (default True)

        Returns:
            Dictionary with total counts of successful and failed generations
        """
        print(f"\n{'='*60}")
        print(f"GENERATING DATA FOR ENTIRE {year} SEASON")
        print(f"{'='*60}")

        total_success = 0
        total_failed = 0

        try:
            # Get the schedule for the year
            schedule = fastf1.get_event_schedule(year)
            
            print(f"\nFound {len(schedule)} events in {year} season")
            
            for idx, event in schedule.iterrows():
                event_name = event['EventName']
                round_num = event['RoundNumber']
                
                print(f"\n{'-'*60}")
                print(f"Event {round_num}: {event_name}")
                print(f"{'-'*60}")
                
                # Get available sessions for this event
                # Typical sessions: FP1, FP2, FP3, Qualifying, Sprint, Race
                available_sessions = []
                
                # Check which sessions are available
                session_map = {
                    'Practice 1': 'FP1',
                    'Practice 2': 'FP2',
                    'Practice 3': 'FP3',
                    'Qualifying': 'Qualifying',
                    'Sprint Qualifying': 'Sprint Qualifying',
                    'Sprint': 'Sprint',
                    'Race': 'Race'
                }
                
                for session_name in session_map.keys():
                    try:
                        # Try to load session to see if it exists
                        test_session = fastf1.get_session(year, round_num, session_name)
                        if test_session is not None:
                            available_sessions.append(session_name)
                    except:
                        pass
                
                # Filter sessions based on parameters
                sessions_to_process = []
                for session in available_sessions:
                    # Skip practice if requested
                    if skip_practice and 'Practice' in session:
                        print(f"  ⊘ Skipping {session}")
                        continue
                    
                    # Filter by session_types if provided
                    if session_types is not None:
                        if session not in session_types and session_map.get(session) not in session_types:
                            print(f"  ⊘ Skipping {session} (not in filter)")
                            continue
                    
                    sessions_to_process.append(session)
                
                # Process each session
                for session_name in sessions_to_process:
                    try:
                        print(f"\n  📊 Processing: {session_name}")
                        
                        if include_comparisons:
                            result = self.generate_default_driver_comparisons(year, round_num, session_name)
                        else:
                            result = self.generate_all_session_data(year, round_num, session_name)
                        
                        total_success += result.get('success', 0)
                        total_failed += result.get('failed', 0)
                        
                    except Exception as e:
                        print(f"    ✗ Error processing {session_name}: {e}")
                        total_failed += 1
                        traceback.print_exc()
                
                if not sessions_to_process:
                    print(f"  ℹ No sessions to process for this event")
            
            print(f"\n{'='*60}")
            print(f"YEAR {year} SUMMARY")
            print(f"{'='*60}")
            print(f"✓ Total Successful: {total_success}")
            print(f"✗ Total Failed: {total_failed}")
            print(f"{'='*60}")
            
            return {'success': total_success, 'failed': total_failed}
            
        except Exception as e:
            print(f"\n✗ Error generating year data: {e}")
            traceback.print_exc()
            return {'success': total_success, 'failed': total_failed}

    def close(self):
        """Close database connection"""
        if self.db_manager:
            self.db_manager.close()
            print("\n✓ Database connection closed")


# Convenience functions for quick usage
def generate_session_data(year: int, round_num: int, session_name: str, 
                         use_mongo: bool = True, include_comparisons: bool = False):
    """
    Quick function to generate all data for a session

    Args:
        year: Race year
        round_num: Round number
        session_name: Session name
        use_mongo: Whether to store in MongoDB
        include_comparisons: Whether to include driver comparisons (auto-detect top drivers)
    """
    generator = PlotDataGenerator(year=year, use_mongo=use_mongo)
    
    try:
        if include_comparisons:
            generator.generate_default_driver_comparisons(year, round_num, session_name)
        else:
            generator.generate_all_session_data(year, round_num, session_name)
        
        generator.print_summary()
    finally:
        generator.close()


def generate_multiple_sessions(sessions: List[Tuple[int, int, str]], 
                               use_mongo: bool = True,
                               include_comparisons: bool = False):
    """
    Generate data for multiple sessions

    Args:
        sessions: List of (year, round_num, session_name) tuples
        use_mongo: Whether to store in MongoDB
        include_comparisons: Whether to include driver comparisons
    """
    generator = PlotDataGenerator(use_mongo=use_mongo)
    
    try:
        for year, round_num, session_name in sessions:
            try:
                if include_comparisons:
                    generator.generate_default_driver_comparisons(year, round_num, session_name)
                else:
                    generator.generate_all_session_data(year, round_num, session_name)
            except Exception as e:
                print(f"\n✗ Error processing Y{year} R{round_num} {session_name}: {e}")
                traceback.print_exc()
        
        generator.print_summary()
    finally:
        generator.close()


def generate_year_data(year: int, use_mongo: bool = True, include_comparisons: bool = False,
                      session_types: Optional[List[str]] = None, skip_practice: bool = True):
    """
    Generate data for all sessions in a given year

    Args:
        year: Race year
        use_mongo: Whether to store in MongoDB
        include_comparisons: Whether to include driver comparisons (auto-detect top drivers)
        session_types: List of session types to include (e.g., ['Qualifying', 'Race']). 
                      If None, includes all sessions.
        skip_practice: Whether to skip practice sessions (default True)
    """
    generator = PlotDataGenerator(year=year, use_mongo=use_mongo)
    
    try:
        generator.generate_year_data(
            year=year,
            include_comparisons=include_comparisons,
            session_types=session_types,
            skip_practice=skip_practice
        )
        generator.print_summary()
    finally:
        generator.close()


# Interactive menu
def display_menu():
    """Display the main menu options"""
    print("\n" + "="*60)
    print("F1 PLOT DATA GENERATOR - Interactive Menu")
    print("="*60)
    print("\n📊 Available Options:\n")
    print("1. Generate data for a single session (basic)")
    print("2. Generate data for a single session (with driver comparisons)")
    print("3. Generate data for multiple sessions")
    print("4. Generate data for entire year (Qualifying & Race only)")
    print("5. Generate data for entire year (All sessions)")
    print("6. Generate data for entire year (with driver comparisons)")
    print("7. Custom configuration (advanced)")
    print("0. Exit")
    print("\n" + "-"*60)


def get_input(prompt, input_type=str, default=None):
    """Helper function to get user input with type conversion"""
    try:
        value = input(prompt).strip()
        if not value and default is not None:
            return default
        return input_type(value) if value else None
    except ValueError:
        print(f"Invalid input. Please enter a valid {input_type.__name__}")
        return get_input(prompt, input_type, default)


def run_option_1():
    """Single session - basic data"""
    print("\n📊 Generate data for a single session (basic)\n")
    year = get_input("Enter year (default 2025): ", int, 2025)
    round_num = get_input("Enter round number: ", int)
    session_name = get_input("Enter session name (e.g., 'Qualifying', 'Race'): ", str)
    use_mongo = get_input("Store to MongoDB? (y/n, default y): ", str, "y").lower() == 'y'
    
    generate_session_data(year, round_num, session_name, use_mongo=use_mongo, include_comparisons=False)


def run_option_2():
    """Single session with driver comparisons"""
    print("\n📊 Generate data for a single session (with driver comparisons)\n")
    year = get_input("Enter year (default 2025): ", int, 2025)
    round_num = get_input("Enter round number: ", int)
    session_name = get_input("Enter session name (e.g., 'Qualifying', 'Race'): ", str)
    use_mongo = get_input("Store to MongoDB? (y/n, default y): ", str, "y").lower() == 'y'
    
    generate_session_data(year, round_num, session_name, use_mongo=use_mongo, include_comparisons=True)


def run_option_3():
    """Multiple sessions"""
    print("\n📊 Generate data for multiple sessions\n")
    use_mongo = get_input("Store to MongoDB? (y/n, default y): ", str, "y").lower() == 'y'
    
    sessions_to_process = []
    print("\nEnter sessions (press Enter on year to finish):")
    
    while True:
        year = get_input(f"\nSession {len(sessions_to_process) + 1} - Year (Enter to finish): ", str)
        if not year:
            break
        year = int(year)
        round_num = get_input("  Round number: ", int)
        session_name = get_input("  Session name: ", str)
        sessions_to_process.append((year, round_num, session_name))
    
    if sessions_to_process:
        include_comp = get_input("\nInclude driver comparisons? (y/n, default n): ", str, "n").lower() == 'y'
        generate_multiple_sessions(sessions_to_process, use_mongo=use_mongo, include_comparisons=include_comp)
    else:
        print("No sessions added.")


def run_option_4():
    """Full year - Qualifying and Race only"""
    print("\n📊 Generate data for entire year (Qualifying & Race only)\n")
    year = get_input("Enter year (default 2025): ", int, 2025)
    use_mongo = get_input("Store to MongoDB? (y/n, default y): ", str, "y").lower() == 'y'
    
    generate_year_data(
        year=year, 
        use_mongo=use_mongo, 
        include_comparisons=False,
        session_types=['Qualifying', 'Race'],
        skip_practice=True
    )


def run_option_5():
    """Full year - All sessions"""
    print("\n📊 Generate data for entire year (All sessions)\n")
    year = get_input("Enter year (default 2025): ", int, 2025)
    use_mongo = get_input("Store to MongoDB? (y/n, default y): ", str, "y").lower() == 'y'
    
    generate_year_data(
        year=year, 
        use_mongo=use_mongo, 
        include_comparisons=False,
        skip_practice=False
    )


def run_option_6():
    """Full year with driver comparisons"""
    print("\n📊 Generate data for entire year (with driver comparisons)\n")
    year = get_input("Enter year (default 2025): ", int, 2025)
    use_mongo = get_input("Store to MongoDB? (y/n, default y): ", str, "y").lower() == 'y'
    skip_practice = get_input("Skip practice sessions? (y/n, default y): ", str, "y").lower() == 'y'
    
    generate_year_data(
        year=year, 
        use_mongo=use_mongo, 
        include_comparisons=True,
        skip_practice=skip_practice
    )


def run_option_7():
    """Custom configuration"""
    print("\n📊 Custom configuration (advanced)\n")
    year = get_input("Enter year (default 2025): ", int, 2025)
    round_num = get_input("Enter round number: ", int)
    session_name = get_input("Enter session name: ", str)
    use_mongo = get_input("Store to MongoDB? (y/n, default y): ", str, "y").lower() == 'y'
    
    print("\nEnter driver pairs for comparisons (e.g., VER,HAM):")
    print("(Press Enter on first driver to finish)")
    driver_pairs = []
    while True:
        pair = get_input(f"  Pair {len(driver_pairs) + 1} (driver1,driver2): ", str)
        if not pair:
            break
        drivers = pair.split(',')
        if len(drivers) == 2:
            driver_pairs.append((drivers[0].strip().upper(), drivers[1].strip().upper()))
        else:
            print("  Invalid format. Use: DRIVER1,DRIVER2")
    
    print("\nEnter drivers for lap time distributions (comma-separated):")
    drivers_input = get_input("  Drivers: ", str)
    drivers_for_laptimes = [d.strip().upper() for d in drivers_input.split(',')] if drivers_input else []
    
    generator = PlotDataGenerator(year=year, use_mongo=use_mongo)
    try:
        generator.generate_all_session_data(
            year=year,
            round_num=round_num,
            session_name=session_name,
            include_driver_comparisons=True,
            driver_pairs=driver_pairs if driver_pairs else None,
            drivers_for_laptimes=drivers_for_laptimes if drivers_for_laptimes else None
        )
        generator.print_summary()
    finally:
        generator.close()


# Main interactive menu
if __name__ == "__main__":
    """
    Interactive menu for PlotDataGenerator
    """
    while True:
        display_menu()
        choice = get_input("Select an option (0-7): ", str)
        
        try:
            if choice == '0':
                print("\n👋 Goodbye!")
                break
            elif choice == '1':
                run_option_1()
            elif choice == '2':
                run_option_2()
            elif choice == '3':
                run_option_3()
            elif choice == '4':
                run_option_4()
            elif choice == '5':
                run_option_5()
            elif choice == '6':
                run_option_6()
            elif choice == '7':
                run_option_7()
            else:
                print("\n❌ Invalid option. Please select 0-7.")
        except KeyboardInterrupt:
            print("\n\n⚠️  Operation cancelled by user.")
        except Exception as e:
            print(f"\n❌ Error: {e}")
            traceback.print_exc()
        
        if choice != '0':
            input("\nPress Enter to continue...")
