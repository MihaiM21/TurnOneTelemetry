"""Admin plot inventory + backfill for V2 features.

Three capabilities, all driven by the canonical catalog in
``src/services/analysis/v2/registry.py`` so "what exists", "what would run" and
"what actually ran" can never drift:

* :func:`compute_inventory` — a **schedule-driven** report of which V2
  ``data_type`` keys are still ungenerated in MongoDB, scoped per year / round
  (GP) / session. Because it enumerates the official schedule
  (``get_season_events``) rather than only reading Mongo, it also flags
  entirely-absent sessions and GPs (which a pure Mongo scan cannot). It also
  reports **extra** stored keys — per-driver/pair/lap documents and legacy
  orphans that the singleton expectation set says nothing about.

* :func:`build_plan` / :func:`estimate_plan` — expand a scope plus a feature
  **selection** into concrete work units, and cost that expansion *before*
  anything runs. A full-catalog backfill of one race is thousands of units, so
  an operator needs the number up front rather than discovering it from a
  progress bar.

* :func:`execute_plan` — run the units, skipping ones already stored unless
  ``force``, honouring cancellation, and reporting progress.

Jobs are tracked both in memory (authoritative, cheap) and in MongoDB via
``src/repositories/admin_jobs.py`` (durable, cross-process). Progress is flushed
to Mongo on a throttle — see :class:`_JobWriter` — because a thousands-of-units
job must not cost a Mongo write per unit.

Concurrency note: units are IO-bound (livetiming fetches), but a
``SessionDataStore``'s stream cache is per-instance and per-thread here. So the
executor parallelizes **across** sessions and serializes **within** a session;
anything else would re-download the same multi-megabyte streams per unit.
"""

from __future__ import annotations

import threading
import time
import unicodedata
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from src.core.logging import get_logger
from src.ingestion.event_resolver import resolve_event
from src.ingestion.reference import get_season_events
from src.repositories import admin_jobs
from src.repositories.mongo import MongoDBManager
from src.services.analysis.v2.registry import (
    KIND_CAREER,
    KIND_PER_DRIVER,
    KIND_PER_DRIVER_LAP,
    KIND_PER_PAIR,
    KIND_SEASON,
    KIND_SINGLETON,
    V2_CAREER_PLOTS,
    V2_PAIR_PLOTS,
    V2_PER_DRIVER_LAP_PLOTS,
    V2_PER_DRIVER_PLOTS,
    V2_SEASON_PLOTS,
    feature_by_key,
    persist_generated,
    persist_season_generated,
    session_driver_laps,
    session_drivers,
    specs_for_session,
)
from src.services.orchestrator_helpers import simplify_session_name

logger = get_logger(__name__)

Identifier = Union[int, str]

# A single unit of work: one session of one GP.
Target = Tuple[int, int, str, str]  # (year, round_nr, gp_name, session_abbrev)

JOB_KIND = "plot_backfill"

# Refuse to build a plan larger than this. A full-catalog whole-season backfill
# with per-lap telemetry runs to hundreds of thousands of units, which is a
# mis-click rather than an intent. The admin can narrow the scope or raise the
# cap explicitly.
MAX_PLAN_UNITS = 50_000

# Progress is flushed to Mongo at most this often, or every N completed units,
# whichever comes first.
_FLUSH_INTERVAL_SECONDS = 2.0
_FLUSH_EVERY_UNITS = 25

MAX_CONCURRENCY = 4


# --------------------------------------------------------------------------- #
# Year / schedule enumeration
# --------------------------------------------------------------------------- #
def _years_with_v2_data() -> List[int]:
    """Years that have a ``{year}_processed_data_v2`` collection."""
    years: List[int] = []
    try:
        db = MongoDBManager(version="v2").db
        for name in db.list_collection_names():
            if name.endswith("_processed_data_v2"):
                prefix = name.split("_", 1)[0]
                if prefix.isdigit():
                    years.append(int(prefix))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not list v2 collections: %s", exc)
    return sorted(set(years))


def _resolve_years(year: Optional[int]) -> List[int]:
    if year is not None:
        return [year]
    years = _years_with_v2_data()
    return years or [datetime.now(timezone.utc).year]


def available_years() -> List[int]:
    """Years offered in the admin UI picker: v2-data years + current/previous."""
    now = datetime.now(timezone.utc).year
    return sorted(set(_years_with_v2_data()) | {now, now - 1}, reverse=True)


def available_events(year: int) -> List[Dict[str, Any]]:
    """GP events for a year as ``[{round_nr, name}]`` for the admin picker.

    Sourced from the curated schedule, which numbers real Grands Prix 1..N and
    never includes pre-season testing — so selecting by ``name`` here cannot be
    confused with a testing meeting. ``round_nr`` matches what the backfill uses.
    """
    try:
        events = get_season_events(year)
    except Exception as exc:
        logger.warning("No schedule for %s: %s", year, exc)
        return []
    out: List[Dict[str, Any]] = []
    for idx, race in enumerate(events):
        round_nr = race.get("round") if isinstance(race.get("round"), int) else idx + 1
        name = race.get("grandPrix") or race.get("name") or ""
        if name:
            out.append({"round_nr": round_nr, "name": name})
    return out


