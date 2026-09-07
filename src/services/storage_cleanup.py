"""Scoped, preview-first cleanup of everything T1API stores.

A generated feature leaves traces in **five** places, and until now only three
of them could be deleted at all — one item at a time:

===============  =========================================  ==================
Layer            Addressed by                               Previously
===============  =========================================  ==================
``mongo``        ``{year}_processed_data[_v2]`` documents,   one ``data_type``
                 nested ``sessions[].data[]`` entries        at a time
``raw_streams``  GridFS ``v2_raw_cache``,                    one session
                 ``{year}_{round}_{session}:{stream}``
``bundles``      ``v2_session_cache``,                       one bundle
                 ``_id = {year}_{round}_{session}``
``redis``        ``t1api:v2:*``                              never
``plots``        ``outputs/plots/{year}/{event}/{session}``  **never**
===============  =========================================  ==================

The filesystem layer had no delete path whatsoever, so every PNG ever rendered
accumulated forever.

Two properties this module is built around:

**Preview first.** ``plan_cleanup`` enumerates *real stored items* and filters
them; it never constructs keys speculatively. That matters because the layers
disagree about how a session is addressed — Mongo uses a country-code ``gp_id``
(``2025_ITA``), the caches use a round number (``2025_16_R``), and the
filesystem uses the event name (``2025/Italian Grand Prix/R``). Guessing keys
across that drift would silently miss or, worse, silently over-match. Filtering
concrete inventory cannot.

**Purge is token-gated.** ``plan_cleanup`` returns a ``confirm_token`` derived
from the exact item set. ``execute_cleanup`` re-plans and refuses if the token
no longer matches, so a purge cannot delete something the operator never saw in
the preview — the same double-confirmation shape the backup restore uses.

Redis is deliberately excluded from the token: it is a pure cache whose
contents shift under TTL constantly, so a stable token is neither achievable
nor meaningful there.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.core.logging import get_logger
from src.repositories import raw_stream_cache, session_cache
from src.repositories.mongo import MongoDBManager
from src.services.plotting.output import UnsafeOutputPath, plots_root, resolve_within

logger = get_logger(__name__)

LAYER_MONGO = "mongo"
LAYER_RAW = "raw_streams"
LAYER_BUNDLES = "bundles"
LAYER_REDIS = "redis"
LAYER_PLOTS = "plots"

#: Every layer, in the order a full purge should process them: durable records
#: first, derived caches last, so an interrupted run degrades toward "cache is
#: cold" rather than "payload is gone but the cache still serves it".
ALL_LAYERS: Tuple[str, ...] = (
    LAYER_MONGO,
    LAYER_RAW,
    LAYER_BUNDLES,
    LAYER_PLOTS,
    LAYER_REDIS,
)

#: Redis namespaces this module is permitted to touch. ``t1api:auth:*`` holds
#: the API-key resolution cache -- purging it would sign every caller out mid
#: request, and it is not "data" in any sense the operator means here. The
#: guard is asserted at delete time, not merely documented.
_REDIS_SAFE_PREFIX = "t1api:v2:"

_SESSION_ALIASES = {
    "RACE": "R",
    "SPRINT": "S",
    "SPRINTRACE": "S",
    "QUALIFYING": "Q",
    "QUALI": "Q",
    "SPRINTQUALIFYING": "SQ",
    "SPRINTSHOOTOUT": "SQ",
    "PRACTICE1": "FP1",
    "PRACTICE2": "FP2",
    "PRACTICE3": "FP3",
}


class CleanupError(RuntimeError):
    """Raised when a purge is refused (bad token, or unscoped without opt-in)."""


def normalize_session(value: Any) -> str:
    """Fold the several spellings of a session type onto one token.

    ``outputs/plots`` alone contains both ``Race`` (2023 vintage) and ``R``
    (2025), so a scope of ``session=R`` has to match both or the older tree is
    invisible to the cleanup.
    """
    token = str(value or "").strip().upper().replace(" ", "").replace("_", "")
    return _SESSION_ALIASES.get(token, token)


def _fold(value: Any) -> str:
    """Case/space/punctuation-insensitive form used for GP matching."""
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


@dataclass(frozen=True)
class CleanupScope:
    """What to delete. Every field is a filter; ``None`` means "no constraint"."""

    year: Optional[int] = None
    gp: Optional[str] = None
    session: Optional[str] = None
    data_type: Optional[str] = None
    layers: Tuple[str, ...] = ALL_LAYERS

    def __post_init__(self) -> None:
        unknown = [layer for layer in self.layers if layer not in ALL_LAYERS]
        if unknown:
            raise ValueError(f"Unknown storage layer(s): {sorted(unknown)}")
        if not self.layers:
            raise ValueError("At least one storage layer must be selected")

    @property
    def is_unscoped(self) -> bool:
        """True when nothing narrows the scope -- i.e. "delete everything"."""
        return self.year is None and self.gp is None and self.session is None and self.data_type is None

    def matches_year(self, year: Any) -> bool:
        if self.year is None:
            return True
        try:
            return int(year) == int(self.year)
        except (TypeError, ValueError):
            return False

    def matches_gp(self, *candidates: Any) -> bool:
        """Match against every identifier a layer might know the GP by.

        Callers pass whatever they have -- ``gp_id``, round number, event name --
        and any one of them matching is enough, because no single layer knows
        all three.
        """
        if self.gp is None:
            return True
        target = _fold(self.gp)
        if not target:
            return True
        for candidate in candidates:
            if candidate is None:
                continue
            folded = _fold(candidate)
            if not folded:
                continue
            if folded == target or target in folded:
                return True
        return False

    def matches_session(self, session: Any) -> bool:
        if self.session is None:
            return True
        return normalize_session(session) == normalize_session(self.session)

    def matches_data_type(self, data_type: Any) -> bool:
        if self.data_type is None:
            return True
        return _fold(data_type) == _fold(self.data_type)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "year": self.year,
            "gp": self.gp,
            "session": self.session,
            "data_type": self.data_type,
            "layers": list(self.layers),
        }


@dataclass
class CleanupItem:
    """One deletable thing, as it exists in storage right now."""

    layer: str
    key: str
    label: str
    bytes: int = 0
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer": self.layer,
            "key": self.key,
            "label": self.label,
            "bytes": self.bytes,
            "detail": self.detail,
        }


# --------------------------------------------------------------------------- #
# Per-layer enumeration (read-only; never deletes)
# --------------------------------------------------------------------------- #

def _years_in_scope(scope: CleanupScope) -> List[int]:
    """Years to scan. An unscoped plan discovers them from the plot tree and
    the Mongo collection names rather than assuming a range."""
    if scope.year is not None:
        return [int(scope.year)]
    years: set = set()
    root = plots_root()
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and child.name.isdigit():
                years.add(int(child.name))
    try:
        manager = MongoDBManager(version="v2")
        for name in manager.db.list_collection_names():
            head = name.split("_", 1)[0]
            if head.isdigit():
                years.add(int(head))
    except Exception as exc:
        logger.warning("Could not enumerate Mongo collections for year discovery: %s", exc)
    return sorted(years)


def _enumerate_mongo(scope: CleanupScope) -> List[CleanupItem]:
    """Stored ``data_type`` entries, one item per entry.

    Both the v1 and v2 collections are walked: a cleanup that silently skipped
    ``{year}_processed_data`` would leave the deprecated-but-still-serving V1
    payloads behind, which is exactly the storage an operator is trying to
    reclaim.
    """
    items: List[CleanupItem] = []
    for year in _years_in_scope(scope):
        for version in ("v2", "v1"):
            try:
                manager = MongoDBManager(year=year, version=version)
                rows = manager.summarize_stored_data(year)
            except Exception as exc:
                logger.warning("Mongo enumeration failed for %s %s: %s", year, version, exc)
                continue
            for row in rows:
                gp_id = row.get("gp_id")
                if not scope.matches_gp(gp_id, row.get("round_nr"), row.get("event_name")):
                    continue
                session_type = row.get("session_type")
                if not scope.matches_session(session_type):
                    continue
                for data_type in row.get("data_types") or []:
                    if not scope.matches_data_type(data_type):
                        continue
                    items.append(CleanupItem(
                        layer=LAYER_MONGO,
                        key=f"{version}:{year}:{gp_id}:{session_type}:{data_type}",
                        label=f"{gp_id} {session_type} - {data_type} ({version})",
                        detail={
                            "year": year, "version": version, "gp_id": gp_id,
                            "session_type": session_type, "data_type": data_type,
                            "event_name": row.get("event_name"),
                        },
                    ))
    return items


def _enumerate_raw(scope: CleanupScope) -> List[CleanupItem]:
    """GridFS raw livetiming blobs, one item per session."""
    if scope.data_type is not None:
        # Raw streams are per-session, not per-feature: a data_type-scoped
        # cleanup must not take them out from under every other feature of
        # that session.
        return []
    items: List[CleanupItem] = []
    try:
        sessions = raw_stream_cache.list_raw_stream_sessions(scope.year)
    except Exception as exc:
        logger.warning("Raw-stream enumeration failed: %s", exc)
        return []
    for row in sessions:
        key = str(row.get("session_key") or "")
        parts = key.split("_")
        year = parts[0] if parts else None
        round_nr = parts[1] if len(parts) > 1 else None
        session = parts[2] if len(parts) > 2 else None
        if not scope.matches_year(year):
            continue
        if not scope.matches_gp(round_nr, row.get("event_name")):
            continue
        if not scope.matches_session(session):
            continue
        items.append(CleanupItem(
            layer=LAYER_RAW,
            key=key,
            label=f"{key} - {row.get('streams', 0)} stream(s)",
            bytes=int(row.get("bytes") or 0),
            detail={"session_key": key, "streams": row.get("streams"),
                    "schema_drift": row.get("schema_drift")},
        ))
    return items


def _enumerate_bundles(scope: CleanupScope) -> List[CleanupItem]:
    """Derived per-session bundles, one item per bundle."""
    if scope.data_type is not None:
        return []
    items: List[CleanupItem] = []
    try:
        bundles = session_cache.list_bundles(scope.year)
    except Exception as exc:
        logger.warning("Bundle enumeration failed: %s", exc)
        return []
    for row in bundles:
        if not scope.matches_year(row.get("year")):
            continue
        if not scope.matches_gp(row.get("round_nr"), row.get("event_name")):
            continue
        if not scope.matches_session(row.get("session")):
            continue
        doc_id = str(row.get("doc_id"))
        items.append(CleanupItem(
            layer=LAYER_BUNDLES,
            key=doc_id,
            label=f"{doc_id} - {len(row.get('keys') or [])} key(s)",
            detail={"doc_id": doc_id, "keys": row.get("keys"),
                    "schema_drift": row.get("schema_drift")},
        ))
    return items


def _plot_matches_data_type(filename: str, data_type: str) -> bool:
    """Loose match of a ``data_type`` against a rendered plot's filename.

    Plot filenames are human titles ("Speed Distribution 2023 ... VER.png"),
    not ``data_type`` keys, so this folds both to alphanumerics and accepts
    either a substring hit or a word-level overlap. Deliberately generous: the
    preview shows every match before anything is deleted, so a false positive
    is visible and correctable, whereas a false negative silently leaves files
    behind and the operator believes the scope is clean.
    """
    folded_name = _fold(filename)
    folded_type = _fold(data_type)
    if not folded_type:
        return True
    if folded_type in folded_name:
        return True
    words = [w for w in str(data_type).replace("-", "_").split("_") if len(w) > 2]
    return bool(words) and all(_fold(word) in folded_name for word in words)


def _enumerate_plots(scope: CleanupScope) -> List[CleanupItem]:
    """Rendered PNGs under ``outputs/plots/{year}/{event}/{session}``.

    One item per *file* rather than per directory, so a ``data_type``-scoped
    cleanup can match on the filename -- which is the only place the plot layer
    records which feature produced it.
    """
    items: List[CleanupItem] = []
    root = plots_root()
    if not root.is_dir():
        return items
    for year_dir in sorted(root.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        if not scope.matches_year(year_dir.name):
            continue
        for event_dir in sorted(year_dir.iterdir()):
            if not event_dir.is_dir():
                continue
            if not scope.matches_gp(event_dir.name):
                continue
            for session_dir in sorted(event_dir.iterdir()):
                if not session_dir.is_dir():
                    continue
                if not scope.matches_session(session_dir.name):
                    continue
                for path in sorted(session_dir.rglob("*")):
                    if not path.is_file():
                        continue
                    if scope.data_type is not None and not _plot_matches_data_type(
                        path.name, scope.data_type
                    ):
                        continue
                    try:
                        size = path.stat().st_size
                    except OSError:
                        size = 0
                    rel = str(path.relative_to(root)).replace("\\", "/")
                    items.append(CleanupItem(
                        layer=LAYER_PLOTS,
                        key=rel,
                        label=rel,
                        bytes=size,
                        detail={"year": year_dir.name, "event": event_dir.name,
                                "session": session_dir.name, "filename": path.name},
                    ))
    return items


def _redis_patterns(scope: CleanupScope) -> List[str]:
    """Glob patterns covering the scope. Always under ``t1api:v2:``.

    Redis holds no per-``data_type`` season/plot distinction fine enough to
    narrow further without a SCAN of every key, so a data_type-scoped cleanup
    simply drops the whole scoped plot namespace -- it is a cache, and the
    cost of over-purging is one recomputation.
    """
    year = scope.year if scope.year is not None else "*"
    return [
        f"{_REDIS_SAFE_PREFIX}plot:{year}:*",
        f"{_REDIS_SAFE_PREFIX}stream:{year}:*",
        f"{_REDIS_SAFE_PREFIX}event:{year}:*",
        f"{_REDIS_SAFE_PREFIX}season_events:{year}",
    ]


_ENUMERATORS = {
    LAYER_MONGO: _enumerate_mongo,
    LAYER_RAW: _enumerate_raw,
    LAYER_BUNDLES: _enumerate_bundles,
    LAYER_PLOTS: _enumerate_plots,
}


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #

def _confirm_token(scope: CleanupScope, items: Sequence[CleanupItem]) -> str:
    """Fingerprint the exact item set a preview showed.

    ``execute_cleanup`` recomputes this and refuses to proceed on a mismatch,
    so anything that appeared in storage between preview and purge blocks the
    run instead of being deleted unseen. Redis is excluded (see module
    docstring) -- its contents shift under TTL, which would invalidate every
    token within seconds.
    """
    payload = json.dumps(
        {
            "scope": {k: v for k, v in scope.to_dict().items() if k != "layers"},
            "items": sorted(f"{item.layer}|{item.key}" for item in items if item.layer != LAYER_REDIS),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def plan_cleanup(scope: CleanupScope) -> Dict[str, Any]:
    """Enumerate everything ``scope`` would delete. Never mutates anything."""
    items: List[CleanupItem] = []
    for layer in scope.layers:
        enumerator = _ENUMERATORS.get(layer)
        if enumerator is None:
            continue
        items.extend(enumerator(scope))

    by_layer: Dict[str, Dict[str, Any]] = {}
    for layer in scope.layers:
        layer_items = [i for i in items if i.layer == layer]
        by_layer[layer] = {
            "count": len(layer_items),
            "bytes": sum(i.bytes for i in layer_items),
            "items": [i.to_dict() for i in layer_items],
        }

    if LAYER_REDIS in scope.layers:
        patterns = _redis_patterns(scope)
        by_layer[LAYER_REDIS] = {
            "count": _count_redis_keys(patterns),
            "bytes": 0,
            "patterns": patterns,
            "items": [],
        }

    return {
        "scope": scope.to_dict(),
        "unscoped": scope.is_unscoped,
        "layers": by_layer,
        "total_items": len(items),
        "total_bytes": sum(i.bytes for i in items),
        "confirm_token": _confirm_token(scope, items),
    }


def _count_redis_keys(patterns: Sequence[str]) -> int:
    """Best-effort count for the preview. Redis being down reports 0, not an
    error: it is a cache, and its absence must not block a durable purge."""
    try:
        from src.core.cache.redis_cache import get_sync_cache

        cache = get_sync_cache()
        if not getattr(cache, "enabled", False):
            return 0
        return sum(cache.count_pattern(p) for p in patterns)
    except Exception as exc:
        logger.debug("Redis key count unavailable: %s", exc)
        return 0


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #

def _delete_mongo(items: Sequence[CleanupItem]) -> Tuple[int, List[str]]:
    deleted, errors = 0, []
    managers: Dict[Tuple[int, str], MongoDBManager] = {}
    for item in items:
        detail = item.detail
        year, version = int(detail["year"]), str(detail["version"])
        try:
            manager = managers.get((year, version))
            if manager is None:
                manager = MongoDBManager(year=year, version=version)
                managers[(year, version)] = manager
            ok = manager.delete_session_data(
                detail["gp_id"], detail["session_type"], detail["data_type"], year
            )
            if ok:
                deleted += 1
            else:
                errors.append(f"{item.key}: not found")
        except Exception as exc:
            logger.exception("Failed deleting Mongo entry %s", item.key)
            errors.append(f"{item.key}: {exc}")
    return deleted, errors


def _delete_raw(items: Sequence[CleanupItem]) -> Tuple[int, List[str]]:
    deleted, errors = 0, []
    for item in items:
        try:
            deleted += int(raw_stream_cache.delete_raw_streams(item.key) or 0)
        except Exception as exc:
            logger.exception("Failed deleting raw streams %s", item.key)
            errors.append(f"{item.key}: {exc}")
    return deleted, errors


def _delete_bundles(items: Sequence[CleanupItem]) -> Tuple[int, List[str]]:
    deleted, errors = 0, []
    for item in items:
        try:
            if session_cache.delete_bundle(item.key):
                deleted += 1
            else:
                errors.append(f"{item.key}: not found")
        except Exception as exc:
            logger.exception("Failed deleting bundle %s", item.key)
            errors.append(f"{item.key}: {exc}")
    return deleted, errors


def _delete_plots(items: Sequence[CleanupItem]) -> Tuple[int, List[str]]:
    """Delete rendered files, then prune directories the deletion emptied.

    Every path is re-resolved through ``resolve_within`` rather than trusted
    from the plan: the plan is JSON that round-trips through an HTTP request,
    so a crafted ``key`` would otherwise reach ``unlink`` directly. Same sink
    guard the write path uses.
    """
    deleted, errors = 0, []
    root = plots_root()
    touched_dirs: set = set()
    for item in items:
        try:
            path = resolve_within(item.key, root)
        except UnsafeOutputPath as exc:
            logger.warning("Refusing to delete outside plots root: %s", item.key)
            errors.append(f"{item.key}: {exc}")
            continue
        try:
            if path.is_file():
                touched_dirs.add(path.parent)
                path.unlink()
                deleted += 1
            else:
                errors.append(f"{item.key}: not found")
        except OSError as exc:
            logger.warning("Failed deleting plot %s: %s", item.key, exc)
            errors.append(f"{item.key}: {exc}")

    # Prune now-empty session/event/year directories, deepest first, so a
    # cleared scope does not leave an empty skeleton behind.
    for directory in sorted(touched_dirs, key=lambda p: len(p.parts), reverse=True):
        current = directory
        while current != root and root in current.parents:
            try:
                if any(current.iterdir()):
                    break
                current.rmdir()
            except OSError:
                break
            current = current.parent
    return deleted, errors


def _delete_redis(patterns: Sequence[str]) -> Tuple[int, List[str]]:
    """Purge scoped cache keys. Refuses any pattern outside ``t1api:v2:``."""
    deleted, errors = 0, []
    for pattern in patterns:
        if not pattern.startswith(_REDIS_SAFE_PREFIX):
            # Never reachable from _redis_patterns; asserted anyway because the
            # adjacent t1api:auth:* namespace holds live API-key resolutions.
            errors.append(f"{pattern}: refused (outside {_REDIS_SAFE_PREFIX})")
            continue
        try:
            from src.core.cache.redis_cache import get_sync_cache

            deleted += int(get_sync_cache().delete_pattern(pattern) or 0)
        except Exception as exc:
            logger.warning("Redis purge failed for %s: %s", pattern, exc)
            errors.append(f"{pattern}: {exc}")
    return deleted, errors


def execute_cleanup(
    scope: CleanupScope,
    confirm_token: str,
    *,
    allow_full_purge: bool = False,
) -> Dict[str, Any]:
    """Delete everything in ``scope``, but only what a preview already showed.

    Raises :class:`CleanupError` when the token does not match the current
    contents of storage, or when an unscoped ("delete everything") purge is
    requested without ``allow_full_purge``.
    """
    if scope.is_unscoped and not allow_full_purge:
        raise CleanupError(
            "Refusing an unscoped purge: pass allow_full_purge to delete every "
            "stored year, GP, session and data type."
        )

    plan = plan_cleanup(scope)
    if plan["confirm_token"] != confirm_token:
        raise CleanupError(
            "Stored data changed since the preview was generated. Re-run the "
            "preview and confirm the new plan."
        )

    results: Dict[str, Any] = {}
    all_errors: List[str] = []

    for layer in scope.layers:
        if layer == LAYER_REDIS:
            continue
        items = [
            CleanupItem(layer=d["layer"], key=d["key"], label=d["label"],
                        bytes=d["bytes"], detail=d["detail"])
            for d in plan["layers"].get(layer, {}).get("items", [])
        ]
        if layer == LAYER_MONGO:
            deleted, errors = _delete_mongo(items)
        elif layer == LAYER_RAW:
            deleted, errors = _delete_raw(items)
        elif layer == LAYER_BUNDLES:
            deleted, errors = _delete_bundles(items)
        elif layer == LAYER_PLOTS:
            deleted, errors = _delete_plots(items)
        else:
            continue
        results[layer] = {
            "requested": len(items),
            "deleted": deleted,
            "bytes": plan["layers"].get(layer, {}).get("bytes", 0),
            "errors": errors,
        }
        all_errors.extend(errors)

    # Redis last: the derived caches must not be able to re-serve a payload
    # whose durable record has just been removed.
    if LAYER_REDIS in scope.layers:
        patterns = plan["layers"][LAYER_REDIS]["patterns"]
        deleted, errors = _delete_redis(patterns)
        results[LAYER_REDIS] = {
            "requested": plan["layers"][LAYER_REDIS]["count"],
            "deleted": deleted,
            "bytes": 0,
            "patterns": patterns,
            "errors": errors,
        }
        all_errors.extend(errors)

    total_deleted = sum(r["deleted"] for r in results.values())
    logger.info(
        "Storage cleanup executed: scope=%s deleted=%s errors=%s",
        scope.to_dict(), total_deleted, len(all_errors),
    )
    return {
        "scope": scope.to_dict(),
        "layers": results,
        "total_deleted": total_deleted,
        "bytes_reclaimed": sum(r.get("bytes", 0) for r in results.values()),
        "errors": all_errors,
        "ok": not all_errors,
    }


# --------------------------------------------------------------------------- #
# Orphan detection
# --------------------------------------------------------------------------- #

def find_orphan_plot_dirs() -> List[Dict[str, Any]]:
    """GP directories that duplicate each other under two naming conventions.

    ``outputs/plots`` accumulated both spaced and unspaced event names
    (``2025/Abu Dhabi Grand Prix`` alongside ``2025/AbuDhabiGrandPrix``) and
    both long and short session names (``Race`` vs ``R``), because the writer
    changed convention without a migration. Nothing reads the stale spelling,
    so those trees are pure dead weight -- but only an operator can say which
    spelling is canonical, so this reports and never deletes.
    """
    root = plots_root()
    if not root.is_dir():
        return []
    groups: Dict[Tuple[str, str], List[Path]] = {}
    for year_dir in sorted(root.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for event_dir in sorted(year_dir.iterdir()):
            if event_dir.is_dir():
                groups.setdefault((year_dir.name, _fold(event_dir.name)), []).append(event_dir)

    orphans: List[Dict[str, Any]] = []
    for (year, _folded), paths in sorted(groups.items()):
        if len(paths) < 2:
            continue
        variants = []
        for path in paths:
            files = [p for p in path.rglob("*") if p.is_file()]
            variants.append({
                "path": str(path.relative_to(root)).replace("\\", "/"),
                "name": path.name,
                "files": len(files),
                "bytes": sum(_safe_size(p) for p in files),
            })
        variants.sort(key=lambda v: (v["files"], v["bytes"]), reverse=True)
        orphans.append({
            "year": int(year),
            "variants": variants,
            "suggested_keep": variants[0]["path"],
            "reclaimable_bytes": sum(v["bytes"] for v in variants[1:]),
        })
    return orphans


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def storage_totals() -> Dict[str, Any]:
    """Headline "what is this costing me" figures for the cleanup dashboard."""
    root = plots_root()
    plot_files, plot_bytes = 0, 0
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file():
                plot_files += 1
                plot_bytes += _safe_size(path)
    try:
        raw_totals = raw_stream_cache.raw_cache_totals()
    except Exception as exc:
        logger.warning("raw_cache_totals failed: %s", exc)
        raw_totals = {"files": 0, "bytes": 0}
    try:
        bundle_stats = session_cache.bundle_totals()
    except Exception as exc:
        logger.warning("bundle_totals failed: %s", exc)
        bundle_stats = {"bundles": 0, "schema_drift": 0}

    orphans = find_orphan_plot_dirs()
    return {
        "plots": {"files": plot_files, "bytes": plot_bytes,
                  "root": str(root).replace("\\", "/")},
        "raw_streams": {"files": raw_totals.get("files", 0),
                        "bytes": raw_totals.get("bytes", 0)},
        "bundles": {"count": bundle_stats.get("bundles", 0),
                    "schema_drift": bundle_stats.get("schema_drift", 0)},
        "orphan_plot_groups": len(orphans),
        "orphan_reclaimable_bytes": sum(o["reclaimable_bytes"] for o in orphans),
    }
