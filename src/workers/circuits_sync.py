"""
Light fallback sync for circuit data.

Our stored circuit files (src/domain/data/circuits/{year}/) are the source of truth,
seeded once from api.multiviewer.app via fetch_circuits.py + organize_circuits.py.
This module only ever ADDS circuits/years we don't already have on disk - it never
overwrites or re-derives anything that's already stored, and any network failure is
logged and skipped rather than raised, so multiviewer being unreachable can never
break the running API.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set, Tuple

from src.core.logging import get_logger
from src.ingestion.circuit_sources.multiviewer_adapter import adapt_circuit_layout, adapt_circuit_summary
from src.workers.fetch_circuits import YEARS, fetch_circuit_data, fetch_circuits_list
from src.workers.organize_circuits import slugify

logger = get_logger(__name__)

BASE_DIR = Path("src/domain/data/circuits")


def _known_circuit_ids_for_year(year: int) -> Set[str]:
    year_dir = BASE_DIR / str(year)
    if not year_dir.exists():
        return set()
    return {
        filename.split("_", 1)[0]
        for filename in os.listdir(year_dir)
        if filename != "all_circuits.json" and filename.endswith(".json")
    }


def find_missing_circuits_and_years() -> List[Tuple[str, int]]:
    """Diff multiviewer's circuit list against what we already have stored."""
    try:
        circuits_dict = fetch_circuits_list()
    except Exception as e:
        logger.warning(f"circuits_sync: could not reach multiviewer.app, skipping: {e}")
        return []

    if not circuits_dict:
        return []

    missing: List[Tuple[str, int]] = []
    for year in YEARS:
        known_ids = _known_circuit_ids_for_year(year)
        for circuit_id in circuits_dict:
            if circuit_id not in known_ids:
                missing.append((circuit_id, year))
    return missing


def sync_missing_circuits() -> int:
    """Fetch and store only circuits/years we don't already have. Returns count added."""
    missing = find_missing_circuits_and_years()
    if not missing:
        logger.debug("circuits_sync: nothing missing, local data is up to date")
        return 0

    logger.info(f"circuits_sync: found {len(missing)} missing circuit/year combinations")

    try:
        circuits_dict = fetch_circuits_list()
    except Exception as e:
        logger.warning(f"circuits_sync: could not reach multiviewer.app, skipping: {e}")
        return 0

    added = 0
    fetched_at = datetime.now(timezone.utc).isoformat()

    for circuit_id, year in missing:
        base_info = circuits_dict.get(circuit_id, {})
        circuit_name = base_info.get("name", "unknown")

        try:
            raw_detail = fetch_circuit_data(circuit_id, year)
        except Exception as e:
            logger.warning(f"circuits_sync: failed to fetch circuit {circuit_id} for {year}: {e}")
            continue

        if not raw_detail:
            continue

        try:
            year_dir = BASE_DIR / str(year)
            year_dir.mkdir(parents=True, exist_ok=True)

            layout = adapt_circuit_layout(raw_detail, circuit_id, year, source_fetched_at=fetched_at)
            slug = slugify(circuit_name)
            circuit_file = year_dir / f"{circuit_id}_{slug}.json"
            with open(circuit_file, "w", encoding="utf-8") as f:
                json.dump(layout.model_dump(), f, indent=2, ensure_ascii=False)

            _append_to_year_summary(year_dir, circuit_id, base_info, year)
            added += 1
            logger.info(f"circuits_sync: added {circuit_file.name}")
        except Exception as e:
            logger.warning(f"circuits_sync: failed to store circuit {circuit_id} for {year}: {e}")

    return added


def _append_to_year_summary(year_dir: Path, circuit_id: str, base_info: dict, year: int) -> None:
    summary_file = year_dir / "all_circuits.json"
    if summary_file.exists():
        with open(summary_file, "r", encoding="utf-8") as f:
            year_data = json.load(f)
    else:
        year_data = {"year": year, "total_circuits": 0, "circuits": []}

    if any(c.get("circuit_id") == circuit_id for c in year_data["circuits"]):
        return

    years_available = sorted(set(base_info.get("years", []) + [year]))
    summary = adapt_circuit_summary(base_info, circuit_id, years_available)
    year_data["circuits"].append(summary.model_dump())
    year_data["total_circuits"] = len(year_data["circuits"])

    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(year_data, f, indent=2, ensure_ascii=False)


class CircuitsSyncWorker:
    """Background loop that periodically tops up missing circuit/year data."""

    def __init__(self, check_interval: int = 2_592_000):
        self.check_interval = check_interval
        self.running = False

    async def run(self):
        self.running = True
        logger.info(f"Circuits sync worker started (interval: {self.check_interval}s)")

        while self.running:
            try:
                await asyncio.to_thread(sync_missing_circuits)
            except Exception as e:
                logger.error(f"Error in circuits sync loop: {e}", exc_info=True)

            await asyncio.sleep(self.check_interval)

    def stop(self):
        logger.info("Stopping circuits sync worker...")
        self.running = False


_worker: Optional[CircuitsSyncWorker] = None


def get_circuits_sync_worker() -> CircuitsSyncWorker:
    global _worker
    if _worker is None:
        from src.core.config import settings
        _worker = CircuitsSyncWorker(check_interval=settings.circuits_sync_interval_seconds)
    return _worker


async def start_circuits_sync():
    worker = get_circuits_sync_worker()
    await worker.run()


async def stop_circuits_sync():
    if _worker:
        _worker.stop()
