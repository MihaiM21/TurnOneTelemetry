"""
Fetch circuit data from the multiviewer.app API for all years 2024-2026.
This script retrieves circuit information and saves it to a JSON file.
"""

import json
import requests
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime

from src.core.logging import get_logger

logger = get_logger(__name__)

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
        logger.info("Fetched %s circuits", len(circuits))
        return circuits
    except requests.RequestException as e:
        logger.error("Error fetching circuits list: %s", e)
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
        logger.error("Error fetching circuit %s for %s: %s", circuit_nr, year, e)
        return None


def fetch_all_circuits_data() -> Dict[str, Any]:
    """
    Fetch all circuits and their data for all years.

    Returns:
        Dictionary containing all circuit data organized by year
    """
    logger.info("Starting circuit data fetch...")

    # Fetch circuits list
    circuits_dict = fetch_circuits_list()
    if not circuits_dict:
        logger.warning("No circuits to process")
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

        logger.info("Fetching data for %s (ID: %s)", circuit_name, circuit_id)

        for year in YEARS:
            current_request += 1
            logger.debug("Fetching [%s/%s] Year %s", current_request, total_requests, year)

            circuit_data = fetch_circuit_data(circuit_id, year)
            if circuit_data:
                all_data["circuits_by_year"][year].append({
                    "circuit_id": circuit_id,
                    "name": circuit_name,
                    "data": circuit_data
                })
                logger.debug("Successfully fetched %s for %s", circuit_name, year)
            else:
                logger.warning("Failed to fetch %s for %s", circuit_name, year)

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

    logger.info("Data saved to %s", output_file)
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
        logger.warning("Circuit data file not found: %s", file_path)
        return None

    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_circuit_data_stats(data: Dict[str, Any]) -> None:
    """
    Log statistics about the fetched circuit data.

    Args:
        data: Circuit data dictionary
    """
    logger.info("CIRCUIT DATA STATISTICS")

    meta = data.get("meta", {})
    logger.info("Fetched at: %s", meta.get('fetched_at', 'Unknown'))
    logger.info("Total circuits: %s", meta.get('total_circuits', 0))
    logger.info("Years covered: %s", ', '.join(map(str, meta.get('years', []))))

    logger.info("Circuits by year:")
    for year in YEARS:
        count = len(data.get("circuits_by_year", {}).get(year, []))
        logger.info("  %s: %s circuits", year, count)


if __name__ == "__main__":
    # Fetch all circuit data
    circuit_data = fetch_all_circuits_data()

    if circuit_data:
        # Save to file
        output_path = save_data(circuit_data)

        # Log statistics
        get_circuit_data_stats(circuit_data)

        logger.info("Successfully fetched and saved circuit data!")
        logger.info("Output file: %s", output_path)
    else:
        logger.error("Failed to fetch circuit data")
