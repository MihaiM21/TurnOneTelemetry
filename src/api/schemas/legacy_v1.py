"""Documentation-only response schemas for the deprecated V1 (FastF1-backed) routers.

Per ``src/api/routers/analysis_v1.py`` / ``seasonal_v1.py`` these models are attached via
``responses={200: {"model": ...}}`` — **never** via ``response_model=`` — so they cannot
filter or otherwise alter the live payload (see the module docstring on
``src/api/schemas/common.py`` for why that distinction matters). Each model is derived from
the actual ``return`` statements of the ``src/services/analysis/v1/*`` function it
documents; nothing here is guessed, and V1 shapes are **not** assumed to match their V2
counterparts even when the two features carry the same name (e.g. V1 qualifying results
include a ``Team`` column V2 does not; V1's lap-times-distribution record spells the lap
number field ``lap_numbers``, singular value, where V2 spells it ``lap_number``).

A few payloads are genuinely dynamic (the ``/dashboard`` aggregate mirrors its V2 sibling's
per-session-type extra keys; the Mongo-backed season summary document has no fixed schema
in the code that writes it). Those use a permissive shape (``model_config = {"extra":
"allow"}``) rather than a guessed fixed schema — see the docstring on each for why.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, RootModel


# ---------------------------------------------------------------------------
# Dashboard / daily (Latest Session, General)
# ---------------------------------------------------------------------------

class LegacyDashboardResponse(BaseModel):
    """Latest finished session, routed by session category — from ``orchestrator.py``.

    Genuinely dynamic: the extra keys present depend on ``session_type``
    (``practice``/``qualifying``/``sprint_qualifying``/``sprint``/``race``), and each data
    block (``top_speed``, ``throttle_comparison``, ``qualifying_results`` for qualifying
    sessions) is *either* that feature's normal payload or ``{"error": "<message>"}`` if
    that block failed to generate. Only the four keys common to every session type are
    declared; the rest is left permissive. Identical shape to the V2 ``/dashboard``
    response — see ``src/api/schemas/analysis.py:DashboardResponse``.
    """

    session_type: str
    year: int
    round: int
    session_name: str

    model_config = {"extra": "allow"}


class LegacyDailyDataResponse(BaseModel):
    """Random daily "featured" plot data bundle — from ``src/workers/daily.py``.

    ``top_speed`` / ``throttle_comparison`` are each that feature's normal list payload
    (see :class:`LegacyTopSpeedEntry` / :class:`LegacyThrottleComparisonEntry`); left as
    ``List[Dict[str, Any]]`` here since the exact record shape is documented on those
    dedicated endpoints.
    """

    top_speed: List[Dict[str, Any]]
    throttle_comparison: List[Dict[str, Any]]
    date: str
    round: int
    event: str


class LegacyDailyStatsResponse(BaseModel):
    """Session-tracker analytics for a day or in aggregate — from ``SessionTracker``.

    The tracker's ``get_daily_stats`` / ``get_total_stats`` build their result from a
    SQLite aggregation whose exact column set is an internal analytics implementation
    detail, not a documented contract. Left permissive rather than guessed.
    """

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Simple Analysis
# ---------------------------------------------------------------------------

class LegacyTopSpeedEntry(BaseModel):
    """One team's top speed for a session (CarData telemetry) — from ``top_speed.py``."""

    Team: str
    top_speed_kmh: float = Field(alias="Top Speed (km/h)")
    Color: str = Field(description="Team hex color")

    model_config = {"populate_by_name": True}


LegacyTopSpeedDataResponse = RootModel[List[LegacyTopSpeedEntry]]


class LegacyThrottleComparisonEntry(BaseModel):
    """One driver's average throttle over their fastest lap — from ``throttle_comparison.py``."""

    Driver: str
    avg_throttle_pct: float = Field(alias="Average Throttle (%)")
    Color: str = Field(description="Driver hex color")

    model_config = {"populate_by_name": True}


LegacyThrottleComparisonDataResponse = RootModel[List[LegacyThrottleComparisonEntry]]


class LegacySpeedDistributionPoint(BaseModel):
    """One telemetry sample along a driver's fastest lap — from ``speed_distribution.py``."""

    time_s: float = Field(alias="Time (s)")
    speed_kmh: float = Field(alias="Speed (km/h)")
    Driver: str
    Color: str

    model_config = {"populate_by_name": True}


LegacySpeedDistributionDataResponse = RootModel[List[LegacySpeedDistributionPoint]]