def _enumerate_targets(
    *,
    year: Optional[int],
    identifier: Optional[Identifier],
    session: Optional[str],
) -> List[Target]:
    """Expand a scope into concrete (year, round_nr, gp_name, session) units.

    Enumeration comes from the official schedule so absent sessions/GPs are
    still surfaced. ``session`` may be an abbreviation (``R``) or full name
    (``Race``); it is normalized before filtering.
    """
    session_filter = simplify_session_name(session).strip().upper() if session else None
    targets: List[Target] = []

    for yr in _resolve_years(year):
        try:
            events = get_season_events(yr)
        except Exception as exc:
            logger.warning("No schedule for %s: %s", yr, exc)
            continue

        # Optional single-GP scoping.
        wanted_round: Optional[int] = None
        if identifier is not None:
            try:
                wanted_round = resolve_event(yr, identifier).round_nr
            except Exception as exc:
                logger.warning("Could not resolve GP %r in %s: %s", identifier, yr, exc)
                continue

        for idx, race in enumerate(events):
            round_nr = race.get("round") if isinstance(race.get("round"), int) else idx + 1
            if wanted_round is not None and round_nr != wanted_round:
                continue
            gp_name = race.get("grandPrix") or race.get("name") or ""
            for sess in race.get("sessions", []):
                abbrev = simplify_session_name(sess.get("name", "")).strip().upper()
                if session_filter and abbrev != session_filter:
                    continue
                targets.append((yr, round_nr, gp_name, abbrev))
    return targets


