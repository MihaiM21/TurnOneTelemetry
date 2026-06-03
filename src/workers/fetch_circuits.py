"""
Fetch circuit data from the multiviewer.app API for all years 2024-2026.
This script retrieves circuit information and saves it to a JSON file.
"""

import json
import requests
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime

BASE_URL = "https://api.multiviewer.app/api/v1"
YEARS = [2024, 2025, 2026]
OUTPUT_DIR = Path("src/domain/data/circuits")

# Headers to avoid 403 Forbidden errors
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def fetch_circuits_list() -> Dict[str, Dict[str, Any]]:
    """
    Fetch the list of all circuits from the API.
    Returns a dictionary with circuit IDs as keys.
    
    Returns:
        Dictionary of circuits with their information
    """
    try:
        response = requests.get(f"{BASE_URL}/circuits/", headers=HEADERS)
        response.raise_for_status()
        circuits = response.json()
        print(f"✓ Fetched {len(circuits)} circuits")
        return circuits
    except requests.RequestException as e:
        print(f"✗ Error fetching circuits list: {e}")
        return {}


def fetch_circuit_data(circuit_nr: str, year: int) -> Dict[str, Any] | None:
    """
    Fetch detailed circuit data for a specific circuit and year.
    
    Args:
        circuit_nr: Circuit number/ID (as string)
        year: Year to fetch data for
        
    Returns:
        Circuit data dictionary or None if request fails
    """
    try:
        response = requests.get(f"{BASE_URL}/circuits/{circuit_nr}/{year}", headers=HEADERS)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"  ✗ Error fetching circuit {circuit_nr} for {year}: {e}")
        return None


def fetch_all_circuits_data() -> Dict[str, Any]:
    """
    Fetch all circuits and their data for all years.
    
    Returns:
        Dictionary containing all circuit data organized by year
    """
    print("Starting circuit data fetch...\n")
    
    # Fetch circuits list
    circuits_dict = fetch_circuits_list()
    if not circuits_dict:
        print("No circuits to process")
        return {}
    
    # Organize data by year
    all_data = {
        "meta": {
            "fetched_at": datetime.now().isoformat(),
            "years": YEARS,
            "total_circuits": len(circuits_dict)
        },
        "circuits": {},
        "circuits_by_year": {year: [] for year in YEARS}
    }
    
    # Store base circuit info
    for circuit_id, circuit_info in circuits_dict.items():
        all_data["circuits"][circuit_id] = circuit_info
    
    # Fetch data for each circuit and year
    total_requests = len(circuits_dict) * len(YEARS)
    current_request = 0
    
    for circuit_id, circuit_info in circuits_dict.items():
        circuit_name = circuit_info.get("name", "Unknown")
        
        print(f"\nFetching data for {circuit_name} (ID: {circuit_id}):")
        
        for year in YEARS:
            current_request += 1
            print(f"  [{current_request}/{total_requests}] Year {year}...", end=" ")
            
            circuit_data = fetch_circuit_data(circuit_id, year)
            if circuit_data:
                all_data["circuits_by_year"][year].append({
                    "circuit_id": circuit_id,
                    "name": circuit_name,
                    "data": circuit_data
                })
                print("✓")
            else:
                print("✗")
    
    return all_data


def save_data(data: Dict[str, Any], output_file: Path | None = None) -> Path:
    """
    Save circuit data to a JSON file.
    
    Args:
        data: Circuit data to save
        output_file: Output file path (default: data/circuits/all_circuits_2024-2026.json)
        
    Returns:
        Path to the saved file
    """
    if output_file is None:
        output_dir = Path(OUTPUT_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "all_circuits_2024-2026.json"
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Data saved to {output_file}")
    return output_file


def load_circuits_data(file_path: Path | None = None) -> Dict[str, Any] | None:
    """
    Load previously fetched circuit data from a JSON file.
    
    Args:
        file_path: Path to the circuits JSON file
        
    Returns:
        Circuit data dictionary or None if file not found
    """
    if file_path is None:
        file_path = Path(OUTPUT_DIR) / "all_circuits_2024-2026.json"
    
    if not file_path.exists():
        print(f"Circuit data file not found: {file_path}")
        return None
    
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_circuit_data_stats(data: Dict[str, Any]) -> None:
    """
    Print statistics about the fetched circuit data.
    
    Args:
        data: Circuit data dictionary
    """
    print("\n" + "="*60)
    print("CIRCUIT DATA STATISTICS")
    print("="*60)
    
    meta = data.get("meta", {})
    print(f"Fetched at: {meta.get('fetched_at', 'Unknown')}")
    print(f"Total circuits: {meta.get('total_circuits', 0)}")
    print(f"Years covered: {', '.join(map(str, meta.get('years', [])))}")
    
    print("\nCircuits by year:")
    for year in YEARS:
        count = len(data.get("circuits_by_year", {}).get(year, []))
        print(f"  {year}: {count} circuits")
    
    print("="*60 + "\n")


if __name__ == "__main__":
    # Fetch all circuit data
    circuit_data = fetch_all_circuits_data()
    
    if circuit_data:
        # Save to file
        output_path = save_data(circuit_data)
        
        # Print statistics
        get_circuit_data_stats(circuit_data)
        
        print(f"Successfully fetched and saved circuit data!")
        print(f"Output file: {output_path}")
    else:
        print("Failed to fetch circuit data")
