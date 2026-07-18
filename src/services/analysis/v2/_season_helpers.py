"""
Shared season-wide helpers (V2, jolpica/Ergast-backed).

Feeds the seasonal "gem" features (teammate battle tracker, season form guide)
that need results across every round of a season rather than a single
session. Two building blocks:

  * ``fetch_season_results`` — paginated jolpica GET of ``results`` /
    ``qualifying`` / ``sprint`` for a whole season, normalized to flat rows.
    Reuses :class:`src.ingestion.ergast_client.ErgastStandingsClient` for its
    session + error wrapping instead of duplicating retry/error logic.
  * ``season_cached_or_generate`` — caching wrapper: past seasons are
    immutable and go through the Mongo-backed ``cached_or_generate``; the
    current season is volatile (results trickle in weekly) and is cached in
    Redis only, with a short TTL.
  * ``pair_teammates`` — per-constructor teammate pairing that tolerates
    mid-season driver swaps (three-plus drivers sharing a constructor across
    the season) by picking the two most-frequent drivers and skipping rounds
    where either is absent.
"""
from __future__ import annotations

import datetime
from typing import Any, Callable, Dict, List, Literal, Optional

from src.core.logging import get_logger
from src.ingestion.ergast_client import ErgastStandingsClient
from src.services.analysis.base import cached_or_generate
from src.services.analysis.v2._helpers import parse_f1_time

logger = get_logger(__name__)

_KindT = Literal["race", "qualifying", "sprint"]

# jolpica/Ergast endpoint suffix per result kind.
_KIND_ENDPOINT = {
    "race": "results",
    "qualifying": "qualifying",
    "sprint": "sprint",
}

_PAGE_LIMIT = 100

# Redis TTL for current-season payloads (results trickle in weekly; keep this
# short so a fresh race weekend shows up within the hour).
_CURRENT_SEASON_TTL_S = 3600


def _current_season_year() -> int:
    return datetime.datetime.utcnow().year


def _races_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        mrdata = payload["MRData"]
        table = mrdata["RaceTable"]
        races = table.get("Races", [])
    except (KeyError, TypeError):
        return []
    return races


def _total_from_payload(payload: Dict[str, Any]) -> int:
    try:
        return int(payload["MRData"].get("total", 0) or 0)
    except (KeyError, TypeError, ValueError):
        return 0


def _classified(position_text: Optional[str]) -> bool:
    if position_text is None:
        return False
    return str(position_text).strip().isdigit()


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _q_time_s(q_result: Dict[str, Any], key: str) -> Optional[float]:
    raw = q_result.get(key)
    if not raw:
        return None
    parsed = parse_f1_time(raw)
    return parsed if parsed else None


