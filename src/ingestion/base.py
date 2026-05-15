"""
Common interface for F1 session data sources.

Both `fastf1_client.SessionLoader` (V1) and `static_client.F1StaticClient` (V2)
should be usable through this Protocol so analyses can be written
data-source-agnostic in the future.

Today, V1 and V2 analysis modules each call their own client directly.
This Protocol marks the seam where unification will happen.
"""
from __future__ import annotations

from typing import Any, Protocol, Union, runtime_checkable


SessionIdentifier = Union[int, str]
"""Round number, event key, or official Grand Prix name."""


@runtime_checkable
class SessionDataSource(Protocol):
    """
    Protocol both data clients should satisfy.

    The shape is intentionally minimal — just enough to retrieve a session
    handle. Per-analysis methods (telemetry, lap times, etc.) live on the
    returned session object whose interface is currently source-specific.
    Future work: define typed DTOs for telemetry / lap_times / results that
    both sources produce.
    """

    def load_session(self, year: int, gp: SessionIdentifier, session: str) -> Any:
        """Return a session handle for the given (year, gp, session) tuple."""
        ...
