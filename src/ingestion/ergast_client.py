"""
Ergast Standings Client
=======================
Fetches drivers and constructors championship standings from the jolpi.ca
Ergast mirror. The original ergast.com is being retired; jolpi.ca preserves
the same response shape under a maintained host.

Used as the fallback source for the V2 standings endpoints, behind the
livetiming-based primary path in F1StaticClient.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from src.core.exceptions import SessionNotFoundError, UpstreamUnavailableError
from src.core.logging import get_logger

logger = get_logger(__name__)


class ErgastStandingsClient:
    BASE_URL = "https://api.jolpi.ca/ergast/f1"
    DEFAULT_TIMEOUT = 30

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "T1API/standings (+https://t1f1.com)",
        })

    def _get_json(self, url: str) -> Dict[str, Any]:
        logger.info("Ergast fetch: %s", url)
        try:
            resp = self.session.get(url, timeout=self.DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise UpstreamUnavailableError(
                source="ergast", reason=f"Network failure fetching {url}: {exc}"
            ) from exc

        if resp.status_code == 404:
            raise SessionNotFoundError(reason=f"Ergast has no data for {url}")
        if resp.status_code >= 500:
            raise UpstreamUnavailableError(
                source="ergast",
                reason=f"Ergast returned {resp.status_code} for {url}",
            )

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise UpstreamUnavailableError(source="ergast", reason=str(exc)) from exc

        try:
            return resp.json()
        except ValueError as exc:
            raise UpstreamUnavailableError(
                source="ergast", reason=f"Ergast returned non-JSON for {url}: {exc}"
            ) from exc

    @staticmethod
    def _standings_lists(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Ergast wraps the actual list in MRData.StandingsTable.StandingsLists[0].
        try:
            return payload["MRData"]["StandingsTable"]["StandingsLists"]
        except (KeyError, TypeError) as exc:
            raise UpstreamUnavailableError(
                source="ergast",
                reason=f"Unexpected Ergast payload shape: {exc}",
            ) from exc

    def fetch_drivers_standings(
        self, year: int, round_nr: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        suffix = f"/{round_nr}" if round_nr is not None else ""
        url = f"{self.BASE_URL}/{year}{suffix}/driverstandings/?format=json"
        payload = self._get_json(url)
        lists = self._standings_lists(payload)
        if not lists:
            return []

        items = lists[0].get("DriverStandings", [])
        normalized: List[Dict[str, Any]] = []
        for item in items:
            driver = item.get("Driver", {}) or {}
            constructors = item.get("Constructors", []) or []
            team = constructors[0].get("name") if constructors else None
            full_name = " ".join(
                part for part in (driver.get("givenName"), driver.get("familyName")) if part
            ).strip()
            normalized.append({
                "position": int(item.get("position", 0) or 0),
                "points": float(item.get("points", 0) or 0),
                "wins": int(item.get("wins", 0) or 0),
                "driver_code": driver.get("code") or driver.get("driverId", ""),
                "driver_name": full_name or driver.get("driverId", ""),
                "team": team or "",
                "nationality": driver.get("nationality"),
            })
        return normalized

    def fetch_constructors_standings(
        self, year: int, round_nr: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        suffix = f"/{round_nr}" if round_nr is not None else ""
        url = f"{self.BASE_URL}/{year}{suffix}/constructorstandings/?format=json"
        payload = self._get_json(url)
        lists = self._standings_lists(payload)
        if not lists:
            return []

        items = lists[0].get("ConstructorStandings", [])
        normalized: List[Dict[str, Any]] = []
        for item in items:
            constructor = item.get("Constructor", {}) or {}
            normalized.append({
                "position": int(item.get("position", 0) or 0),
                "points": float(item.get("points", 0) or 0),
                "wins": int(item.get("wins", 0) or 0),
                "team": constructor.get("name", ""),
                "nationality": constructor.get("nationality"),
            })
        return normalized
