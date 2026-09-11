"""
SessionDataStore: one-stop, cached access to a single F1 session's streams.

The V2 analysis modules each re-fetch and re-parse the same livetiming
``.jsonStream`` / ``.json`` files (TimingData, TrackStatus, DriverList, ...).
For the race-oriented plot features this is wasteful: a single plot may need
lap times, positions, pit stops and track status, which is four full downloads
of the multi-megabyte TimingData stream.

``SessionDataStore`` resolves an event once and exposes lazily-cached, parsed
accessors for every stream. Each accessor caches in four tiers:

1. In-process dict (per store instance) — free repeats within one request.
2. Redis via :func:`get_sync_cache` (24h TTL) — shared across requests/workers.
   Skipped for the big compressed streams (``car_data`` / ``position_data``).
3. Durable GridFS via :mod:`src.repositories.raw_stream_cache` — parse-once,
   survives Redis expiry; written only once the session is complete.
4. Fetch + parse from livetiming — the slow path.

Network failures surface as :class:`UpstreamUnavailableError`; a missing
stream (HTTP 404) surfaces as :class:`DataNotAvailableError`, mirroring the
error-wrapping conventions in ``top_speed.py``.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Union

import requests

from src.core.cache.redis_cache import get_sync_cache, make_stream_key
from src.core.exceptions import (
    DataNotAvailableError,
    SessionNotFoundError,
    UpstreamUnavailableError,
)
from src.core.logging import get_logger
from src.ingestion.static_client import F1StaticClient
from src.repositories.raw_stream_cache import get_raw_stream, store_raw_stream
from src.repositories.session_cache import get_session_bundle, store_session_bundle
from src.services.analysis.v2._helpers import extract_stints_from_data, parse_f1_time
from src.services.analysis.v2._race_helpers import (
    _compute_best_sectors,
    _compute_lap_times,
    _compute_pit_stops,
    _compute_positions_by_lap,
    _compute_track_status_periods,
)

logger = get_logger(__name__)

# Long TTL: a completed session's streams never change.
_STREAM_TTL_SECONDS = 86400

# A session is treated as safely finished (and thus durably cacheable) once its
# start is this far in the past — enough to cover a full race plus red-flag
# stoppages and the cool-down before final data settles.
_SESSION_COMPLETE_AFTER_SECONDS = 4 * 3600


class SessionDataStore:
    """Cached accessor for one session's livetiming streams.

    Args:
        year: Season year.
        identifier: Round number, livetiming meeting Key, or official/event name
            — resolved the same way every other V2 module resolves ``gp``.
        session_name: Session label ("Race", "Qualifying", "FP1", ...).
        client: Optional shared :class:`F1StaticClient` (connection pooling).
    """

    def __init__(
        self,
        year: int,
        identifier: Union[int, str],
        session_name: str,
        client: Optional[F1StaticClient] = None,
    ) -> None:
        self.year = int(year)
        self.identifier = identifier
        self.session_name = session_name
        self.client = client or F1StaticClient()

        event_info = self.client.get_event_info(year, identifier)
        if not event_info:
            raise SessionNotFoundError(
                year=year, gp=identifier, session=session_name,
                reason=f"Could not resolve event for {year} identifier={identifier!r}",
            )

        self.event_name: str = event_info["name"]
        self.round_nr: int = event_info["round_nr"]
        self.official_name: str = event_info.get("official_name", "")
        self.event_key: Optional[int] = event_info.get("key")

        # Resolve the livetiming base URL by name. The curated round number can
        # drift from livetiming's positional meeting index (e.g. pre-season
        # testing shifts every round by one), so name matching is the reliable
        # path; get_event_session_url falls back to it internally.
        base_url = self.client.get_event_session_url(
            year, self.event_name, session_name, round_nr=self.round_nr
        )
        if not base_url:
            base_url = self.client.get_event_session_url(
                year, self.event_name, session_name
            )
        if not base_url:
            raise SessionNotFoundError(
                year=year, gp=identifier, session=session_name,
                reason=f"No livetiming session for {self.event_name} {session_name}",
            )
        self.base_url: str = base_url

        # Per-instance parsed-stream cache.
        self._cache: Dict[str, Any] = {}

        # Durable derived-data cache (MongoDB, completed sessions only). Holds
        # the small lap-indexed structures the race features consume so a warm
        # session never re-downloads or re-parses the multi-MB timing streams.
        self._derived_bundle: Optional[Dict[str, Any]] = None
        self._bundle_checked = False

    # ------------------------------------------------------------------
    # Fetch primitives
    # ------------------------------------------------------------------
    def _fetch_json(self, filename: str) -> Any:
        """GET a plain ``.json`` file, BOM-decoded."""
        url = self.base_url + filename
        try:
            resp = self.client.session.get(url, timeout=self.client.DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise UpstreamUnavailableError(
                source="livetiming", reason=f"Failed to fetch {url}: {exc}"
            ) from exc
        if resp.status_code == 404:
            raise DataNotAvailableError(
                year=self.year, gp=self.identifier, session=self.session_name,
                source="livetiming", reason=f"{filename} not published (404)",
            )
        if resp.status_code >= 500:
            raise UpstreamUnavailableError(
                source="livetiming",
                reason=f"livetiming returned {resp.status_code} for {url}",
            )
        try:
            resp.raise_for_status()
            return json.loads(resp.content.decode("utf-8-sig"))
        except (requests.HTTPError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise UpstreamUnavailableError(
                source="livetiming", reason=f"Bad response for {url}: {exc}"
            ) from exc

    def _fetch_stream(self, filename: str) -> List[Dict[str, Any]]:
        """GET and parse a ``.jsonStream`` file into a list of entries."""
        url = self.base_url + filename
        # Probe status first so 404 maps cleanly (parse_jsonstream_simple would
        # raise a generic HTTPError otherwise).
        try:
            resp = self.client.session.get(url, timeout=self.client.DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise UpstreamUnavailableError(
                source="livetiming", reason=f"Failed to fetch {url}: {exc}"
            ) from exc
        if resp.status_code == 404:
            raise DataNotAvailableError(
                year=self.year, gp=self.identifier, session=self.session_name,
                source="livetiming", reason=f"{filename} not published (404)",
            )
        if resp.status_code >= 500:
            raise UpstreamUnavailableError(
                source="livetiming",
                reason=f"livetiming returned {resp.status_code} for {url}",
            )
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise UpstreamUnavailableError(
                source="livetiming", reason=str(exc)
            ) from exc
        return self._parse_stream_content(resp.content)

    @staticmethod
    def _parse_stream_content(content: bytes) -> List[Dict[str, Any]]:
        """Parse raw ``.jsonStream`` bytes (``HH:MM:SS.mmm{json}`` per line)."""
        text = content.decode("utf-8-sig").replace("\r\n", "\n").strip()
        entries: List[Dict[str, Any]] = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            json_start = -1
            for idx, ch in enumerate(line):
                if ch in ("{", "["):
                    json_start = idx
                    break
            if json_start == -1:
                continue
            timestamp = line[:json_start].strip()
            try:
                obj = json.loads(line[json_start:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and timestamp:
                obj["_timestamp"] = timestamp
            entries.append(obj)
        return entries

    # ------------------------------------------------------------------
    # Tiered cache wrapper
    # ------------------------------------------------------------------
    def _cached(
        self, stream_name: str, loader: Callable[[], Any], big: bool = False
    ) -> Any:
        """In-process → Redis → durable GridFS → loader, backfilling faster tiers.

        ``big`` skips the Redis tier for the multi-MB compressed telemetry
        streams (``car_data`` / ``position_data``); they still use the durable
        GridFS tier. The durable write is completion-gated so a live/partial
        session is never persisted.
        """
        if stream_name in self._cache:
            return self._cache[stream_name]

        # Tier 2: Redis (small streams only).
        redis_key = None
        cache = None
        if not big:
            redis_key = make_stream_key(
                self.year, self.round_nr, self.session_name, stream_name
            )
            try:
                cache = get_sync_cache()
                if cache.enabled:
                    hit = cache.get_json(redis_key)
                    if hit is not None:
                        self._cache[stream_name] = hit
                        return hit
            except Exception as exc:  # Redis must fail open.
                logger.debug("Redis stream lookup skipped for %s: %s", stream_name, exc)
                cache = None

        # Tier 3: durable GridFS.
        durable = get_raw_stream(
            self.year, self.round_nr, self.session_name, stream_name
        )
        if durable is not None:
            self._cache[stream_name] = durable
            self._backfill_redis(cache, redis_key, durable)
            return durable

        # Tier 4: fetch + parse (slow path).
        data = loader()
        self._cache[stream_name] = data
        self._backfill_redis(cache, redis_key, data)
        # Persist durably once the session is safely finished. The in-process
        # cache is already populated above, so the is_session_complete() ->
        # session_info() lookup resolves without re-entering this loader.
        if self.is_session_complete():
            store_raw_stream(
                self.year, self.round_nr, self.session_name, stream_name, data
            )
        return data

    @staticmethod
    def _backfill_redis(cache: Any, redis_key: Optional[str], data: Any) -> None:
        """Write ``data`` back to Redis when a Redis-eligible key is available."""
        if cache is not None and redis_key is not None and getattr(cache, "enabled", False):
            try:
                cache.set_json(redis_key, data, ttl=_STREAM_TTL_SECONDS)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public stream accessors
    # ------------------------------------------------------------------
    def timing_data(self) -> List[Dict[str, Any]]:
        """Parsed TimingData.jsonStream entries."""
        return self._cached(
            "timing_data", lambda: self._fetch_stream("TimingData.jsonStream")
        )

    def timing_app_data(self) -> List[Dict[str, Any]]:
        """Parsed TimingAppData.jsonStream entries (stint / tyre data)."""
        return self._cached(
            "timing_app_data", lambda: self._fetch_stream("TimingAppData.jsonStream")
        )

    def track_status(self) -> List[Dict[str, Any]]:
        """Parsed TrackStatus.jsonStream entries."""
        return self._cached(
            "track_status", lambda: self._fetch_stream("TrackStatus.jsonStream")
        )

    def weather_data(self) -> List[Dict[str, Any]]:
        """Parsed WeatherData.jsonStream entries."""
        if self._derived_bundle is not None and "weather" in self._derived_bundle:
            return self._derived_bundle["weather"]
        return self._cached(
            "weather_data", lambda: self._fetch_stream("WeatherData.jsonStream")
        )

    def race_control(self) -> List[Dict[str, Any]]:
        """RaceControlMessages, normalized to a list of message dicts."""
        def _load() -> List[Dict[str, Any]]:
            payload = self._fetch_json("RaceControlMessages.json")
            msgs = payload.get("Messages") if isinstance(payload, dict) else payload
            if isinstance(msgs, dict):
                return list(msgs.values())
            return msgs if isinstance(msgs, list) else []

        return self._cached("race_control", _load)

    def driver_list(self) -> Dict[str, Dict[str, Any]]:
        """DriverList.json → ``{car_number: {tla, team, color, ...}}``."""
        if self._derived_bundle is not None and "driver_list" in self._derived_bundle:
            return self._derived_bundle["driver_list"]

        def _load() -> Dict[str, Dict[str, Any]]:
            payload = self._fetch_json("DriverList.json")
            result: Dict[str, Dict[str, Any]] = {}
            if not isinstance(payload, dict):
                return result
            for num, info in payload.items():
                if not isinstance(info, dict):
                    continue
                colour = info.get("TeamColour", "")
                if colour and not colour.startswith("#"):
                    colour = f"#{colour}"
                result[str(num)] = {
                    "tla": info.get("Tla", str(num)),
                    "team": info.get("TeamName", "Unknown"),
                    "color": colour,
                    "name": info.get("FullName", ""),
                    "line": info.get("Line"),
                    "racing_number": info.get("RacingNumber", str(num)),
                }
            return result

        return self._cached("driver_list", _load)

    def session_info(self) -> Dict[str, Any]:
        """SessionInfo.json payload."""
        return self._cached(
            "session_info", lambda: self._fetch_json("SessionInfo.json")
        )

    def car_data(self) -> List[Dict[str, Any]]:
        """Parsed CarData.z.jsonStream entries (per-driver telemetry channels).

        This is the largest stream — durably cached in GridFS, never in Redis.
        """
        return self._cached(
            "car_data",
            lambda: self.client.parse_compressed_stream(
                self.base_url + "CarData.z.jsonStream"
            ),
            big=True,
        )

    def position_data(self) -> List[Dict[str, Any]]:
        """Parsed Position.z.jsonStream entries (per-driver track positions).

        Large compressed stream — durably cached in GridFS, never in Redis.
        """
        return self._cached(
            "position_data",
            lambda: self.client.parse_compressed_stream(
                self.base_url + "Position.z.jsonStream"
            ),
            big=True,
        )

    # ------------------------------------------------------------------
    # Completion detection
    # ------------------------------------------------------------------
    def _session_start_utc(self) -> Optional[float]:
        """Session ``StartDate`` (+ ``GmtOffset``) as a POSIX UTC timestamp."""
        try:
            info = self.session_info()
        except (DataNotAvailableError, UpstreamUnavailableError):
            return None
        if not isinstance(info, dict):
            return None
        start_date = info.get("StartDate")
        if not start_date:
            return None
        try:
            local_dt = datetime.fromisoformat(str(start_date))
            offset_s = parse_f1_time(info.get("GmtOffset", "00:00:00"))
            return local_dt.timestamp() - offset_s
        except (ValueError, TypeError):
            return None

    def is_session_complete(self) -> bool:
        """True when the session is safely finished and thus durably cacheable.

        A past season is always complete. For the current season the session
        must have started more than :data:`_SESSION_COMPLETE_AFTER_SECONDS` ago;
        if the start can't be resolved, the safe default is *not* complete so a
        live/ongoing session is never persisted with partial data.
        """
        if self.year < datetime.utcnow().year:
            return True
        start = self._session_start_utc()
        if start is None:
            return False
        return (datetime.utcnow().timestamp() - start) > _SESSION_COMPLETE_AFTER_SECONDS

    # ------------------------------------------------------------------
    # Durable derived-data cache (MongoDB)
    # ------------------------------------------------------------------
    def _get_derived(self, key: str, compute: Callable[[], Any]) -> Any:
        """In-process → Mongo bundle → compute, for one derived structure.

        On the first derived access the durable bundle is consulted once. A hit
        serves every subsequent accessor with zero network I/O. A miss on a
        *completed* session builds the whole bundle once and persists it; on a
        live session it falls back to lazy per-key compute (today's behaviour).
        """
        if self._derived_bundle is not None and key in self._derived_bundle:
            return self._derived_bundle[key]

        if not self._bundle_checked:
            self._bundle_checked = True
            loaded = get_session_bundle(self.year, self.round_nr, self.session_name)
            if loaded is not None:
                self._derived_bundle = loaded
            elif self.is_session_complete():
                self._build_and_persist_bundle()
            if self._derived_bundle is not None and key in self._derived_bundle:
                return self._derived_bundle[key]

        if self._derived_bundle is None:
            self._derived_bundle = {}
        if key not in self._derived_bundle:
            self._derived_bundle[key] = compute()
        return self._derived_bundle[key]

    def _build_and_persist_bundle(self) -> None:
        """Compute every derived structure once and persist it to MongoDB.

        The partial bundle is exposed on ``self`` as it fills so that derived
        computations depending on earlier keys (e.g. track-status periods need
        lap times) reuse them instead of re-walking the timing stream. Missing
        optional streams are skipped rather than aborting the whole build.
        """
        bundle: Dict[str, Any] = {}
        self._derived_bundle = bundle

        def _try(key: str, fn: Callable[[], Any]) -> None:
            try:
                bundle[key] = fn()
            except (DataNotAvailableError, UpstreamUnavailableError) as exc:
                logger.debug("Skipping %s in session bundle build: %s", key, exc)

        # Order matters: dependencies first (driver_list before stints,
        # lap_times before track_status_periods).
        _try("driver_list", self.driver_list)
        _try("lap_times", lambda: _compute_lap_times(self))
        _try("positions_by_lap", lambda: _compute_positions_by_lap(self))
        _try("pit_stops", lambda: _compute_pit_stops(self))
        _try("best_sectors", lambda: _compute_best_sectors(self))
        _try("track_status_periods", lambda: _compute_track_status_periods(self))
        _try("stints", self._compute_stints)
        _try("weather", self.weather_data)

        # Only persist when the core timing-derived data resolved; a bundle
        # without lap times would just cache emptiness for a dataless session.
        if "lap_times" in bundle:
            store_session_bundle(
                self.year, self.round_nr, self.session_name, bundle,
                meta={"event_name": self.event_name},
            )

    def _compute_stints(self) -> Dict[str, List[Dict[str, Any]]]:
        """Per-driver stint records ``{car_number: [{compound, start_lap, ...}]}``."""
        app_data = self.timing_app_data()
        return {
            num: extract_stints_from_data(app_data, num)
            for num in self.driver_list()
        }

    # ------------------------------------------------------------------
    # Public derived accessors (durably cached)
    # ------------------------------------------------------------------
    def lap_times(self) -> Dict[str, List[Dict[str, Any]]]:
        """Per-driver completed-lap records."""
        return self._get_derived("lap_times", lambda: _compute_lap_times(self))

    def positions_by_lap(self) -> Dict[str, List[Dict[str, Any]]]:
        """Per-driver position snapshots aligned to the lap counter."""
        return self._get_derived(
            "positions_by_lap", lambda: _compute_positions_by_lap(self)
        )

    def pit_stops(self) -> Dict[str, List[Dict[str, Any]]]:
        """Per-driver pit stops."""
        return self._get_derived("pit_stops", lambda: _compute_pit_stops(self))

    def best_sectors(self) -> Dict[str, Dict[str, float]]:
        """Per-driver best sector times."""
        return self._get_derived("best_sectors", lambda: _compute_best_sectors(self))

    def track_status_periods(self) -> List[Dict[str, Any]]:
        """Contiguous track-status periods mapped onto lap ranges."""
        return self._get_derived(
            "track_status_periods", lambda: _compute_track_status_periods(self)
        )

    def stints(self) -> Dict[str, List[Dict[str, Any]]]:
        """Per-driver stint records."""
        return self._get_derived("stints", self._compute_stints)


__all__ = ["SessionDataStore"]
