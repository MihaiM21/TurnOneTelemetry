"""Response schemas for the V2 seasonal endpoints (``src/api/routers/seasonal_v2.py``)
and the unauthenticated static-reference endpoints (``drivers_api.py``, ``teams_api.py``,
``circuits_api.py``).

These models are attached via ``responses={200: {"model": ...}}`` for documentation
only — never as a bare ``response_model=`` — since the handlers below return
hand-built dicts and a merely-incomplete model would silently filter live traffic.

Two of the models (``SeasonEventItem`` / ``EventSessionItem``) wrap fields lifted
verbatim from the official, externally-controlled formula1.com season index; that
upstream shape is not ours to pin down, so those two allow extra fields rather
than rejecting/dropping anything the upstream adds. The static-reference models
below (``DriverItem``, ``TeamItem``, ``CircuitSummaryItem``) wrap our own curated
JSON files (``src/domain/data/{drivers,teams}.json``, ``src/domain/data/circuits/``)
but still allow extra fields since those files are hand-maintained and grow ad hoc.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SeasonEventItem(BaseModel):
    """One meeting (Grand Prix weekend) from the official F1 season index."""

    name: Optional[str] = None
    official_name: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None
    key: Optional[int] = Field(default=None, description="Meeting key; use as `event_name` to list its sessions")
    code: Optional[str] = None

    model_config = {"extra": "allow"}


class SeasonEventsResponse(BaseModel):
    """All meetings for a season, fetched live from formula1.com (not cached)."""

    year: int
    events: List[SeasonEventItem]


class EventSessionItem(BaseModel):
    """One session (Practice, Qualifying, Race, ...) within a meeting."""

    name: Optional[str] = None
    type: Optional[str] = None
    number: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    path: Optional[str] = None
    key: Optional[int] = None

    model_config = {"extra": "allow"}


class EventSessionsResponse(BaseModel):
    """All sessions for one season event, fetched live from formula1.com."""

    year: int
    event_name: Optional[str] = Field(default=None, description="Full meeting name matched from `event_name`")
    event_key: Optional[int] = None
    sessions: List[EventSessionItem]


class TeammateBattleTeamItem(BaseModel):
    """One constructor's season-long teammate head-to-head."""

    team: str
    color: Optional[str] = None
    driver_a: str
    driver_b: str
    quali_h2h: List[int] = Field(description="[wins_a, wins_b] qualifying head-to-head across the season")
    race_h2h: List[int] = Field(description="[wins_a, wins_b] race head-to-head; DNFs excluded from either side")
    avg_quali_gap_s: Optional[float] = Field(
        default=None, description="Mean quali gap (driver_b - driver_a); positive means driver_a was faster"
    )
    rounds_counted: int = Field(description="Number of rounds contributing to avg_quali_gap_s")


class TeammateBattleResponse(BaseModel):
    """Season-long teammate battle scorecard, one row per constructor, alphabetical by team."""

    teams: List[TeammateBattleTeamItem]


class SeasonFormDriverItem(BaseModel):
    """One driver's rolling race/qualifying form across the season."""

    tla: str
    team: str
    color: str
    rounds: List[int] = Field(description="Round numbers with any race/quali result this season")
    finish: List[Optional[int]] = Field(description="Raw finishing position per round; null where the driver sat out")
    quali: List[Optional[int]] = Field(description="Raw qualifying position per round; null where absent")
    finish_rolling: List[Optional[float]] = Field(description="Rolling-average finish position over `window` races")
    quali_rolling: List[Optional[float]] = Field(description="Rolling-average qualifying position over `window`")


class SeasonFormResponse(BaseModel):
    """Rolling-average race/qualifying form guide for the selected (or default top-10) drivers."""

    window: int = Field(description="Rolling-average window size, in races")
    rounds: List[int]
    drivers: List[SeasonFormDriverItem]


class DriverRadarDriverItem(BaseModel):
    """One driver's scaled radar spokes plus the raw metric behind each spoke."""

    tla: str
    team: str
    color: str
    values: List[Optional[float]] = Field(description="0-100 scaled value per axis, aligned to `axes` order")
    raw: Dict[str, Optional[float]] = Field(description="Underlying raw metric for each axis key, before scaling")


class SeasonDriverRadarResponse(BaseModel):
    """Season or career driver performance radar; `scope` distinguishes the two.

    Shared by ``/seasons/{year}/driver-radar-data`` (``scope="season"``) and
    ``/career/driver-radar-data`` (``scope="career"``) — both endpoints return
    this same shape.
    """

    scope: str = Field(description="'season' or 'career'")
    axes: List[str] = Field(description="Axis display names, in the order `values` is aligned to")
    drivers: List[DriverRadarDriverItem]
    hero: bool = Field(description="True when exactly one driver is charted (single-driver hero layout)")


# ============================================================================
# Static reference data (src/api/routers/{drivers,teams,circuits}_api.py)
# ============================================================================

class DriverItem(BaseModel):
    """One driver entry from ``src/domain/data/drivers.json``."""

    code: Optional[str] = None
    name: Optional[str] = None
    full_name: Optional[str] = None
    team: Optional[str] = None
    color: Optional[str] = None
    number: Optional[int] = None

    model_config = {"extra": "allow"}


class SeasonDriversResponse(BaseModel):
    """All drivers for a season year."""

    drivers: List[DriverItem]


class DriverDetailResponse(BaseModel):
    """A single driver, matched by name/code/number.

    The field is named ``team`` in the live response even though it holds the
    driver record, not a team — that is existing handler behaviour and is
    documented as-is rather than changed.
    """

    team: DriverItem


class TeamItem(BaseModel):
    """One constructor entry from ``src/domain/data/teams.json``."""

    name: Optional[str] = None
    alt_name: Optional[str] = None
    short_name: Optional[str] = None
    color: Optional[str] = None
    drivers: Optional[List[str]] = Field(default=None, description="Driver codes for this team")

    model_config = {"extra": "allow"}


class SeasonTeamsResponse(BaseModel):
    """All constructors for a season year."""

    teams: List[TeamItem]


class TeamDetailResponse(BaseModel):
    """A single constructor, matched by name or short name."""

    team: TeamItem


class CircuitSummaryItem(BaseModel):
    """One circuit summary from ``src/domain/data/circuits/{year}/all_circuits.json``."""

    circuit_id: Optional[str] = None
    name: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    circuit_key: Optional[int] = None
    years_available: Optional[List[int]] = None

    model_config = {"extra": "allow"}


class YearlyCircuitsResponse(BaseModel):
    """All circuit summaries for a season year."""

    circuits: List[CircuitSummaryItem]


class CircuitInfoResponse(BaseModel):
    """A single circuit summary, matched by circuit ID."""

    circuit: CircuitSummaryItem


class CircuitDataResponse(BaseModel):
    """Full circuit layout (corners, rotation, marshal lights/sectors, track outline)
    in our own schema, as stored per-circuit under ``src/domain/data/circuits/{year}/``.

    Deliberately permissive: the layout schema is internal and evolves per circuit,
    so this does not pin down its keys — see
    ``src/ingestion/circuits_loader.py:get_circuit_data_file``.
    """

    data: Dict[str, Any]
