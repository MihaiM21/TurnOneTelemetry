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

from fastapi import Depends, HTTPException, Query, status

from src.core.config import settings

#: Driver three-letter abbreviation, e.g. ``VER``. Also the safe alphabet for
#: the driver segment of a generated filename.
_TLA_RE = re.compile(r"^[A-Za-z]{3}$")

_GP_NAME_RE = re.compile(r"^[A-Za-z0-9 ._'-]{1,80}$")


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
    """Round number, event key, or official event name.

    Integers are range-checked against the season bounds. Strings are resolved
    upstream by ``F1StaticClient.get_event_info`` — which returns a
    server-controlled name — but are still constrained here so an unresolvable
    value fails fast and cannot reach a path builder if a future caller skips
    that resolution.
    """
    if isinstance(gp, bool):  # bool is an int subclass; reject explicitly.
        _reject("gp must be a round number or event name")
    if isinstance(gp, int):
        if not (settings.min_round <= gp <= settings.max_round):
            _reject(
                f"gp round must be between {settings.min_round} and {settings.max_round}"
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
