"""Reusable request-parameter dependencies for the analysis endpoints.

Two problems this solves.

**Validation.** ``session`` used to be declared as a bare ``str`` with no
pattern and no length bound, yet it is interpolated into a filesystem path by
every V2 analysis module (``outputs/plots/{year}/{event}/{session}``). A value
of ``../../..`` therefore escaped the output directory. ``settings.valid_sessions_list``
already existed to describe the legal values but was never used for validation
anywhere. These dependencies close that gap at the edge; the containment guard
in ``src/services/plotting/output.py`` closes it at the sink.

**Duplication.** ~100 endpoints re-declared identical ``year``/``gp``/``session``
``Query(...)`` triples, so the constraints drifted and Swagger documented them
inconsistently. Depending on ``SessionQuery`` gives one definition and one set
of docs.

These supersede the never-referenced ``SessionParams`` / ``DriverComparisonParams``
/ ``DriverSessionParams`` models in ``schemas/health.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union

from fastapi import Depends, HTTPException, Query, Request, status

from src.core.config import settings

#: Driver three-letter abbreviation, e.g. ``VER``. Also the safe alphabet for
#: the driver segment of a generated filename.
_TLA_RE = re.compile(r"^[A-Za-z]{3}$")

# Letters (any script: "São Paulo", "Montréal"), digits, space and . ' - only -- no path
# separators. ``\w`` also matches "_", which is harmless here.
_GP_NAME_RE = re.compile(r"^[\w .'\-]{1,80}$")

#: Livetiming meeting keys are four-digit numbers (1279 ...); anything this large is a
#: key, not a round. Rounds are 1..``settings.max_round``.
_MIN_MEETING_KEY = 100
_MAX_MEETING_KEY = 99999


def _reject(detail: str) -> None:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def validate_session(session: str) -> str:
    """Return ``session`` if it is a known session identifier, else 400.

    Membership in ``valid_sessions_list`` is the whole check: the list is a
    closed set of literals, so nothing containing a path separator, ``..`` or a
    NUL byte can pass. Matching is case-insensitive because callers send both
    ``Q`` and ``q``; the canonical spelling from the settings list is returned
    so downstream path building is deterministic.
    """
    if not session:
        _reject("session is required")
    for allowed in settings.valid_sessions_list:
        if session.strip().lower() == allowed.lower():
            return allowed
    _reject(
        f"Invalid session '{session}'. Valid values: "
        f"{', '.join(settings.valid_sessions_list)}"
    )


def validate_driver(driver: Optional[str]) -> Optional[str]:
    """Return the upper-cased TLA, or ``None``. Rejects anything else.

    ``driver`` is interpolated into generated filenames, so it must not carry
    separators or dots.
    """
    if driver is None or driver == "":
        return None
    if not _TLA_RE.match(driver.strip()):
        _reject(f"Invalid driver '{driver}'. Expected a 3-letter code such as 'VER'.")
    return driver.strip().upper()


def validate_gp(gp: Union[int, str]) -> Union[int, str]:
    """Round number, livetiming meeting key, or event name.

    Integers (and all-digit strings) are either an official round
    (``min_round..max_round``) or a meeting key (``100..99999``); anything in
    between is rejected. Strings are resolved upstream by
    ``F1StaticClient.get_event_info`` — which returns a server-controlled name —
    but are still constrained here so an unresolvable value fails fast and cannot
    reach a path builder if a future caller skips that resolution.
    """
    if isinstance(gp, bool):  # bool is an int subclass; reject explicitly.
        _reject("gp must be a round number or event name")
    numeric = gp if isinstance(gp, int) else (int(gp.strip()) if isinstance(gp, str) and gp.strip().isdigit() else None)
    if numeric is not None:
        is_round = settings.min_round <= numeric <= settings.max_round
        is_key = _MIN_MEETING_KEY <= numeric <= _MAX_MEETING_KEY
        if not (is_round or is_key):
            _reject(
                f"gp must be a round between {settings.min_round} and {settings.max_round}, "
                f"a meeting key, or an event name"
            )
        return gp
    text = str(gp).strip()
    if not _GP_NAME_RE.match(text):
        _reject(f"Invalid gp '{gp}'.")
    return text


@dataclass(frozen=True)
class SessionQuery:
    """The (year, gp, session) triple every analysis endpoint is addressed by."""

    year: int
    gp: Union[int, str]
    session: str


@dataclass(frozen=True)
class DriverQuery:
    """A session plus a single driver."""

    year: int
    gp: Union[int, str]
    session: str
    driver: Optional[str]


@dataclass(frozen=True)
class DriverPairQuery:
    """A session plus two drivers, for head-to-head comparisons."""

    year: int
    gp: Union[int, str]
    session: str
    driver1: str
    driver2: str


def session_query(
    year: int = Query(
        2025,
        ge=settings.min_year,
        le=settings.max_year,
        description="Season year.",
    ),
    gp: Union[int, str] = Query(
        1, description="Round number, event key, or official event name."
    ),
    session: str = Query(
        "Q",
        description="Session identifier, e.g. FP1, Q, SQ, S, R.",
    ),
) -> SessionQuery:
    return SessionQuery(year=year, gp=validate_gp(gp), session=validate_session(session))


def driver_query(
    base: SessionQuery = Depends(session_query),
    driver: Optional[str] = Query(
        None, description="Driver three-letter code, e.g. VER. Omit for all drivers."
    ),
) -> DriverQuery:
    return DriverQuery(
        year=base.year,
        gp=base.gp,
        session=base.session,
        driver=validate_driver(driver),
    )


def driver_pair_query(
    base: SessionQuery = Depends(session_query),
    driver1: str = Query(..., description="First driver's three-letter code."),
    driver2: str = Query(..., description="Second driver's three-letter code."),
) -> DriverPairQuery:
    d1 = validate_driver(driver1)
    d2 = validate_driver(driver2)
    if d1 is None or d2 is None:
        _reject("driver1 and driver2 are both required")
    return DriverPairQuery(
        year=base.year, gp=base.gp, session=base.session, driver1=d1, driver2=d2
    )


# ---------------------------------------------------------------------------
# Router-level guards
# ---------------------------------------------------------------------------
#
# The dataclass dependencies above are the clean way to declare parameters, but
# retrofitting them across ~100 existing endpoints would rewrite every
# signature -- and the query-parameter names are part of a frozen public
# contract. These guards give the same validation with zero signature churn:
# they read the raw query string, so they add no OpenAPI parameters and change
# no schema.


async def guard_session_params(request: Request) -> None:
    """Reject malformed ``session``/``driver`` query values with a 400.

    Without this, a bad ``session`` propagated all the way to the upstream
    session lookup and surfaced as an opaque **500**, even though it is plainly
    a client error. It is also the outer half of the path-traversal defence:
    ``session`` is interpolated into ``outputs/plots/{year}/{event}/{session}``,
    with :func:`src.services.plotting.output.resolve_within` as the inner half.
    """
    session = request.query_params.get("session")
    if session is not None and session != "":
        validate_session(session)

    # ``gp`` (and ``gp2`` on the cross-session comparison) is a round, a livetiming
    # meeting key or an event name. Most endpoints declare it as a bare
    # ``Union[int, str]``, so this is the one place it is checked everywhere.
    for name in ("gp", "gp2"):
        value = request.query_params.get(name)
        if value is not None and value != "":
            validate_gp(value)

    # Single-TLA parameters. The API spells this several ways across versions
    # (`driver`, `driver1`/`driver2`, and `d1`/`d2` on the comparison
    # endpoints); all of them are frozen public names, so validate each rather
    # than renaming.
    for name in ("driver", "driver1", "driver2", "d1", "d2"):
        value = request.query_params.get(name)
        if value:
            validate_driver(value)

    # `drivers` is a comma-separated TLA list.
    drivers = request.query_params.get("drivers")
    if drivers:
        for tla in drivers.split(","):
            if tla.strip():
                validate_driver(tla)

    # NOTE: `driver_name` is deliberately excluded -- it carries a full name
    # ("Max Verstappen"), not a TLA, so the 3-letter rule would reject valid
    # input.