class LegacyLaptimesEntry(BaseModel):
    """One completed lap for a driver — from ``laptimes_distribution.py``.

    Note the field is ``lap_numbers`` (plural), not ``lap_number``: the source builds a
    dict of parallel lists keyed ``lap_numbers`` and converts it with
    ``DataFrame.to_dict(orient='records')``, so each record still carries a single int
    under that plural key. The V2 sibling (``LaptimesDistributionEntry``) spells it
    ``lap_number`` — the two are not the same shape.
    """

    driver: str
    lap_times_formatted: Optional[str] = Field(
        default=None, description="mm:ss.sss, or null when the lap time itself was NaN."
    )
    lap_times_seconds: float
    lap_numbers: int = Field(description="1-based lap number; field name kept as stored (plural).")
    compound: str


LegacyLaptimesDataResponse = RootModel[List[LegacyLaptimesEntry]]


# ---------------------------------------------------------------------------
# Qualifying
# ---------------------------------------------------------------------------

class LegacyQualifyingResultEntry(BaseModel):
    """One driver's qualifying classification row — from ``qualifying_results.py``.

    Unlike the V2 equivalent, V1 includes ``Team`` directly on each row.
    """

    Driver: str
    Team: str
    LapTime: str = Field(description="Formatted lap time, e.g. '1:23.456'; pole shows its own time.")
    LapTimeDelta: float = Field(description="Gap to pole in seconds; 0.0 for the pole row.")
    Color: str = Field(description="Team hex color")


LegacyQualifyingResultsDataResponse = RootModel[List[LegacyQualifyingResultEntry]]


# ---------------------------------------------------------------------------
# Driver Comparison (2 drivers)
# ---------------------------------------------------------------------------

class LegacyTrackComparisonTelemetryPoint(BaseModel):
    """One merged telemetry sample for the minisector comparison — from ``track_comparison.py``."""

    x: float
    y: float
    distance: float
    speed: float
    driver: str
    minisector: int
    fastest_driver: str
    fastest_driver_int: int = Field(description="1 for driver1, 2 for driver2.")


class LegacyTrackComparisonSessionInfo(BaseModel):
    year: int
    race: int = Field(description="Round number, as passed to the endpoint.")
    event: str = Field(description="Session identifier, as passed to the endpoint (e.g. 'Q').")
    event_name: str
    session_name: str


class LegacyTrackComparisonResponse(BaseModel):
    """Minisector track comparison for two drivers — from ``track_comparison.py``."""

    driver1: str
    driver2: str
    driver1_color: str
    driver2_color: str
    telemetry: List[LegacyTrackComparisonTelemetryPoint]
    session_info: LegacyTrackComparisonSessionInfo


class LegacyThrottleBrakeTelemetryPoint(BaseModel):
    """One telemetry sample along a driver's fastest lap — from ``throttle_brake_comparison.py``."""

    distance: float
    speed: float
    throttle: float
    brake: float
    lap_time: float = Field(description="Seconds elapsed since the start of the lap.")
    driver: str


class LegacyThrottleBrakeSessionInfo(BaseModel):
    year: int
    race: str = Field(description="Event name (the source assigns the event name here, not the round).")
    event: str = Field(description="Session identifier, as passed to the endpoint (e.g. 'Q').")
    event_name: str
    session_name: str


class LegacyThrottleBrakeComparisonResponse(BaseModel):
    """Speed/throttle/brake vs distance for two drivers' fastest laps — from
    ``throttle_brake_comparison.py``."""

    driver1: str
    driver2: str
    driver1_color: str
    driver2_color: str
    telemetry: List[LegacyThrottleBrakeTelemetryPoint]
    session_info: LegacyThrottleBrakeSessionInfo


class LegacyLapTimeAnalysisTelemetryPoint(BaseModel):
    """One telemetry sample along a driver's fastest lap — from ``lap_time_analysis.py``."""

    distance: float
    speed: float
    throttle: float
    lap_time: float = Field(description="Seconds elapsed since the start of the lap.")
    driver: str


class LegacyLapTimeAnalysisDeltaPoint(BaseModel):
    """One delta-time sample, on driver1's distance axis."""

    distance: float
    delta: float = Field(description="Positive: driver2 is behind driver1 at this point.")


class LegacyLapTimeAnalysisSessionInfo(BaseModel):
    year: int
    event: str = Field(description="Session identifier, as passed to the endpoint (e.g. 'Q').")
    event_name: str
    session_name: str


