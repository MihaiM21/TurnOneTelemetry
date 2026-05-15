"""
Custom exception hierarchy for T1API.

These are translated to HTTP responses by handlers registered in
`src/api/app.py`. Services and ingestion clients raise these instead
of swallowing errors or letting raw library exceptions bubble up.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional


class T1APIError(Exception):
    """Base class for all T1API-specific errors."""


class DataNotAvailableError(T1APIError):
    """
    The requested session is real, but no data has been published yet
    (e.g. FastF1 index lag, livetiming static feed not published).

    Distinct from SessionNotFoundError: the session exists in the schedule
    but neither upstream source can supply data right now. Clients should
    retry later.
    """

    def __init__(
        self,
        year: Optional[int] = None,
        gp: Any = None,
        session: Optional[str] = None,
        source: Optional[str] = None,
        sources_tried: Optional[Iterable[str]] = None,
        reason: str = "",
    ):
        self.year = year
        self.gp = gp
        self.session = session
        self.source = source
        self.sources_tried = list(sources_tried) if sources_tried else ([source] if source else [])
        self.reason = reason
        super().__init__(
            f"Data not available for year={year} gp={gp} session={session} "
            f"(sources_tried={self.sources_tried}): {reason}"
        )


class UpstreamUnavailableError(T1APIError):
    """An upstream dependency (FastF1, livetiming.formula1.com, etc.) failed."""

    def __init__(self, source: str, reason: str = ""):
        self.source = source
        self.reason = reason
        super().__init__(f"Upstream {source!r} unavailable: {reason}")


class SessionNotFoundError(T1APIError):
    """The requested session does not exist in the schedule at all."""

    def __init__(self, year: Optional[int] = None, gp: Any = None, session: Optional[str] = None, reason: str = ""):
        self.year = year
        self.gp = gp
        self.session = session
        self.reason = reason
        super().__init__(
            f"Session not found: year={year} gp={gp} session={session}: {reason}"
        )
