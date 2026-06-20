"""
V2 standings service.

Composes:
  - F1StaticClient (primary, livetiming) → ErgastStandingsClient (fallback)
    via `with_fallback` from src/services/analysis/base.py
  - Redis → MongoDB → generate via `cached_or_generate`

The generators return a dict shaped like the API response so the cache
stores the final shape. The router only needs to add the response model.

NB: the primary path normalizes whatever livetiming returns into the same
flat-dict shape Ergast uses. If a future livetiming feed surfaces extra
fields, extend the normalizer here — the cache key namespacing will handle
the rest.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.core.logging import get_logger
from src.ingestion.ergast_client import ErgastStandingsClient
from src.ingestion.static_client import F1StaticClient
from src.services.analysis.base import cached_or_generate, with_fallback

logger = get_logger(__name__)


def _normalize_livetiming_drivers(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for row in rows:
        normalized.append({
            "position": int(row.get("Position") or row.get("position") or 0),
            "points": float(row.get("Points") or row.get("points") or 0),
            "wins": int(row.get("Wins") or row.get("wins") or 0),
            "driver_code": row.get("Tla") or row.get("Code") or row.get("driver_code") or "",
            "driver_name": row.get("FullName") or row.get("driver_name") or "",
            "team": row.get("TeamName") or row.get("team") or "",
            "nationality": row.get("Nationality") or row.get("nationality"),
        })
    return normalized


def _normalize_livetiming_constructors(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for row in rows:
        normalized.append({
            "position": int(row.get("Position") or row.get("position") or 0),
            "points": float(row.get("Points") or row.get("points") or 0),
            "wins": int(row.get("Wins") or row.get("wins") or 0),
            "team": row.get("TeamName") or row.get("Name") or row.get("team") or "",
            "nationality": row.get("Nationality") or row.get("nationality"),
        })
    return normalized


def _identifier(round_nr: Optional[int]) -> str:
    return str(round_nr) if round_nr is not None else "season"


def get_drivers_standings(year: int, round_nr: Optional[int] = None) -> Dict[str, Any]:
    def _generate() -> Dict[str, Any]:
        def primary() -> Dict[str, Any]:
            rows = F1StaticClient().fetch_drivers_standings(year, round_nr)
            return {
                "year": year,
                "round": round_nr,
                "source": "livetiming",
                "standings": _normalize_livetiming_drivers(rows),
            }

        def secondary() -> Dict[str, Any]:
            rows = ErgastStandingsClient().fetch_drivers_standings(year, round_nr)
            return {
                "year": year,
                "round": round_nr,
                "source": "ergast",
                "standings": rows,
            }

        return with_fallback(
            primary=primary,
            secondary=secondary,
            primary_source="livetiming",
            secondary_source="ergast",
            year=year,
            gp=round_nr,
            session="standings",
            data_type="drivers_standings",
        )

    return cached_or_generate(
        year=year,
        identifier=_identifier(round_nr),
        session="standings",
        data_type="drivers_standings",
        generator=_generate,
        version="v2",
    )


def get_constructors_standings(year: int, round_nr: Optional[int] = None) -> Dict[str, Any]:
    def _generate() -> Dict[str, Any]:
        def primary() -> Dict[str, Any]:
            rows = F1StaticClient().fetch_constructors_standings(year, round_nr)
            return {
                "year": year,
                "round": round_nr,
                "source": "livetiming",
                "standings": _normalize_livetiming_constructors(rows),
            }

        def secondary() -> Dict[str, Any]:
            rows = ErgastStandingsClient().fetch_constructors_standings(year, round_nr)
            return {
                "year": year,
                "round": round_nr,
                "source": "ergast",
                "standings": rows,
            }

        return with_fallback(
            primary=primary,
            secondary=secondary,
            primary_source="livetiming",
            secondary_source="ergast",
            year=year,
            gp=round_nr,
            session="standings",
            data_type="constructors_standings",
        )

    return cached_or_generate(
        year=year,
        identifier=_identifier(round_nr),
        session="standings",
        data_type="constructors_standings",
        generator=_generate,
        version="v2",
    )