def _norm_name(name: Optional[str]) -> str:
    """Fold an event name to a stable key: diacritics stripped, alnum only.

    Joins the schedule's ``"Australian Grand Prix"`` to the stored document's
    ``"AustralianGrandPrix"`` (both -> ``"australiangrandprix"``). We key on the
    event name rather than ``round_nr`` because stored ``round_nr`` is unreliable
    — some ingestion paths write the livetiming round (which is offset by
    pre-season testing) instead of the curated one, so the same GP can carry a
    different round number than the schedule reports.
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(c for c in text.lower() if c.isalnum())


def _existing_data_types(year: int) -> Dict[Tuple[str, str], Set[str]]:
    """Map ``(norm_event_name, session_type)`` -> set of stored ``data_type`` keys."""
    result: Dict[Tuple[str, str], Set[str]] = {}
    try:
        manager = MongoDBManager(year=year, version="v2")
        for gp in manager.list_all_gps(year=year):
            name_key = _norm_name(gp.get("name"))
            for sess in gp.get("sessions", []) or []:
                session_type = str(sess.get("session_type", "")).strip().upper()
                present = {
                    entry.get("data_type")
                    for entry in (sess.get("data", []) or [])
                    if isinstance(entry, dict) and entry.get("data_type")
                }
                # Merge rather than overwrite in case a GP is split across docs.
                result.setdefault((name_key, session_type), set()).update(present)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not read existing v2 data for %s: %s", year, exc)
    return result


# --------------------------------------------------------------------------- #
# Inventory (read-only)
# --------------------------------------------------------------------------- #
# Prefixes used to bucket stored keys that aren't in the singleton expectation
# set, so the UI can show "track_map_speed x18" instead of 18 raw tags. Longest
# prefix wins, so ``tyre_degradation_VER_fuel_corrected`` groups under the
# per-driver bucket rather than the bare ``tyre_degradation`` singleton.
_EXTRA_PREFIXES: Tuple[str, ...] = (
    "lap_all_data",
    "lap_times_distribution",
    "track_map_speed",
    "track_map_gear",
    "track_comparison",
    "throttle_brake_comparison",
    "lap_time_analysis",
    "corner_duel",
    "driver_radar",
    "speed_distribution",
    "tyre_degradation",
)


def _group_extras(extras: Iterable[str]) -> List[Dict[str, Any]]:
    """Bucket unexpected stored keys by feature family, with counts + samples."""
    buckets: "OrderedDict[str, List[str]]" = OrderedDict()
    for key in sorted(extras):
        prefix = next(
            (p for p in sorted(_EXTRA_PREFIXES, key=len, reverse=True) if key.startswith(p)),
            "other",
        )
        buckets.setdefault(prefix, []).append(key)
    return [
        {"prefix": prefix, "count": len(keys), "sample": keys[:6]}
        for prefix, keys in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]


def compute_inventory(
    *,
    year: Optional[int] = None,
    identifier: Optional[Identifier] = None,
    session: Optional[str] = None,
) -> Dict[str, Any]:
    """Report ungenerated singleton V2 plots (and stored extras) for a scope."""
    targets = _enumerate_targets(year=year, identifier=identifier, session=session)

    # One Mongo read per year, reused across that year's targets.
    existing_by_year: Dict[int, Dict[Tuple[str, str], Set[str]]] = {}
    gp_index: Dict[Tuple[int, int], Dict[str, Any]] = {}
    total_missing = 0
    total_expected = 0
    total_extra = 0

    for yr, round_nr, gp_name, session_type in targets:
        if yr not in existing_by_year:
            existing_by_year[yr] = _existing_data_types(yr)

        specs = specs_for_session(session_type)
        expected = [spec.data_type for spec in specs]
        labels = {spec.data_type: spec.label for spec in specs}
        present = existing_by_year[yr].get((_norm_name(gp_name), session_type), set())
        missing = [dt for dt in expected if dt not in present]
        extra = sorted(present - set(expected))

        total_expected += len(expected)
        total_missing += len(missing)
        total_extra += len(extra)

        gp = gp_index.setdefault(
            (yr, round_nr),
            {"year": yr, "round_nr": round_nr, "event_name": gp_name, "sessions": []},
        )
        gp["sessions"].append(
            {
                "session_type": session_type,
                "expected": expected,
                "present": sorted(present & set(expected)),
                "missing": missing,
                # Parameterized + legacy keys the expectation set says nothing
                # about. Grouped so a race with 1,000 lap_all_data documents
                # renders as one row.
                "extra_groups": _group_extras(extra),
                "extra_count": len(extra),
                "labels": labels,
            }
        )

    grand_prix = [gp_index[key] for key in sorted(gp_index)]
    return {
        "version": "v2",
        "scope": {"year": year, "gp": identifier, "session": session},
        "years_scanned": sorted({yr for yr, *_ in targets}),
        "total_sessions": sum(len(gp["sessions"]) for gp in grand_prix),
        "total_expected_plots": total_expected,
        "total_missing_plots": total_missing,
        "total_extra_plots": total_extra,
        "grand_prix": grand_prix,
    }


def compute_missing(
    *,
    year: Optional[int] = None,
    identifier: Optional[Identifier] = None,
    session: Optional[str] = None,
) -> Dict[str, Any]:
    """Backwards-compatible alias for :func:`compute_inventory`.

    ``GET /api/admin/plots/missing`` has shipped under this name; the report is
    a superset of what it used to return.
    """
    return compute_inventory(year=year, identifier=identifier, session=session)


def season_inventory(year: int) -> Dict[str, Any]:
    """Which season-scope keys exist for a year.

    Season payloads live in the synthetic ``{year}_SEASON`` document rather than
    under a Grand Prix, so they need their own read.
    """
    from src.repositories.plots import SEASON_SESSION_TYPE, season_gp_id

    present: Set[str] = set()
    try:
        manager = MongoDBManager(year=year, version="v2")
        doc = manager._get_collection(year).find_one(
            {"year": year, "gp_id": season_gp_id(year)}
        )
        for sess in (doc or {}).get("sessions", []) or []:
            if str(sess.get("session_type")) != SEASON_SESSION_TYPE:
                continue
            present |= {
                entry.get("data_type")
                for entry in (sess.get("data", []) or [])
                if isinstance(entry, dict) and entry.get("data_type")
            }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not read season data for %s: %s", year, exc)

    rows = []
    for spec in V2_SEASON_PLOTS:
        for variant in spec.default_variants:
            data_type = spec.stored_key(*variant)
            rows.append({
                "feature": spec.name,
                "label": spec.label or spec.name,
                "data_type": data_type,
                "present": data_type in present,
            })
    return {
        "year": year,
        "features": rows,
        "extra_groups": _group_extras(present - {r["data_type"] for r in rows}),
        # The request path keeps the live season in Redis only (results move
        # week to week), so a durable row here is admin-generated by definition.
        "is_current_season": year >= datetime.now(timezone.utc).year,
    }


# --------------------------------------------------------------------------- #
# Selection + planning
# --------------------------------------------------------------------------- #
@dataclass
class Selection:
    """What the operator asked to generate, on top of a year/GP/session scope.

    ``features=None`` means "every applicable **singleton**" — the historical
    default, and what the *Missing only* preset sends. Parameterized, season and
    career features are never implicit: each costs orders of magnitude more work
    (or, for season scope, reaches outside the requested GP entirely), so they
    only run when named explicitly.
    """

    features: Optional[List[str]] = None
    # Whole-group selection ("every per-driver feature"), which is what the UI's
    # group checkboxes and the legacy ``include_comparisons`` flag express. Kept
    # separate from ``features`` so selecting a group stays correct as the
    # catalog grows, rather than freezing today's key list into the request.
    include_kinds: Optional[Set[str]] = None
    drivers: Optional[List[str]] = None
    pairs: Optional[List[Tuple[str, str]]] = None
    lap_from: Optional[int] = None
    lap_to: Optional[int] = None
    windows: Optional[List[int]] = None
    career_years: Optional[List[int]] = None
    max_units: int = MAX_PLAN_UNITS

    @classmethod
    def from_legacy(cls, include_comparisons: bool) -> "Selection":
        """Map the old boolean onto the catalog.

        ``include_comparisons=True`` historically meant "every per-driver and
        per-pair feature for every driver / every pair", which is what the
        checkbox on the old admin page did. Per-lap and season features are not
        included — they had no equivalent before and are far more expensive.
        """
        if not include_comparisons:
            return cls()
        return cls(include_kinds={KIND_SINGLETON, KIND_PER_DRIVER, KIND_PER_PAIR})

    def selects(self, key: str, kind: str) -> bool:
        # No selection at all -> the historical default of every singleton.
        if self.features is None and self.include_kinds is None:
            return kind == KIND_SINGLETON
        # Once the caller states a selection it is exhaustive: singletons do not
        # sneak into a request that asked only for, say, per-driver features.
        if self.include_kinds and kind in self.include_kinds:
            return True
        return bool(self.features) and key in self.features


@dataclass(frozen=True)
class WorkUnit:
    """One generation call, with everything needed to skip, run and persist it."""

    label: str
    year: int
    identifier: Identifier
    session: str            # "" for season/career scope
    feature_key: str
    data_type: Optional[str]        # stored key, when knowable ahead of time
    run: Callable[[], Any]
    persist_dt: Optional[str] = None    # set => caller must write Mongo
    season_scope: bool = False

    def group_key(self) -> Tuple[Any, ...]:
        """Units sharing this key touch the same session and must stay on one
        thread so they share a warm ``SessionDataStore``.
        """
        return (self.year, str(self.identifier), self.session)


@dataclass
class PlanResult:
    units: List[WorkUnit] = field(default_factory=list)
    by_feature: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    drivers_by_session: Dict[str, List[str]] = field(default_factory=dict)
    truncated: bool = False

    def summary(self) -> Dict[str, Any]:
        return {
            "units": len(self.units),
            "by_feature": self.by_feature,
            "warnings": self.warnings,
            "drivers_by_session": self.drivers_by_session,
            "truncated": self.truncated,
            "sessions": len({u.group_key() for u in self.units if not u.season_scope}),
        }


def _resolve_drivers(
    job_errors: List[str],
    cache: Dict[Tuple[int, str, str], List[str]],
    yr: int,
    ident: Identifier,
    session_type: str,
    round_nr: int,
) -> List[str]:
    key = (yr, str(ident), session_type)
    if key not in cache:
        try:
            cache[key] = session_drivers(yr, ident, session_type)
        except Exception as exc:
            cache[key] = []
            job_errors.append(f"{yr} R{round_nr} {session_type} drivers: {exc}")
    return cache[key]


def _driver_thunk(spec: Any, y: int, ident: Identifier, e: str, drv: str) -> Callable[[], Any]:
    return lambda: spec.generate(y, ident, e, drv)


def _pair_thunk(spec: Any, y: int, ident: Identifier, e: str, d1: str, d2: str) -> Callable[[], Any]:
    return lambda: spec.generate(y, ident, e, d1, d2)


def _lap_thunk(spec: Any, y: int, ident: Identifier, e: str, drv: str, lap: int) -> Callable[[], Any]:
    return lambda: spec.generate(y, ident, e, drv, lap)


def _season_thunk(spec: Any, y: int, variant: Tuple[Any, ...]) -> Callable[[], Any]:
    return lambda: spec.generate(y, *variant)


def build_plan(
    *,
    year: Optional[int],
    identifier: Optional[Identifier],
    session: Optional[str],
    selection: Optional[Selection] = None,
) -> PlanResult:
    """Expand a scope + selection into concrete work units.

    Nothing is generated and nothing is read from Mongo here — this is pure
    planning, so :func:`estimate_plan` can call it to cost a selection before
    the operator commits.
    """
    selection = selection or Selection()
    result = PlanResult()
    targets = _enumerate_targets(year=year, identifier=identifier, session=session)
    drivers_cache: Dict[Tuple[int, str, str], List[str]] = {}

    def _add(unit: WorkUnit) -> bool:
        """Append a unit, returning False once the cap is hit.

        Hitting the cap aborts planning entirely, so the warning is recorded
        here rather than after the loops — those are never reached.
        """
        if len(result.units) >= selection.max_units:
            if not result.truncated:
                result.truncated = True
                result.warnings.append(
                    f"Plan truncated at {selection.max_units} units — narrow the scope "
                    f"or deselect a per-lap / per-pair feature."
                )
            return False
        result.units.append(unit)
        result.by_feature[unit.feature_key] = result.by_feature.get(unit.feature_key, 0) + 1
        return True

    for yr, round_nr, gp_name, session_type in targets:
        # Prefer the GP name identifier to avoid the pre-season-testing
        # round-number offset (mirrors PlotDataGenerator).
        ident: Identifier = gp_name or round_nr
        prefix = f"{yr} R{round_nr} {session_type}"

        # ---- singletons ----
        for spec in specs_for_session(session_type):
            if not selection.selects(spec.data_type, KIND_SINGLETON):
                continue
            if not _add(WorkUnit(
                label=f"{prefix} {spec.data_type}",
                year=yr, identifier=ident, session=session_type,
                feature_key=spec.data_type, data_type=spec.data_type,
                run=_singleton_thunk(spec, yr, ident, session_type),
                persist_dt=spec.data_type if spec.persist_result else None,
            )):
                return result

        # ---- parameterized: resolve the driver list only if needed ----
        wants_driver = any(
            selection.selects(s.name, KIND_PER_DRIVER) and s.applies(session_type)
            for s in V2_PER_DRIVER_PLOTS
        )
        wants_pair = any(
            selection.selects(s.name, KIND_PER_PAIR) and s.applies(session_type)
            for s in V2_PAIR_PLOTS
        )
        wants_lap = any(
            selection.selects(s.name, KIND_PER_DRIVER_LAP) and s.applies(session_type)
            for s in V2_PER_DRIVER_LAP_PLOTS
        )
        if not (wants_driver or wants_pair or wants_lap):
            continue

        drivers = selection.drivers or _resolve_drivers(
            result.warnings, drivers_cache, yr, ident, session_type, round_nr
        )
        drivers = [d.strip().upper() for d in drivers if d and d.strip()]
        if drivers:
            result.drivers_by_session[f"{yr}|{gp_name}|{session_type}"] = drivers

        for spec in V2_PER_DRIVER_PLOTS:
            if not (selection.selects(spec.name, KIND_PER_DRIVER) and spec.applies(session_type)):
                continue
            for drv in drivers:
                if not _add(WorkUnit(
                    label=f"{prefix} {spec.name}[{drv}]",
                    year=yr, identifier=ident, session=session_type,
                    feature_key=spec.name, data_type=spec.key_for(drv),
                    run=_driver_thunk(spec, yr, ident, session_type, drv),
                    persist_dt=spec.persist_key(drv) if spec.persist_key else None,
                )):
                    return result

        pairs = selection.pairs
        for spec in V2_PAIR_PLOTS:
            if not (selection.selects(spec.name, KIND_PER_PAIR) and spec.applies(session_type)):
                continue
            # An ordered feature stores {D1}_{D2} in the order given, so (A,B)
            # and (B,A) are different documents and a user asking for the
            # reverse gets a miss. Plan both directions for those.
            base = pairs if pairs is not None else list(combinations(drivers, 2))
            expanded = (
                [(a, b) for a, b in base] + [(b, a) for a, b in base]
                if spec.ordered else list(base)
            )
            for d1, d2 in expanded:
                if not _add(WorkUnit(
                    label=f"{prefix} {spec.name}[{d1}v{d2}]",
                    year=yr, identifier=ident, session=session_type,
                    feature_key=spec.name, data_type=spec.key_for(d1, d2),
                    run=_pair_thunk(spec, yr, ident, session_type, d1, d2),
                    persist_dt=spec.persist_key(d1, d2) if spec.persist_key else None,
                )):
                    return result

        for spec in V2_PER_DRIVER_LAP_PLOTS:
            if not (selection.selects(spec.name, KIND_PER_DRIVER_LAP) and spec.applies(session_type)):
                continue
            laps_by_driver = _plan_laps(
                result, yr, ident, session_type, drivers, selection, prefix
            )
            for drv, laps in laps_by_driver.items():
                for lap in laps:
                    if not _add(WorkUnit(
                        label=f"{prefix} {spec.name}[{drv} L{lap}]",
                        year=yr, identifier=ident, session=session_type,
                        feature_key=spec.name, data_type=spec.key_for(drv, lap),
                        run=_lap_thunk(spec, yr, ident, session_type, drv, lap),
                        persist_dt=spec.persist_key(drv, lap) if spec.persist_key else None,
                    )):
                        return result

    # ---- season / career scope (not tied to a session) ----
    for yr in _resolve_years(year):
        for spec in V2_SEASON_PLOTS:
            if not selection.selects(spec.name, KIND_SEASON):
                continue
            variants = spec.default_variants
            if spec.name == "season_form" and selection.windows:
                variants = tuple((w,) for w in selection.windows)
            for variant in variants:
                data_type = spec.stored_key(*variant)
                if not _add(WorkUnit(
                    label=f"{yr} season {data_type}",
                    year=yr, identifier="season", session="",
                    feature_key=spec.name, data_type=data_type,
                    run=_season_thunk(spec, yr, variant),
                    persist_dt=data_type, season_scope=True,
                )):
                    return result

        for spec in V2_CAREER_PLOTS:
            if not selection.selects(spec.name, KIND_CAREER):
                continue
            if not selection.career_years:
                result.warnings.append(
                    f"{spec.label or spec.name}: skipped — a career year range is required"
                )
                continue
            years = sorted(selection.career_years)
            data_type = spec.stored_key(years)
            store_year = max(years)
            if not _add(WorkUnit(
                label=f"{store_year} career {data_type}",
                year=store_year, identifier="season", session="",
                feature_key=spec.name, data_type=data_type,
                run=_season_thunk(spec, store_year, (years,)),
                persist_dt=data_type, season_scope=True,
            )):
                return result

    return result


def _singleton_thunk(spec: Any, y: int, ident: Identifier, e: str) -> Callable[[], Any]:
    return lambda: spec.generate(y, ident, e)


def _plan_laps(
    result: PlanResult,
    yr: int,
    ident: Identifier,
    session_type: str,
    drivers: Sequence[str],
    selection: Selection,
    prefix: str,
) -> Dict[str, List[int]]:
    """Lap numbers to generate per driver, bounded by the requested range.

    A lap range is mandatory: drivers x laps is the largest key space in the
    catalog (~1,100 units for a single race), so an unbounded request is almost
    always a mistake rather than an intent.
    """
    if selection.lap_from is None or selection.lap_to is None:
        result.warnings.append(
            f"{prefix} lap_all_data: skipped — a lap range (from/to) is required"
        )
        return {}
    lo, hi = sorted((int(selection.lap_from), int(selection.lap_to)))

    try:
        actual = session_driver_laps(yr, ident, session_type)
    except Exception as exc:
        result.warnings.append(f"{prefix} lap_all_data laps: {exc}")
        return {}

    out: Dict[str, List[int]] = {}
    for drv in drivers:
        laps = [lap for lap in actual.get(drv, []) if lo <= lap <= hi]
        if laps:
            out[drv] = laps
    return out


def estimate_plan(
    *,
    year: Optional[int],
    identifier: Optional[Identifier],
    session: Optional[str],
    selection: Optional[Selection] = None,
) -> Dict[str, Any]:
    """Dry-run cost of a selection, for the admin UI to show before committing."""
    plan = build_plan(
        year=year, identifier=identifier, session=session, selection=selection
    )
    summary = plan.summary()
    summary["by_feature"] = [
        {
            "feature": key,
            "label": (feature_by_key(key).label if feature_by_key(key) else key),
            "units": count,
        }
        for key, count in sorted(plan.by_feature.items(), key=lambda kv: -kv[1])
    ]
    return summary


# --------------------------------------------------------------------------- #
# Background job tracking
# --------------------------------------------------------------------------- #
@dataclass
class PlotGenJob:
    job_id: str
    scope: Dict[str, Any]
    status: str = "queued"  # queued | running | completed | failed | cancelled
    total: int = 0
    done: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    current: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    per_feature: Dict[str, Dict[str, int]] = field(default_factory=dict)
    selection: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def record(self, feature_key: str, outcome: str) -> None:
        bucket = self.per_feature.setdefault(
            feature_key, {"success": 0, "failed": 0, "skipped": 0}
        )
        bucket[outcome] = bucket.get(outcome, 0) + 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "scope": self.scope,
            "selection": self.selection,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "success": self.success,
            "failed": self.failed,
            "skipped": self.skipped,
            "current": self.current,
            "per_feature": self.per_feature,
            "errors": self.errors[-25:],
            "warnings": self.warnings,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_JOBS: "Dict[str, PlotGenJob]" = {}
_JOBS_LOCK = threading.Lock()
_MAX_JOBS = 50


def _register_job(job: PlotGenJob) -> None:
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job
        # Trim oldest finished jobs if we exceed the cap. Mongo keeps the full
        # history; this dict is only the hot cache.
        if len(_JOBS) > _MAX_JOBS:
            for jid in sorted(_JOBS, key=lambda k: _JOBS[k].started_at or ""):
                if _JOBS[jid].status in ("completed", "failed", "cancelled"):
                    del _JOBS[jid]
                    if len(_JOBS) <= _MAX_JOBS:
                        break


def get_job(job_id: str) -> Optional[PlotGenJob]:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def get_job_dict(job_id: str) -> Optional[Dict[str, Any]]:
    """Job state from memory, falling back to MongoDB.

    The fallback is what makes the progress poll work at all under a
    multi-worker deploy: the request that starts a job and the request that
    polls it routinely land on different processes.
    """
    job = get_job(job_id)
    if job is not None:
        return job.as_dict()
    return admin_jobs.get_job(job_id)


def list_jobs(limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Durable job history, newest first, with live in-memory state layered on.

    A job owned by this process has fresher counters than its last Mongo flush,
    so prefer the in-memory view where we have one.
    """
    stored = admin_jobs.list_jobs(limit=limit, status=status, kind=JOB_KIND)
    with _JOBS_LOCK:
        live = {jid: job.as_dict() for jid, job in _JOBS.items()}

    merged: List[Dict[str, Any]] = []
    for doc in stored:
        current = live.pop(doc["job_id"], None)
        merged.append({**doc, **current} if current else doc)

    # Anything running here that Mongo never accepted (write failure) still shows.
    for extra in live.values():
        if status is None or extra.get("status") == status:
            merged.append(extra)
    return merged[:limit]


