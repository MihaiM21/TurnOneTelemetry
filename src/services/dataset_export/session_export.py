"""Export one session: build a store, run the requested tiers, record the outcome.

Tier order is ``tables -> corpus -> raw -> telemetry``: the corpus needs the
tables in memory, and the two multi-hundred-megabyte telemetry streams come
last so an early cancel or failure has already produced everything cheap.

The store is built with the session *abbreviation* (``R``, ``Q``, ``FP1``…)
exactly as the planner yields it, because that is what the GridFS raw-stream
cache is keyed by; building it with ``"Race"`` would silently miss the durable
cache and re-download every stream. ``shared_session_stores`` is deliberately
not used here — it would pin every session's parsed streams for the life of the
job.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.core.exceptions import DataNotAvailableError, SessionNotFoundError, UpstreamUnavailableError
from src.core.logging import get_logger
from src.services.analysis.v2._helpers import build_session_store
from src.services.dataset_export import builders, corpus, raw, telemetry
from src.services.dataset_export.manifest import Manifest, TierEntry, session_key
from src.services.dataset_export.schema import TABLES, Target, tables_for_tier
from src.services.dataset_export.writer import FileStat, ParquetChunkWriter, partition_dir, write_parquet

logger = get_logger(__name__)

TIER_ORDER = ("tables", "corpus", "raw", "telemetry")

_CORPUS_LOCK = threading.Lock()


@dataclass
class SessionOutcome:
    session_key: str
    per_tier: Dict[str, str] = field(default_factory=dict)  # tier -> done|skipped|failed|absent|cancelled
    warnings: List[str] = field(default_factory=list)
    bytes_written: int = 0

    @property
    def failed(self) -> bool:
        return any(status == "failed" for status in self.per_tier.values())


def _now() -> str:
    from src.workers._job_progress import now
    return now()


def _entry(files: List[FileStat], *, absent: Optional[List[str]] = None, status: str = "done") -> TierEntry:
    return TierEntry(
        status=status,
        files=list(files),
        rows=sum(f.rows for f in files),
        bytes=sum(f.bytes for f in files),
        finished_at=_now(),
        absent_streams=list(absent or []),
    )


def _failed_entry(exc: BaseException) -> TierEntry:
    return TierEntry(status="failed", error=f"{type(exc).__name__}: {exc}"[:500], finished_at=_now())


def _export_tables(store: Any, target: Target, root: Path) -> tuple:
    """Write every ``tables``-tier Parquet file; returns ``(entry, tables, warnings)``."""
    year, round_nr, _gp, session = target
    tables, warnings = builders.build_tables(store, target)
    files: List[FileStat] = []
    for spec in tables_for_tier("tables"):
        df = tables.get(spec.name)
        if df is None:
            continue
        path = partition_dir(root, spec.name, year, round_nr, session) / "part.parquet"
        files.append(write_parquet(df, path, spec, root))
    return _entry(files), tables, warnings


def _export_corpus(target: Target, tables: Dict[str, Any], root: Path) -> TierEntry:
    counts = corpus.write_corpus(target, tables, root, _CORPUS_LOCK)
    entry = TierEntry(status="done", rows=sum(counts.values()), finished_at=_now())
    return entry


def _export_raw(store: Any, target: Target, root: Path, cancelled: Callable[[], bool]) -> TierEntry:
    year, round_nr, _gp, session = target
    dest = partition_dir(root, "raw", year, round_nr, session)
    written, absent = raw.write_raw_streams(store, dest, root, cancelled=cancelled)
    files = list(written.values())
    if cancelled():
        return _entry(files, absent=absent, status="pending")
    return _entry(files, absent=absent)


def _export_telemetry(store: Any, target: Target, root: Path, cancelled: Callable[[], bool]) -> TierEntry:
    year, round_nr, _gp, session = target
    files: List[FileStat] = []
    absent: List[str] = []
    streams = (
        ("car_telemetry", telemetry.iter_car_telemetry),
        ("position_telemetry", telemetry.iter_position_telemetry),
    )
    for table, iterator in streams:
        if cancelled():
            return _entry(files, absent=absent, status="pending")
        spec = TABLES[table]
        path = partition_dir(root, table, year, round_nr, session) / "part.parquet"
        chunks = iterator(store, target)
        # Pull the first chunk before opening the writer: an absent stream raises
        # on the first pull and must not leave an empty partition directory.
        try:
            first = next(chunks)
        except StopIteration:
            first = None
        except DataNotAvailableError:
            absent.append(table)
            continue
        with ParquetChunkWriter(path, spec, root) as writer:
            if first is not None:
                writer.write(first)
                for chunk in chunks:
                    writer.write(chunk)
        files.append(writer.stat)
    return _entry(files, absent=absent)


def export_session(
    target: Target,
    root: Path,
    tiers: List[str],
    manifest: Manifest,
    *,
    cancelled: Optional[Callable[[], bool]] = None,
    resume: bool = True,
) -> SessionOutcome:
    """Run the requested tiers for one session and record each in ``manifest``.

    The caller owns persisting the manifest. A tier already ``done`` (or
    ``absent``) is skipped when ``resume`` is set. A tier raising anything but a
    cancellation is marked ``failed`` with the error and the remaining tiers
    still run, so one bad stream does not cost the session's cheap tables.
    """
    cancelled = cancelled or (lambda: False)
    year, round_nr, gp_name, session = target
    key = session_key(year, round_nr, session)
    outcome = SessionOutcome(session_key=key)
    mark_kw = {"year": year, "round": round_nr, "gp_name": gp_name, "session": session}

    wanted = [tier for tier in TIER_ORDER if tier in tiers]
    if resume:
        for tier in list(wanted):
            if manifest.is_done(key, tier):
                outcome.per_tier[tier] = "skipped"
                wanted.remove(tier)
    if not wanted:
        return outcome

    store = build_session_store(year, round_nr, session)
    if store is None:
        for tier in wanted:
            entry = TierEntry(status="failed", error="session could not be resolved", finished_at=_now())
            manifest.mark(key, tier, entry, **mark_kw)
            outcome.per_tier[tier] = "failed"
        return outcome

    tables: Optional[Dict[str, Any]] = None
    try:
        for tier in wanted:
            if cancelled():
                outcome.per_tier[tier] = "cancelled"
                continue
            try:
                if tier == "tables":
                    entry, tables, warnings = _export_tables(store, target, root)
                    outcome.warnings.extend(f"{key} {w}" for w in warnings)
                elif tier == "corpus":
                    if tables is None:
                        tables, warnings = builders.build_tables(store, target)
                        outcome.warnings.extend(f"{key} {w}" for w in warnings)
                    entry = _export_corpus(target, tables, root)
                elif tier == "raw":
                    entry = _export_raw(store, target, root, cancelled)
                elif tier == "telemetry":
                    entry = _export_telemetry(store, target, root, cancelled)
                else:
                    raise ValueError(f"unknown tier {tier!r}")
            except (SessionNotFoundError, DataNotAvailableError, UpstreamUnavailableError) as exc:
                logger.warning("%s %s: %s", key, tier, exc)
                entry = _failed_entry(exc)
            except Exception as exc:  # noqa: BLE001 - one tier must not kill the session
                logger.exception("%s %s failed", key, tier)
                entry = _failed_entry(exc)

            manifest.mark(key, tier, entry, **mark_kw)
            outcome.bytes_written += entry.bytes
            outcome.per_tier[tier] = "cancelled" if entry.status == "pending" else entry.status
            if entry.absent_streams:
                outcome.warnings.append(f"{key} {tier}: absent {', '.join(entry.absent_streams)}")
    finally:
        # Drop the parsed streams (hundreds of MB for a race) before the next session.
        del store
    return outcome