def _normalize_race_row(race: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    driver = result.get("Driver", {}) or {}
    constructors = result.get("Constructor") or (result.get("Constructors") or [{}])[0]
    if isinstance(constructors, list):
        constructors = constructors[0] if constructors else {}
    full_name = " ".join(
        part for part in (driver.get("givenName"), driver.get("familyName")) if part
    ).strip()
    position_text = result.get("positionText")
    return {
        "round": _to_int(race.get("round")),
        "driver_code": driver.get("code") or driver.get("driverId", ""),
        "driver_name": full_name or driver.get("driverId", ""),
        "constructor": constructors.get("name", "") if constructors else "",
        "position": _to_int(result.get("position")),
        "grid": _to_int(result.get("grid")),
        "status": result.get("status"),
        "classified": _classified(position_text),
        "q1_s": None,
        "q2_s": None,
        "q3_s": None,
    }


def _normalize_quali_row(race: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    driver = result.get("Driver", {}) or {}
    constructor = result.get("Constructor", {}) or {}
    full_name = " ".join(
        part for part in (driver.get("givenName"), driver.get("familyName")) if part
    ).strip()
    position = _to_int(result.get("position"))
    return {
        "round": _to_int(race.get("round")),
        "driver_code": driver.get("code") or driver.get("driverId", ""),
        "driver_name": full_name or driver.get("driverId", ""),
        "constructor": constructor.get("name", ""),
        "position": position,
        "grid": None,
        "status": None,
        "classified": position is not None,
        "q1_s": _q_time_s(result, "Q1"),
        "q2_s": _q_time_s(result, "Q2"),
        "q3_s": _q_time_s(result, "Q3"),
    }


def _rows_from_page(kind: _KindT, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for race in _races_from_payload(payload):
        if kind == "race":
            results = race.get("Results", []) or []
            for result in results:
                rows.append(_normalize_race_row(race, result))
        elif kind == "sprint":
            results = race.get("SprintResults", []) or []
            for result in results:
                rows.append(_normalize_race_row(race, result))
        else:  # qualifying
            results = race.get("QualifyingResults", []) or []
            for result in results:
                rows.append(_normalize_quali_row(race, result))
    return rows


def fetch_season_results(
    year: int, kind: _KindT, client: Optional[ErgastStandingsClient] = None
) -> List[Dict[str, Any]]:
    """Fetch every round's results for ``year`` from jolpica, paginated.

    ``kind`` selects the endpoint: ``"race"`` -> ``results.json``,
    ``"qualifying"`` -> ``qualifying.json``, ``"sprint"`` -> ``sprint.json``.
    Rows are normalized flat dicts (see module docstring / plan for schema).
    Reuses :class:`ErgastStandingsClient` for session + error handling rather
    than duplicating retry/error logic.
    """
    if kind not in _KIND_ENDPOINT:
        raise ValueError(f"Unknown season-results kind: {kind!r}")

    ergast = client or ErgastStandingsClient()
    endpoint = _KIND_ENDPOINT[kind]

    rows: List[Dict[str, Any]] = []
    offset = 0
    total = None
    while total is None or offset < total:
        url = (
            f"{ergast.BASE_URL}/{year}/{endpoint}.json"
            f"?limit={_PAGE_LIMIT}&offset={offset}"
        )
        payload = ergast._get_json(url)
        if total is None:
            total = _total_from_payload(payload)
        rows.extend(_rows_from_page(kind, payload))
        offset += _PAGE_LIMIT
        if total == 0:
            break

    return rows


def season_cached_or_generate(
    year: int, data_type: str, generator: Callable[[], Any]
) -> Any:
    """Cache season-wide payloads: Mongo (immutable) for past seasons, Redis
    only (short TTL) for the current season. Fails open to ``generator()``.
    """
    if year < _current_season_year():
        try:
            return cached_or_generate(
                year=year, identifier="season", session="Season",
                data_type=data_type, generator=generator, version="v2",
            )
        except Exception as exc:
            logger.warning(
                "season_cached_or_generate: Mongo path failed for %s/%s, "
                "falling back to generator: %s", year, data_type, exc,
            )
            return generator()

    # Current season: Redis-only, short TTL. Never persisted to Mongo since
    # results keep changing week to week.
    try:
        from src.core.cache.redis_cache import get_sync_cache, make_plot_key

        cache = get_sync_cache()
        key = make_plot_key(
            year=year, gp_id="season", session="Season", data_type=data_type,
            params={"v": "v2"},
        )
        if cache.enabled:
            hit = cache.get_json(key)
            if hit is not None:
                return hit

        result = generator()
        if cache.enabled and result is not None:
            try:
                cache.set_json(key, result, ttl=_CURRENT_SEASON_TTL_S)
            except Exception:
                pass
        return result
    except Exception as exc:
        logger.debug("season_cached_or_generate: Redis path failed, generating fresh: %s", exc)
        return generator()


def pair_teammates(results: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Pair the two most-frequent drivers per constructor.

    Handles mid-season swaps (a constructor may show 3+ ``driver_code``
    values across the season): only the two most common drivers are kept as
    "the" teammate pair. Returns ``{constructor: [driver_a, driver_b]}``,
    sorted alphabetically by ``driver_a`` for determinism when frequencies
    tie (ties broken by driver code).
    """
    counts: Dict[str, Dict[str, int]] = {}
    for row in results:
        constructor = row.get("constructor")
        driver = row.get("driver_code")
        if not constructor or not driver:
            continue
        counts.setdefault(constructor, {})
        counts[constructor][driver] = counts[constructor].get(driver, 0) + 1

    pairs: Dict[str, List[str]] = {}
    for constructor, driver_counts in counts.items():
        ranked = sorted(driver_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        top_two = [drv for drv, _ in ranked[:2]]
        if len(top_two) < 2:
            continue
        pairs[constructor] = sorted(top_two)

    return pairs