def cancel_job(job_id: str) -> bool:
    """Request cancellation. The worker notices on its next progress flush."""
    flagged = admin_jobs.request_cancel(job_id)
    job = get_job(job_id)
    if job is not None and job.status in ("queued", "running"):
        _CANCELLED.add(job_id)
        return True
    return flagged


# In-process cancel signal, so a cancel served by the owning worker takes effect
# immediately rather than waiting for the Mongo round-trip.
_CANCELLED: Set[str] = set()


# --------------------------------------------------------------------------- #
# Backfill execution
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _JobWriter:
    """Throttled write-through of job progress to MongoDB.

    A full-catalog backfill is thousands of units. Flushing per unit would cost
    more than the generation, so state is flushed at most every
    ``_FLUSH_INTERVAL_SECONDS`` or every ``_FLUSH_EVERY_UNITS`` completions —
    plus unconditionally on every status transition. Cancellation is polled on
    the same cadence, which bounds how long a cancel takes to take effect.
    """

    def __init__(self, job: PlotGenJob) -> None:
        self.job = job
        self._last_flush = 0.0
        self._since_flush = 0
        self._pending_errors: List[str] = []
        self._lock = threading.Lock()

    def note_error(self, message: str) -> None:
        with self._lock:
            self.job.errors.append(message)
            self._pending_errors.append(message)

    def tick(self, force: bool = False) -> None:
        with self._lock:
            self._since_flush += 1
            due = (
                force
                or self._since_flush >= _FLUSH_EVERY_UNITS
                or (time.monotonic() - self._last_flush) >= _FLUSH_INTERVAL_SECONDS
            )
            if not due:
                return
            errors, self._pending_errors = self._pending_errors, []
            self._since_flush = 0
            self._last_flush = time.monotonic()
            job = self.job

        admin_jobs.update_job(
            job.job_id,
            status=job.status,
            total=job.total,
            done=job.done,
            success=job.success,
            failed=job.failed,
            skipped=job.skipped,
            current=job.current,
            per_feature=job.per_feature,
            warnings=job.warnings,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
        if errors:
            admin_jobs.push_errors(job.job_id, errors)

    def cancelled(self) -> bool:
        if self.job.job_id in _CANCELLED:
            return True
        return admin_jobs.is_cancelled(self.job.job_id)


def _bump_metric(success: bool) -> None:
    try:
        from src.core.observability.monitoring import (
            BACKGROUND_JOBS_FAILED,
            BACKGROUND_JOBS_PROCESSED,
        )
        (BACKGROUND_JOBS_PROCESSED if success else BACKGROUND_JOBS_FAILED).inc()
    except Exception:  # pragma: no cover - metrics are best-effort
        pass


def execute_plan(
    job: PlotGenJob,
    plan: PlanResult,
    *,
    force: bool = False,
    concurrency: int = 1,
) -> None:
    """Run a plan, updating ``job`` live and skipping already-stored units."""
    job.status = "running"
    job.started_at = job.started_at or _now()
    job.total = len(plan.units)
    job.warnings = list(plan.warnings)
    _register_job(job)

    writer = _JobWriter(job)
    writer.tick(force=True)

    # Existing keys per year, so a unit whose document is already stored is
    # skipped without calling the generator.
    existing_by_year: Dict[int, Dict[Tuple[str, str], Set[str]]] = {}
    existing_lock = threading.Lock()

    def _present(unit: WorkUnit) -> Set[str]:
        with existing_lock:
            if unit.year not in existing_by_year:
                existing_by_year[unit.year] = _existing_data_types(unit.year)
            gp_key = _norm_name(str(unit.identifier))
            return existing_by_year[unit.year].setdefault((gp_key, unit.session), set())

    progress_lock = threading.Lock()

    def _run_unit(unit: WorkUnit) -> None:
        present = None if unit.season_scope else _present(unit)
        with progress_lock:
            job.current = unit.label

        if not force and unit.data_type and present is not None and unit.data_type in present:
            with progress_lock:
                job.skipped += 1
                job.done += 1
                job.record(unit.feature_key, "skipped")
            writer.tick()
            return

        try:
            result = unit.run()
            if result:
                # Class-based features only warm Redis; persist to Mongo so the
                # inventory (and future reads) actually see them.
                if unit.persist_dt:
                    if unit.season_scope:
                        persist_season_generated(unit.year, unit.persist_dt, result)
                    else:
                        persist_generated(
                            unit.year, unit.identifier, unit.session, unit.persist_dt, result
                        )
                with progress_lock:
                    job.success += 1
                    job.record(unit.feature_key, "success")
                    if present is not None and unit.data_type:
                        present.add(unit.data_type)
                _bump_metric(True)
            else:
                with progress_lock:
                    job.failed += 1
                    job.record(unit.feature_key, "failed")
                writer.note_error(f"{unit.label}: empty result")
                _bump_metric(False)
        except Exception as exc:
            with progress_lock:
                job.failed += 1
                job.record(unit.feature_key, "failed")
            writer.note_error(f"{unit.label}: {exc}")
            logger.warning("Backfill failed for %s: %s", unit.label, exc)
            _bump_metric(False)
        finally:
            with progress_lock:
                job.done += 1
            writer.tick()

    cancelled = False
    try:
        # Group by session so units sharing a SessionDataStore stay on one
        # thread; parallelism happens across sessions only.
        groups: "OrderedDict[Tuple[Any, ...], List[WorkUnit]]" = OrderedDict()
        for unit in plan.units:
            groups.setdefault(unit.group_key(), []).append(unit)

        def _run_group(units: List[WorkUnit]) -> bool:
            for unit in units:
                if writer.cancelled():
                    return True
                _run_unit(unit)
            return False

        workers = max(1, min(int(concurrency or 1), MAX_CONCURRENCY))
        if workers == 1 or len(groups) <= 1:
            for units in groups.values():
                if _run_group(units):
                    cancelled = True
                    break
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="backfill") as pool:
                for was_cancelled in pool.map(_run_group, list(groups.values())):
                    cancelled = cancelled or was_cancelled

        job.status = "cancelled" if cancelled else "completed"
    except Exception as exc:  # pragma: no cover - defensive
        job.status = "failed"
        writer.note_error(f"fatal: {exc}")
        logger.error("Backfill job %s crashed: %s", job.job_id, exc, exc_info=True)
    finally:
        job.current = None
        job.finished_at = _now()
        _CANCELLED.discard(job.job_id)
        writer.tick(force=True)


