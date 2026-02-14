"""
Test script to verify v2 MongoDB separation and round number handling
"""

from src.data_loader.f1_static_client import F1StaticClient
from src.utils.database.mongo_helper import get_country_code_from_event_name

def test_v2_round_numbering():
    """Test that v2 correctly maps round numbers to events"""
    client = F1StaticClient()
    year = 2025
    
    print("=" * 80)
    print("Testing v2 (F1StaticClient) Round Numbering")
    print("=" * 80)
    
    # Test first few rounds
    for round_nr in range(1, 6):
        event_name = client.get_event_name(year, round_nr)
        country_code = get_country_code_from_event_name(event_name)
        gp_id = f"{year}_{country_code}"
        
        print(f"Round {round_nr}: {event_name:30s} -> GP ID: {gp_id}")
    
    print("\n" + "=" * 80)
    print("Expected for v2 (F1StaticClient):")
    print("  Round 1: Pre-Season Testing")
    print("  Round 2: Australian Grand Prix (first GP)")
    print("  Round 3: Chinese Grand Prix (second GP)")
    print("=" * 80)
    
    print("\n" + "=" * 80)
    print("Expected for v1 (FastF1):")
    print("  Round 1: Australian Grand Prix (first GP)")
    print("  Round 2: Chinese Grand Prix (second GP)")
    print("  Round 3: Bahrain Grand Prix (third GP)")
    print("=" * 80)

if __name__ == "__main__":
    test_v2_round_numbering()
