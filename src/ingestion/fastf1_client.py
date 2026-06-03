import fastf1
from fastf1 import plotting

from src.core.exceptions import (
    DataNotAvailableError,
    SessionNotFoundError,
    UpstreamUnavailableError,
)
from src.core.logging import get_logger

logger = get_logger(__name__)

_FASTF1_SESSION_NAMES = {
    'SQ': 'Sprint Qualifying',
    'S': 'Sprint',
}

_NOT_AVAILABLE_HINTS = (
    'does not yet have data',
    'has not been loaded',
    'no laps data',
    'no data for this session',
    'session is not available',
    'has no data',
)

_NOT_FOUND_HINTS = (
    'does not exist',
    'no event named',
    'no schedule',
    'failed to load any schedule',
)


def _classify_fastf1_error(year, round_, event, exc: Exception):
    """Translate a FastF1/network exception into a typed T1API error."""
    msg = str(exc).lower()

    if any(h in msg for h in _NOT_FOUND_HINTS):
        return SessionNotFoundError(year=year, gp=round_, session=event, reason=str(exc))

    if any(h in msg for h in _NOT_AVAILABLE_HINTS):
        return DataNotAvailableError(
            year=year, gp=round_, session=event,
            source="fastf1", reason=str(exc),
        )

    # Network/HTTP failures from FastF1's underlying requests
    exc_name = type(exc).__name__.lower()
    if any(t in exc_name for t in ('connection', 'timeout', 'httperror', 'requestexception')):
        return UpstreamUnavailableError(source="fastf1", reason=str(exc))

    # FastF1's DataNotLoadedError / SessionNotAvailableError typically indicate
    # the session exists but data hasn't been published yet.
    if 'datanotloaded' in exc_name or 'sessionnotavailable' in exc_name:
        return DataNotAvailableError(
            year=year, gp=round_, session=event,
            source="fastf1", reason=str(exc),
        )

    return None


class SessionLoader:
    def __init__(self, year, round, event):
        self.year = year
        self.round = round
        self.event = event
        plotting.setup_mpl(misc_mpl_mods=False)
        fastf1.Cache.enable_cache('./cache')
        fastf1_event = _FASTF1_SESSION_NAMES.get(event, event)
        try:
            self.session = fastf1.get_session(self.year, self.round, fastf1_event)
            self.session.load()
        except (DataNotAvailableError, SessionNotFoundError, UpstreamUnavailableError):
            raise
        except Exception as exc:
            logger.exception(
                "FastF1 failed for year=%s round=%s event=%s", year, round, event
            )
            typed = _classify_fastf1_error(year, round, event, exc)
            if typed is not None:
                raise typed from exc
            # Unknown FastF1 failure — treat as data not available (safer than 500)
            raise DataNotAvailableError(
                year=year, gp=round, session=event,
                source="fastf1", reason=str(exc),
            ) from exc

    def get_session(self):
        return self.session