def generate_missing(
    job: PlotGenJob,
    *,
    year: Optional[int],
    identifier: Optional[Identifier],
    session: Optional[str],
    force: bool = False,
    include_comparisons: bool = False,
    selection: Optional[Selection] = None,
    concurrency: int = 1,
) -> None:
    """Plan and run a backfill for the scope, updating ``job`` live.

    Kept as the single entrypoint the API and the UI call. ``include_comparisons``
    is the legacy switch and maps onto a full per-driver/per-pair selection.
    """
    if selection is None:
        selection = Selection.from_legacy(include_comparisons)

    plan = build_plan(
        year=year, identifier=identifier, session=session, selection=selection
    )
    execute_plan(job, plan, force=force, concurrency=concurrency)


def scopes_overlap(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Whether two job scopes could touch the same sessions.

    Deliberately conservative: a missing (``None``) field means "everything", so
    a whole-year job overlaps every narrower job in that year.
    """
    for key in ("year", "gp", "session"):
        av, bv = a.get(key), b.get(key)
        if av is None or bv is None:
            continue
        if str(av).strip().upper() != str(bv).strip().upper():
            return False
    return True


def find_conflicting_job(scope: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A live job whose scope overlaps ``scope``, if any."""
    for doc in admin_jobs.running_jobs(kind=JOB_KIND):
        if scopes_overlap(scope, doc.get("scope") or {}):
            return doc
    return None


def start_generation_job(
    *,
    year: Optional[int],
    identifier: Optional[Identifier],
    session: Optional[str],
    force: bool = False,
    include_comparisons: bool = False,
    selection: Optional[Selection] = None,
    concurrency: int = 1,
) -> PlotGenJob:
    """Create a job and run the backfill in a daemon thread."""
    scope = {
        "year": year,
        "gp": identifier,
        "session": session,
        "force": force,
        "include_comparisons": include_comparisons,
        "concurrency": concurrency,
    }
    selection_doc = _selection_as_dict(selection, include_comparisons)

    job = PlotGenJob(
        job_id=uuid.uuid4().hex[:12],
        scope=scope,
        selection=selection_doc,
        started_at=_now(),
    )
    _register_job(job)
    admin_jobs.create_job(job.job_id, kind=JOB_KIND, scope=scope, selection=selection_doc)

    thread = threading.Thread(
        target=generate_missing,
        args=(job,),
        kwargs={
            "year": year,
            "identifier": identifier,
            "session": session,
            "force": force,
            "include_comparisons": include_comparisons,
            "selection": selection,
            "concurrency": concurrency,
        },
        name=f"plot-backfill-{job.job_id}",
        daemon=True,
    )
    thread.start()
    return job


def _selection_as_dict(
    selection: Optional[Selection], include_comparisons: bool
) -> Dict[str, Any]:
    if selection is None:
        selection = Selection.from_legacy(include_comparisons)
    return {
        "features": selection.features,
        "drivers": selection.drivers,
        "include_kinds": sorted(selection.include_kinds) if selection.include_kinds else None,
        "pairs": [list(p) for p in selection.pairs] if selection.pairs else None,
        "lap_from": selection.lap_from,
        "lap_to": selection.lap_to,
        "windows": selection.windows,
        "career_years": selection.career_years,
    }


# Kept for readers that imported these names before the planner split.
__all__ = [
    "PlotGenJob",
    "PlanResult",
    "Selection",
    "WorkUnit",
    "available_events",
    "available_years",
    "build_plan",
    "cancel_job",
    "compute_inventory",
    "compute_missing",
    "estimate_plan",
    "execute_plan",
    "find_conflicting_job",
    "generate_missing",
    "get_job",
    "get_job_dict",
    "list_jobs",
    "season_inventory",
    "start_generation_job",
]
