"""
Organize circuit data into separate files by year and circuit.

Takes the all_circuits_2024-2026.json file and reorganizes it into:
- src/domain/data/circuits/{year}/all_circuits.json (all circuits for that year)
- src/domain/data/circuits/{year}/{circuit_id}_{circuit_name}.json (individual circuit files)
"""

import json
from pathlib import Path
from typing import Dict, Any
import re


def slugify(name: str) -> str:
    """
    Convert circuit name to a filename-safe slug.
    
    Args:
        name: Circuit name
        
    Returns:
        Slugified name (lowercase, hyphens instead of spaces)
    """
    # Convert to lowercase and replace spaces with hyphens
    slug = name.lower().replace(" ", "_")
    # Remove special characters except hyphens and underscores
    slug = re.sub(r"[^a-z0-9_-]", "", slug)
    return slug


def organize_circuits_data():
    """
    Organize circuit data from the combined file into year and circuit-specific files.
    """
    # Read the combined circuits file
    combined_file = Path("src/domain/data/circuits/all_circuits_2024-2026.json")
    
    if not combined_file.exists():
        print(f"✗ File not found: {combined_file}")
        print("Run 'python fetch_circuits.py' first to fetch the data.")
        return
    
    print(f"✓ Reading {combined_file}")
    with open(combined_file, "r", encoding="utf-8") as f:
        all_data = json.load(f)
    
    base_dir = Path("src/domain/data/circuits")
    years = all_data.get("meta", {}).get("years", [2024, 2025, 2026])
    circuits = all_data.get("circuits", {})
    
    # Create files for each year
    for year in years:
        year_dir = base_dir / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"\nProcessing year {year}:")
        
        # Organize circuits for this year
        # Note: JSON keys are strings, so we need to use str(year)
        year_circuits = all_data.get("circuits_by_year", {}).get(str(year), [])
        
        # Create all_circuits.json for this year
        year_data = {
            "year": year,
            "total_circuits": len(year_circuits),
            "circuits": []
        }
        
        for circuit_entry in year_circuits:
            circuit_id = circuit_entry.get("circuit_id")
            circuit_name = circuit_entry.get("name", "Unknown")
            circuit_detail = circuit_entry.get("data", {})
            
            # Add to year summary (without detailed race data)
            circuit_summary = {
                "circuit_id": circuit_id,
                "name": circuit_name,
            }
            
            # Add base info fields if available
            if circuit_id in circuits:
                base_info = circuits[circuit_id]
                circuit_summary.update(base_info)
            
            year_data["circuits"].append(circuit_summary)
            
            # Create individual circuit file
            slug = slugify(circuit_name)
            circuit_filename = f"{circuit_id}_{slug}.json"
            circuit_file = year_dir / circuit_filename
            
            circuit_data = {
                "year": year,
                "circuit_id": circuit_id,
                "name": circuit_name,
                "base_info": circuits.get(circuit_id, {}),
                "race_data": circuit_detail
            }
            
            with open(circuit_file, "w", encoding="utf-8") as f:
                json.dump(circuit_data, f, indent=2, ensure_ascii=False)
            
            print(f"  ✓ {circuit_filename}")
        
        # Save all_circuits.json for this year
        all_circuits_file = year_dir / "all_circuits.json"
        with open(all_circuits_file, "w", encoding="utf-8") as f:
            json.dump(year_data, f, indent=2, ensure_ascii=False)
        
        print(f"  ✓ all_circuits.json (contains {len(year_circuits)} circuits)")
    
    print("\n" + "="*60)
    print("ORGANIZATION COMPLETE")
    print("="*60)
    print(f"\nDirectory structure created:")
    print("src/domain/data/circuits/")
    for year in years:
        year_dir = base_dir / str(year)
        circuit_count = len(list(year_dir.glob("*.json"))) - 1  # -1 for all_circuits.json
        print(f"  {year}/")
        print(f"    all_circuits.json")
        print(f"    {circuit_count} individual circuit files")
    print("\n✓ All circuit data organized successfully!")


if __name__ == "__main__":
    organize_circuits_data()