class LegacyLapTimeAnalysisResponse(BaseModel):
    """Speed / delta-time / throttle comparison for two drivers' fastest laps — from
    ``lap_time_analysis.py``. Falls back to V2 (livetiming) when FastF1 lacks data."""

    driver1: str
    driver2: str
    driver1_color: str
    driver2_color: str
    driver1_laptime: Optional[float] = Field(default=None, description="Fastest lap time, seconds.")
    driver2_laptime: Optional[float] = Field(default=None, description="Fastest lap time, seconds.")
    reference_driver: str = Field(description="Always driver1; delta is computed on its distance axis.")
    telemetry: List[LegacyLapTimeAnalysisTelemetryPoint]
    delta: List[LegacyLapTimeAnalysisDeltaPoint]
    session_info: LegacyLapTimeAnalysisSessionInfo


# ---------------------------------------------------------------------------
# Pace Analysis
# ---------------------------------------------------------------------------

class LegacyDriverPaceEntry(BaseModel):
    """One driver's lap-time distribution for the session (107% quicklap filter) — from
    ``driver_pace.py``."""

    driver: str
    team: str
    color: str
    lap_times_seconds: List[float]
    lap_count: int
    min: float
    q1: float
    median: float
    q3: float
    max: float


LegacyDriverPaceDataResponse = RootModel[List[LegacyDriverPaceEntry]]


class LegacyTeamsPaceEntry(BaseModel):
    """One team's lap-time distribution for the session (both drivers aggregated) — from
    ``teams_pace.py``."""

    team: str
    color: str
    lap_times_seconds: List[float]
    lap_count: int
    min: float
    q1: float
    median: float
    q3: float
    max: float


LegacyTeamsPaceDataResponse = RootModel[List[LegacyTeamsPaceEntry]]


class LegacyTyreStintUsageEntry(BaseModel):
    """One driver's stint on one compound — from ``tyre_stint_usage.py``."""

    driver: str
    team: str
    position: int = Field(description="Finishing position, or 99 if unavailable.")
    stint_number: int
    compound: str
    start_lap: int
    end_lap: int
    lap_count: int
    tyre_life_end: Optional[int] = Field(default=None, description="Tyre age (laps) at the end of the stint.")
    color: str = Field(description="Compound hex color")


LegacyTyreStintUsageDataResponse = RootModel[List[LegacyTyreStintUsageEntry]]


# ---------------------------------------------------------------------------
# Seasonal Data (seasonal_v1.py) — MongoDB SeasonsDataManager documents
# ---------------------------------------------------------------------------

class LegacySeasonsListResponse(BaseModel):
    """Years with a stored season document in MongoDB — from ``seasonal_data.get_available_seasons``."""

    seasons: List[int]


class LegacySeasonSummaryResponse(BaseModel):
    """Complete season document (drivers, teams, races) — from
    ``SeasonsDataManager.get_season_data``.

    Genuinely dynamic: this is a raw MongoDB document (minus ``_id``) whose full field set
    is defined by whatever populated it (``src/repositories/populate_seasons.py``) and is
    not validated against a schema anywhere in the code. ``year``, ``drivers`` and ``teams``
    are the fields every other endpoint in this router reads back out of it; the rest is
    left permissive.
    """

    year: Optional[int] = None
    drivers: Optional[List[Dict[str, Any]]] = None
    teams: Optional[List[Dict[str, Any]]] = None

    model_config = {"extra": "allow"}


class LegacySeasonDriverEntry(BaseModel):
    """One driver record embedded in a season document.

    Fields per the ``seasonal_data.get_drivers_for_season`` docstring; stored as a plain
    Mongo sub-document so extra keys are passed through rather than dropped.
    """

    code: Optional[str] = None
    name: Optional[str] = None
    full_name: Optional[str] = None
    team: Optional[str] = None
    color: Optional[str] = None
    number: Optional[int] = None

    model_config = {"extra": "allow"}


class LegacySeasonDriversResponse(BaseModel):
    year: int
    drivers: List[LegacySeasonDriverEntry]


class LegacySeasonTeamEntry(BaseModel):
    """One team record embedded in a season document.

    Fields per the ``seasonal_data.get_teams_for_season`` docstring; stored as a plain
    Mongo sub-document so extra keys are passed through rather than dropped.
    """

    name: Optional[str] = None
    color: Optional[str] = None
    drivers: Optional[List[str]] = None

    model_config = {"extra": "allow"}


class LegacySeasonTeamsResponse(BaseModel):
    year: int
    teams: List[LegacySeasonTeamEntry]


#: `/season/{year}/driver/{driver_code}` returns the driver sub-document directly (unwrapped).
LegacyDriverInfoResponse = LegacySeasonDriverEntry

#: `/season/{year}/team/{team_name}` returns the team sub-document directly (unwrapped).
LegacyTeamInfoResponse = LegacySeasonTeamEntry
