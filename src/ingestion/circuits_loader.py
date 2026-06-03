import json
import os

def get_yearly_circuits(season_year):
    """Get circuits data for a given season year"""

    try:
        with open(f"src/domain/data/circuits/{season_year}/all_circuits.json", "r") as f:
            data = json.load(f)
            # Handle if circuits are nested under a key
            if isinstance(data, dict) and "circuits" in data:
                return data["circuits"]
            return data if isinstance(data, list) else []
    except FileNotFoundError:
        raise ValueError(f"Circuits data for season year {season_year} not found.")
    
def get_circuit_data_by_id(circuit_id, season_year):
    """Get circuit data for a given circuit ID and season year"""
    circuits = get_yearly_circuits(season_year)
    # Convert circuit_id to int for comparison (circuit IDs are numeric)
    try:
        circuit_id_int = int(circuit_id)
        for circuit in circuits:
            if int(circuit.get("circuit_id", -1)) == circuit_id_int:
                return circuit
    except (ValueError, TypeError):
        # Fallback to string comparison if conversion fails
        for circuit in circuits:
            if str(circuit.get("circuit_id", "")) == str(circuit_id):
                return circuit
    return None

def get_circuit_data_file(circuit_id, season_year):
    """Get x and y coordinate data (SVG path) from the individual circuit JSON file in original order"""
    try:
        # Look for the circuit data file matching the circuit_id
        circuit_dir = f"src/domain/data/circuits/{season_year}"
        # Find file matching pattern {circuit_id}_*.json
        for filename in os.listdir(circuit_dir):
            if filename.startswith(f"{circuit_id}_") and filename.endswith(".json") and filename != "all_circuits.json":
                filepath = os.path.join(circuit_dir, filename)
                with open(filepath, "r") as f:
                    data = json.load(f)
                    # Extract only x and y data from race_data
                    if "race_data" in data:
                        return {
                            "x": data["race_data"].get("x", []),
                            "y": data["race_data"].get("y", [])
                        }
                    return {"x": [], "y": []}
        raise ValueError(f"Circuit data file for ID {circuit_id} not found in {season_year}")
    except FileNotFoundError:
        raise ValueError(f"Circuits directory for year {season_year} not found")

def get_circuit_data_by_name(circuit_name, season_year):
    """Get circuit data for a given circuit name and season year"""
    circuits = get_yearly_circuits(season_year)
    for circuit in circuits:
        if circuit["name"].lower() == circuit_name.lower():
            return circuit
    return None