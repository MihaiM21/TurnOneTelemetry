"""
Light fallback sync for circuit data.

Our stored circuit files (src/domain/data/circuits/{year}/) are the source of truth,
seeded once from api.multiviewer.app via fetch_circuits.py + organize_circuits.py.
This module only ever ADDS circuits/years we don't already have on disk - it never
overwrites a multiviewer layout, and any network failure is logged and skipped
rather than raised, so multiviewer being unreachable can never break the running
API.

The one thing it *does* replace is a telemetry-derived layout
(``source == "telemetry"``, see src/services/circuit_derivation.py): those are a
stopgap traced from a single lap for a circuit multiviewer had not published yet,
so they count as "missing" here and the real layout (marshal posts, official
corner numbering) takes over as soon as it exists upstream. The write goes
through circuits_store.save_circuit_layout, which removes the stale derived file
and replaces its manifest entry.
"""

import asyncio
from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple

from src.core.logging import get_logger
from src.ingestion import circuits_store
from src.ingestion.circuit_sources.multiviewer_adapter import adapt_circuit_layout, adapt_circuit_summary
from src.workers.fetch_circuits import YEARS, fetch_circuit_data, fetch_circuits_list

logger = get_logger(__name__)

MULTIVIEWER_SOURCE = "multiviewer"


def _known_circuit_ids_for_year(year: int) -> Set[str]:
    """Circuit ids for ``year`` that already hold a *multiviewer* layout.

    A telemetry-derived file is deliberately not "known": it is provisional,
    and multiviewer's version must replace it when available.
    """
    return {
        circuit_id
        for circuit_id, info in circuits_store.list_circuit_files(year).items()
        if info.get("source") == MULTIVIEWER_SOURCE
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
            layout = adapt_circuit_layout(raw_detail, circuit_id, year, source_fetched_at=fetched_at)
            if not layout.name:
                layout.name = circuit_name
            years_available = sorted(set(base_info.get("years", []) + [year]))
            summary = adapt_circuit_summary(base_info, circuit_id, years_available)
            circuit_file = circuits_store.save_circuit_layout(layout, summary)
            added += 1
            logger.info(f"circuits_sync: added {circuit_file.name}")
        except Exception as e:
            logger.warning(f"circuits_sync: failed to store circuit {circuit_id} for {year}: {e}")

    return added


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
