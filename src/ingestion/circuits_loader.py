"""Read-side access to the stored circuit layouts.

Every circuit file lives under :data:`CIRCUITS_DIR` as
``{year}/{circuit_id}_{slug}.json`` (our own :class:`CircuitLayout` schema) with a
per-year ``all_circuits.json`` manifest of :class:`CircuitSummary` entries.
``circuit_id`` is the livetiming ``Meeting.Circuit.Key``, which is also
multiviewer's circuit id.

The path is cwd-relative on purpose (Docker ``WORKDIR`` is the repo root).
``circuits_store`` and ``circuits_sync`` read the constant through this module
at call time so a single ``monkeypatch.setattr(circuits_loader, "CIRCUITS_DIR",
tmp_path)`` redirects reads and writes together in tests.
"""
import json
import os
from pathlib import Path

CIRCUITS_DIR = Path("src/domain/data/circuits")
MANIFEST_NAME = "all_circuits.json"


def get_yearly_circuits(season_year):
    """Get circuits data (CircuitSummary list) for a given season year"""

    try:
        with open(CIRCUITS_DIR / str(season_year) / MANIFEST_NAME, "r") as f:
            data = json.load(f)
            # Handle if circuits are nested under a key
            if isinstance(data, dict) and "circuits" in data:
                return data["circuits"]
            return data if isinstance(data, list) else []
    except FileNotFoundError:
        raise ValueError(f"Circuits data for season year {season_year} not found.")


def get_circuit_data_by_id(circuit_id, season_year):
    """Get circuit summary for a given circuit ID and season year"""
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
    """Get the full CircuitLayout (corners, marshal lights/sectors, rotation, track
    outline, etc.) stored for a circuit/year, in our own schema."""
    try:
        # Look for the circuit data file matching the circuit_id
        circuit_dir = CIRCUITS_DIR / str(season_year)
        # Find file matching pattern {circuit_id}_*.json
        for filename in os.listdir(circuit_dir):
            if filename.startswith(f"{circuit_id}_") and filename.endswith(".json") and filename != MANIFEST_NAME:
                filepath = os.path.join(circuit_dir, filename)
                with open(filepath, "r") as f:
                    return json.load(f)
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
