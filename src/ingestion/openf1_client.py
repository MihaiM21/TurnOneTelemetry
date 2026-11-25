"""OpenF1 (api.openf1.org) as a fallback for the livetiming archive.

Since 14-15 Oct 2026 livetiming's ``/static/<year>/`` archive answers 401 without an
F1 TV token. OpenF1 republishes the same feed (2023+) for free, keyed by the same
meeting ``Key`` livetiming uses, with position in the same tenths-of-a-metre frame.

This module only *reads* OpenF1. Turning its rows back into livetiming-shaped
streams is :mod:`src.ingestion.openf1_streams`; nothing downstream of
``F1StaticClient`` / ``SessionDataStore`` knows OpenF1 exists.

Two things it reconstructs that make the rest of the codebase unaware of the swap:

* :func:`build_index` -- a livetiming ``Index.json`` (meetings, sessions, ``Path``).
  Session paths are *synthesized* from OpenF1's names and local dates. Checked
  against the committed 2023-2025 snapshots: 310/311 match exactly (the miss is
  2024 Sao Paulo qualifying, moved a day by rain -- livetiming kept the planned
  date). An exact path keeps ``source_token`` -- and so every pre-cutoff cache
  entry -- valid.
* :func:`session_key_for_path` -- the reverse: livetiming session path -> OpenF1
  ``session_key``, so a stream whose URL came from a real livetiming index can
  still be served.
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict, List, Optional

import requests

from src.core.config import settings
from src.core.exceptions import DataNotAvailableError, UpstreamUnavailableError
from src.core.logging import get_logger

logger = get_logger(__name__)

#: OpenF1 has no data before this season.
MIN_YEAR = 2023

_CACHE_TTL_CURRENT_S = 300
_CACHE_TTL_PAST_S = 86400
_MAX_RETRIES = 4


def _parse_offset(offset: Optional[str]) -> timedelta:
    """``"-08:00:00"`` -> ``timedelta(hours=-8)``."""
    if not offset:
        return timedelta(0)
    sign = -1 if offset.startswith("-") else 1
    parts = [int(p) for p in offset.lstrip("+-").split(":")]
    while len(parts) < 3:
        parts.append(0)
    return sign * timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])


def parse_utc(value: str) -> datetime:
    """OpenF1 ISO timestamp (``...+00:00``) -> aware UTC datetime."""
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def local_naive(utc_iso: str, gmt_offset: Optional[str]) -> datetime:
    """UTC ISO + ``gmt_offset`` -> naive local wall-clock, as livetiming writes ``StartDate``."""
    return (parse_utc(utc_iso) + _parse_offset(gmt_offset)).replace(tzinfo=None)


def livetiming_session_name(name: str, year: int) -> str:
    """OpenF1 session name -> the spelling livetiming used that season.

    2023 called sprint qualifying "Sprint Shootout"; pre-season testing days are
    "Practice N" in livetiming and "Day N" in OpenF1.
    """
    if year == 2023 and name == "Sprint Qualifying":
        return "Sprint Shootout"
    if name.startswith("Day ") and name[4:].isdigit():
        return f"Practice {name[4:]}"
    return name


def synth_session_path(meeting_name: str, meeting_sessions: List[Dict[str, Any]],
                       session: Dict[str, Any], year: int) -> str:
    """``{year}/{race-day}_{Meeting_Name}/{session-day}_{Session_Name}/``.

    The meeting folder is dated by the meeting's last session (local date); each
    session folder by its own local start date.
    """
    last = max(local_naive(s["date_start"], s.get("gmt_offset")) for s in meeting_sessions)
    folder = f"{year}/{last:%Y-%m-%d}_{meeting_name.replace(' ', '_')}/"
    start = local_naive(session["date_start"], session.get("gmt_offset"))
    name = livetiming_session_name(session["session_name"], year)
    return f"{folder}{start:%Y-%m-%d}_{name.replace(' ', '_')}/"


class OpenF1Client:
    """Throttled reader for api.openf1.org.

    The free tier enforces a small per-second/per-minute budget and answers 422
    ("too much data at once") to an unfiltered whole-session telemetry query, so
    telemetry is always fetched per driver. Calls are serialized process-wide and
    spaced by ``settings.openf1_min_interval_s``; a 429 backs off and retries.
    """

    DEFAULT_TIMEOUT = 60
    _lock = Lock()
    _last_call = 0.0
    _window: "deque[float]" = deque()

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.setdefault("Accept", "application/json")
        self.base_url = settings.openf1_base_url.rstrip("/") + "/"

    def get(self, endpoint: str, **params: Any) -> List[Dict[str, Any]]:
        """GET ``endpoint`` and return its rows; ``[]`` when OpenF1 has none.

        OpenF1 answers 404 ``{"detail": "No results found."}`` for an empty result,
        which is data ("nothing happened"), not an outage.
        """
        url = self.base_url + endpoint
        for attempt in range(_MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=self.DEFAULT_TIMEOUT)
            except requests.RequestException as exc:
                if attempt < _MAX_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise UpstreamUnavailableError(source="openf1", reason=f"{endpoint}: {exc}") from exc

            if resp.status_code == 404:
                return []
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < _MAX_RETRIES:
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 * (attempt + 1)
                    logger.info("OpenF1 %s answered %s; retrying in %.1fs", endpoint, resp.status_code, delay)
                    time.sleep(delay)
                    continue
            if resp.status_code == 422:
                raise UpstreamUnavailableError(
                    source="openf1", reason=f"{endpoint} refused the query as too large: {params}"
                )
            if resp.status_code != 200:
                raise UpstreamUnavailableError(
                    source="openf1", reason=f"{endpoint} answered {resp.status_code}"
                )
            try:
                rows = resp.json()
            except ValueError as exc:
                raise UpstreamUnavailableError(source="openf1", reason=f"{endpoint}: bad JSON") from exc
            return rows if isinstance(rows, list) else []
        raise UpstreamUnavailableError(source="openf1", reason=f"{endpoint}: retries exhausted")

    @classmethod
    def _throttle(cls) -> None:
        """Space calls by ``openf1_min_interval_s`` and keep ``openf1_max_per_minute`` per 60 s.

        The free tier answers 429 with ``Retry-After: 60`` once the minute budget
        is spent, so waiting here is cheaper than being told to wait a minute.
        """
        with cls._lock:
            now = time.monotonic()
            wait = cls._last_call + settings.openf1_min_interval_s - now
            window = cls._window
            while window and now - window[0] >= 60:
                window.popleft()
            if settings.openf1_max_per_minute and len(window) >= settings.openf1_max_per_minute:
                wait = max(wait, 60 - (now - window[0]))
            if wait > 0:
                time.sleep(wait)
            cls._last_call = time.monotonic()
            window.append(cls._last_call)


# ---------------------------------------------------------------------------
# Season catalogue (meetings + sessions), cached per year
# ---------------------------------------------------------------------------
_CATALOG: Dict[int, tuple] = {}
_CATALOG_LOCK = Lock()


def clear_cache() -> None:
    with _CATALOG_LOCK:
        _CATALOG.clear()


def _check_year(year: int) -> None:
    if int(year) < MIN_YEAR:
        raise DataNotAvailableError(
            year=year, source="openf1", reason=f"OpenF1 has no data before {MIN_YEAR}"
        )


def season_catalog(year: int, client: Optional[OpenF1Client] = None) -> Dict[str, Any]:
    """``{"meetings": {key: row}, "sessions": [row, ...]}`` for ``year`` (cached)."""
    year = int(year)
    _check_year(year)
    with _CATALOG_LOCK:
        hit = _CATALOG.get(year)
    if hit is not None and hit[0] > time.time():
        return hit[1]

    client = client or OpenF1Client()
    meetings = {m["meeting_key"]: m for m in client.get("meetings", year=year)}
    sessions = [s for s in client.get("sessions", year=year) if not s.get("is_cancelled")]
    catalog = {"meetings": meetings, "sessions": sessions}
    ttl = _CACHE_TTL_PAST_S if year < datetime.utcnow().year else _CACHE_TTL_CURRENT_S
    with _CATALOG_LOCK:
        _CATALOG[year] = (time.time() + ttl, catalog)
    return catalog


def _sessions_by_meeting(sessions: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for s in sessions:
        grouped.setdefault(s["meeting_key"], []).append(s)
    for rows in grouped.values():
        rows.sort(key=lambda s: s["date_start"])
    return grouped


def build_index(year: int, client: Optional[OpenF1Client] = None) -> Dict[str, Any]:
    """A livetiming ``Index.json`` rebuilt from OpenF1 (``"_source": "openf1"``).

    Only meetings with at least one session are listed, like livetiming, so a
    future round keeps ``key=None`` until OpenF1 publishes its schedule.
    """
    catalog = season_catalog(year, client)
    out_meetings = []
    for meeting_key, sessions in sorted(_sessions_by_meeting(catalog["sessions"]).items(),
                                        key=lambda kv: kv[1][0]["date_start"]):
        m = catalog["meetings"].get(meeting_key) or {}
        name = m.get("meeting_name") or sessions[0].get("location", "")
        out_sessions = []
        for s in sessions:
            offset = s.get("gmt_offset") or "00:00:00"
            out_sessions.append({
                "Key": s["session_key"],
                "Type": s.get("session_type", ""),
                "Name": livetiming_session_name(s["session_name"], year),
                "StartDate": local_naive(s["date_start"], offset).isoformat(),
                "EndDate": local_naive(s["date_end"], offset).isoformat() if s.get("date_end") else "",
                "GmtOffset": offset.lstrip("+"),
                "Path": synth_session_path(name, sessions, s, year),
            })
        out_meetings.append({
            "Key": meeting_key,
            "Code": "",
            "Number": None,
            "Location": m.get("location") or sessions[0].get("location", ""),
            "OfficialName": m.get("meeting_official_name", ""),
            "Name": name,
            "Country": {"Key": m.get("country_key"), "Code": m.get("country_code", ""),
                        "Name": m.get("country_name", "")},
            "Circuit": {"Key": m.get("circuit_key", sessions[0].get("circuit_key")),
                        "ShortName": m.get("circuit_short_name", sessions[0].get("circuit_short_name", ""))},
            "Sessions": out_sessions,
        })
    return {"Year": year, "Meetings": out_meetings, "_source": "openf1"}


def session_path_from_url(url: str) -> Optional[str]:
    """``.../2026/2026-11-22_Las_Vegas_Grand_Prix/2026-11-21_Qualifying/X.json`` -> session path."""
    from urllib.parse import unquote

    parts = [p for p in unquote(url).split("/") if p]
    for i, part in enumerate(parts):
        if len(part) == 4 and part.isdigit() and i + 2 < len(parts):
            return "/".join(parts[i:i + 3]) + "/"
    return None


def session_key_for_path(path: str, client: Optional[OpenF1Client] = None) -> Dict[str, Any]:
    """The OpenF1 session row for a livetiming session path.

    Exact synthesized-path match first. Failing that (a session moved after
    livetiming named its folder), the same meeting folder plus the same session
    name, ignoring the session folder's date. Raises ``DataNotAvailableError``.
    """
    year = int(path.split("/", 1)[0])
    catalog = season_catalog(year, client)
    grouped = _sessions_by_meeting(catalog["sessions"])
    want_meeting, want_session = path.rstrip("/").split("/")[1:3]
    want_session_name = want_session.split("_", 1)[1] if "_" in want_session else want_session

    loose = None
    for meeting_key, sessions in grouped.items():
        name = (catalog["meetings"].get(meeting_key) or {}).get("meeting_name") or sessions[0].get("location", "")
        for s in sessions:
            synth = synth_session_path(name, sessions, s, year)
            if synth == path:
                return s
            meeting_folder, session_folder = synth.rstrip("/").split("/")[1:3]
            if meeting_folder == want_meeting and session_folder.split("_", 1)[-1] == want_session_name:
                loose = s
    if loose is not None:
        return loose
    raise DataNotAvailableError(year=year, source="openf1", reason=f"no OpenF1 session for {path}")


__all__ = [
    "OpenF1Client",
    "build_index",
    "clear_cache",
    "livetiming_session_name",
    "local_naive",
    "parse_utc",
    "season_catalog",
    "session_key_for_path",
    "session_path_from_url",
    "synth_session_path",
]
