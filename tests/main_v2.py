"""
TurnOne Telemetry - V2 Main Interface
======================================
Interactive CLI for F1 telemetry visualization using custom F1StaticClient.
No FastF1 dependency required!

Usage:
    python main_v2.py                    # Interactive mode
    python main_v2.py --year 2025        # Pre-select year
    python main_v2.py -y 2025 -e "Italian Grand Prix" -s Race  # Direct
"""

import argparse
import sys
from typing import Optional, List, Dict
from datetime import datetime

from src.data_loader.f1_static_client import F1StaticClient
from src.scripts.simple.v2_top_speed import TopSpeedPlot_V2


def get_available_years() -> List[int]:
    """Get list of available F1 seasons."""
    current_year = datetime.now().year
    # F1 data typically available from 2018 onwards
    return list(range(2018, current_year + 1))


def get_events_for_year(year: int, client: F1StaticClient) -> List[Dict]:
    """
    Fetch available events for a given year.
    
    Returns:
        List of dicts with 'Name' and 'Sessions' keys
    """
    try:
        season_index = client.fetch_season_index(year)
        events = []
        for meeting in season_index.get('Meetings', []):
            event_info = {
                'Name': meeting.get('Name', 'Unknown'),
                'Location': meeting.get('Location', 'Unknown'),
                'Sessions': [s.get('Name', 'Unknown') for s in meeting.get('Sessions', [])]
            }
            events.append(event_info)
        return events
    except Exception as e:
        print(f"Error fetching events for {year}: {e}")
        return []


def interactive_session_selector() -> tuple:
    """
    Interactive CLI for selecting year/event/session.
    
    Returns:
        Tuple of (year, event_name, session_name)
    """
    client = F1StaticClient()
    
    # Step 1: Select Year
    print("\n" + "="*80)
    print("🏎️  TURNONE TELEMETRY - V2 (FastF1-Free)")
    print("="*80)
    
    available_years = get_available_years()
    print("\n📅 Available Seasons:")
    for i, year in enumerate(available_years, 1):
        print(f"  [{i}] {year}")
    
    while True:
        try:
            choice = input(f"\nSelect season (1-{len(available_years)}) or year (e.g., 2025): ").strip()
            if choice.isdigit():
                num = int(choice)
                if 1 <= num <= len(available_years):
                    year = available_years[num - 1]
                    break
                elif num in available_years:
                    year = num
                    break
            print("❌ Invalid selection. Try again.")
        except KeyboardInterrupt:
            print("\n\n👋 Exiting...")
            sys.exit(0)
    
    print(f"\n✓ Selected: {year} Season")
    
    # Step 2: Fetch and display events
    print(f"\n🔍 Fetching {year} calendar...")
    events = get_events_for_year(year, client)
    
    if not events:
        print(f"❌ No events found for {year}")
        sys.exit(1)
    
    print(f"\n🏁 {year} Grand Prix Events:")
    for i, event in enumerate(events, 1):
        print(f"  [{i:2d}] {event['Name']} ({event['Location']})")
    
    while True:
        try:
            choice = input(f"\nSelect event (1-{len(events)}): ").strip()
            if choice.isdigit():
                num = int(choice)
                if 1 <= num <= len(events):
                    event = events[num - 1]
                    break
            print("❌ Invalid selection. Try again.")
        except KeyboardInterrupt:
            print("\n\n👋 Exiting...")
            sys.exit(0)
    
    event_name = event['Name']
    print(f"\n✓ Selected: {event_name}")
    
    # Step 3: Select Session
    sessions = event['Sessions']
    print(f"\n📊 Available Sessions:")
    for i, session in enumerate(sessions, 1):
        print(f"  [{i}] {session}")
    
    while True:
        try:
            choice = input(f"\nSelect session (1-{len(sessions)}): ").strip()
            if choice.isdigit():
                num = int(choice)
                if 1 <= num <= len(sessions):
                    session_name = sessions[num - 1]
                    break
            print("❌ Invalid selection. Try again.")
        except KeyboardInterrupt:
            print("\n\n👋 Exiting...")
            sys.exit(0)
    
    print(f"\n✓ Selected: {session_name}")
    
    return year, event_name, session_name


def main():
    """Main entry point for V2 telemetry visualization."""
    
    parser = argparse.ArgumentParser(
        description='TurnOne Telemetry V2 - FastF1-Free F1 Data Visualization',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main_v2.py                                    # Interactive mode
  python main_v2.py -y 2025                            # Pre-select year
  python main_v2.py -y 2025 -e "Italian Grand Prix" -s Race
  python main_v2.py -y 2024 -e "Monaco" -s Qualifying --no-cache
        """
    )
    
    parser.add_argument('-y', '--year', type=int, help='F1 season year')
    parser.add_argument('-e', '--event', type=str, help='Event name (e.g., "Italian Grand Prix")')
    parser.add_argument('-s', '--session', type=str, help='Session name (e.g., "Race", "Qualifying")')
    parser.add_argument('--no-cache', action='store_true', help='Disable MongoDB cache')
    parser.add_argument('--plot', default='top_speed', 
                       choices=['top_speed'], 
                       help='Plot type (currently only top_speed available)')
    
    args = parser.parse_args()
    
    # If all parameters provided, use direct mode
    if args.year and args.event and args.session:
        year = args.year
        event_name = args.event
        session_name = args.session
        print(f"\n🎯 Direct Mode: {year} {event_name} {session_name}")
    else:
        # Interactive mode
        year, event_name, session_name = interactive_session_selector()
    
    # Generate plot
    print("\n" + "="*80)
    print("🚀 GENERATING PLOT")
    print("="*80)
    print(f"📊 Plot Type: {args.plot.replace('_', ' ').title()}")
    print(f"📅 Season: {year}")
    print(f"🏁 Event: {event_name}")
    print(f"🎪 Session: {session_name}")
    print(f"💾 Cache: {'Disabled' if args.no_cache else 'Enabled'}")
    print("="*80 + "\n")
    
    try:
        if args.plot == 'top_speed':
            result = TopSpeedPlot_V2(
                year, 
                event_name, 
                session_name, 
                use_cache=not args.no_cache
            )
            print(f"\n✅ SUCCESS!")
            print(f"📁 Plot saved: {result}")
        else:
            print(f"❌ Plot type '{args.plot}' not yet implemented in V2")
            print("   Available: top_speed")
            print("   Coming soon: lap_times, throttle_comparison, etc.")
            
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
