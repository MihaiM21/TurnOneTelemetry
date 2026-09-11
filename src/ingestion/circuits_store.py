"""Write-side of the stored circuit layouts (the single on-disk writer).

Before this module, ``circuits_sync`` was the only thing that wrote a circuit
file, and it could only *append*: it never deleted a stale file for the same
circuit id and never replaced a manifest entry. That was fine while every
layout came from multiviewer. Telemetry-derived layouts
(:mod:`src.services.circuit_derivation`) are provisional and must be replaced
when multiviewer publishes the real circuit, so writes now go through
:func:`save_circuit_layout`, which:

* deletes any other ``{circuit_id}_*.json`` for that id first -- the loader
  returns the first ``os.listdir`` hit, so two files for one id (different
  slugs) would be resolved in filesystem order;
* replaces (not skips) the manifest entry with the same ``circuit_id``;
* mirrors the layout to MongoDB (``circuit_layouts``) so a Docker rebuild --
  which does not preserve ``src/`` -- can re-materialise it at startup via
  :func:`hydrate_from_mongo`.

Paths are resolved through ``circuits_loader.CIRCUITS_DIR`` at call time so a
single monkeypatch redirects loader, store and sync together in tests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from src.core.logging import get_logger
from src.domain.models.circuits import CircuitLayout, CircuitSummary
from src.ingestion import circuits_loader
from src.workers.organize_circuits import slugify

logger = get_logger(__name__)

DEFAULT_SOURCE = "multiviewer"


def year_dir(year: int) -> Path:
    return Path(circuits_loader.CIRCUITS_DIR) / str(int(year))


def _is_layout_file(filename: str) -> bool:
    return filename.endswith(".json") and filename != circuits_loader.MANIFEST_NAME


def _split_filename(filename: str) -> Tuple[str, str]:
    """``153_madring.json`` -> ``("153", "madring")``."""
    stem = filename[:-len(".json")]
    circuit_id, _, slug = stem.partition("_")
    return circuit_id, slug


def list_circuit_files(year: int) -> Dict[str, Dict[str, Any]]:
    """Every stored layout for ``year`` keyed by circuit id.

    Each value is ``{"path", "slug", "source", "name"}``. ``source`` is read
    from the file (``"multiviewer"`` when the field is absent -- every file
    written before the field existed came from multiviewer). A file that fails
    to parse is reported with ``source="invalid"`` rather than hidden, so the
    admin status page can surface it.
    """
    directory = year_dir(year)
    if not directory.is_dir():
        return {}

    result: Dict[str, Dict[str, Any]] = {}
    for filename in sorted(os.listdir(directory)):
        if not _is_layout_file(filename):
            continue
        circuit_id, slug = _split_filename(filename)
        path = directory / filename
        source, name = DEFAULT_SOURCE, ""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if isinstance(payload, dict):
                source = payload.get("source") or DEFAULT_SOURCE
                name = payload.get("name") or ""
        except (OSError, ValueError):
            logger.warning("circuits_store: could not parse %s", path, exc_info=True)
            source = "invalid"
        # First file wins, matching the loader's os.listdir-order behaviour.
        result.setdefault(circuit_id, {"path": path, "slug": slug, "source": source, "name": name})
    return result


def find_circuit_file(year: int, circuit_id: Any) -> Optional[Path]:
    """Path of the stored layout for ``circuit_id``/``year``, or ``None``."""
    entry = list_circuit_files(year).get(str(circuit_id))
    return entry["path"] if entry else None


def read_source(path: Path) -> str:
    """The ``source`` recorded in a layout file (``"multiviewer"`` if absent)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return (payload.get("source") if isinstance(payload, dict) else None) or DEFAULT_SOURCE
    except (OSError, ValueError):
        return "invalid"


def _write_json_atomic(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def upsert_manifest_entry(year: int, summary: CircuitSummary) -> None:
    """Insert or *replace* the ``all_circuits.json`` entry for ``summary``.

    ``years_available`` is merged with whatever the previous entry claimed so
    replacing a derived layout with multiviewer's never loses years.
    """
    directory = year_dir(year)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / circuits_loader.MANIFEST_NAME

    data: Dict[str, Any] = {"year": int(year), "total_circuits": 0, "circuits": []}
    if manifest.exists():
        with open(manifest, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict) and isinstance(loaded.get("circuits"), list):
            data = loaded

    entry = summary.model_dump()
    kept = []
    for existing in data["circuits"]:
        if str(existing.get("circuit_id")) == str(summary.circuit_id):
            previous_years = existing.get("years_available") or []
            entry["years_available"] = sorted(set(previous_years) | set(entry.get("years_available") or []))
            continue
        kept.append(existing)
    kept.append(entry)

    data["circuits"] = kept
    data["total_circuits"] = len(kept)
    _write_json_atomic(manifest, data)


def save_circuit_layout(
    layout: CircuitLayout,
    summary: CircuitSummary,
    *,
    mirror_to_mongo: bool = True,
) -> Path:
    """Persist a layout: file, manifest entry and (optionally) the Mongo mirror.

    Returns the path written. Any stale ``{circuit_id}_*.json`` for the same id
    is removed first so the loader can never pick the wrong one.
    """
    directory = year_dir(layout.year)
    directory.mkdir(parents=True, exist_ok=True)

    target = directory / f"{layout.circuit_id}_{slugify(layout.name or 'circuit')}.json"
    for filename in os.listdir(directory):
        if not _is_layout_file(filename):
            continue
        circuit_id, _ = _split_filename(filename)
        stale = directory / filename
        if circuit_id == str(layout.circuit_id) and stale != target:
            stale.unlink()
            logger.info("circuits_store: removed stale layout %s", stale.name)

    _write_json_atomic(target, layout.model_dump())
    upsert_manifest_entry(layout.year, summary)
    logger.info("circuits_store: wrote %s (source=%s)", target, layout.source)

    if mirror_to_mongo:
        # Imported lazily: the loader/store must stay importable without a
        # Mongo client (organize_circuits.py is a plain CLI script).
        from src.repositories import circuit_layouts

        if not circuit_layouts.store_layout(layout.model_dump(), summary.model_dump()):
            logger.warning("circuits_store: Mongo mirror skipped for %s", target.name)

    return target


def hydrate_from_mongo(docs: Optional[Iterable[Dict[str, Any]]] = None) -> int:
    """Re-create on disk every Mongo-mirrored layout that is missing locally.

    Files already present are left untouched (disk is the source of truth
    while it exists). Returns the number of files written. Never raises.
    """
    if docs is None:
        try:
            from src.repositories import circuit_layouts

            docs = circuit_layouts.iter_layouts()
        except Exception:
            logger.warning("circuits_store: hydrate skipped, Mongo unavailable", exc_info=True)
            return 0

    written = 0
    for doc in docs:
        try:
            year = int(doc["year"])
            circuit_id = str(doc["circuit_id"])
            if find_circuit_file(year, circuit_id) is not None:
                continue
            layout = CircuitLayout(**doc["layout"])
            summary = CircuitSummary(**doc["summary"])
            save_circuit_layout(layout, summary, mirror_to_mongo=False)
            written += 1
        except Exception:
            logger.warning("circuits_store: could not hydrate %s", doc.get("_id"), exc_info=True)
    return written
