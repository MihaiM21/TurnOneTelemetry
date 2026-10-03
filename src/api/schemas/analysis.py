"""Documentation-only response schemas for the V2 analysis router.

These models exist purely so Swagger can show a real shape for every
``/api/v2/...`` JSON endpoint. Per ``src/api/routers/analysis_v2.py`` they are
attached via ``responses={200: {"model": ...}}`` — **never** via
``response_model=`` — so they cannot filter or otherwise alter the live
payload. Each model is derived from the actual ``return`` statements of the
service function it documents (see the module docstring on each field's
origin below); nothing here is guessed.

A few payloads are genuinely dynamic (the ``/dashboard`` aggregate, the raw
weather sample embedded in ``lap-all-data``, and the ``circuit`` block shared
by ``track-map`` / ``corner-duel``). Those use a permissive shape
(``model_config = {"extra": "allow"}`` or ``Dict[str, Any]``) rather than a
guessed fixed schema — see the docstring on each for why.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, RootModel


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

class SessionInfo(BaseModel):
    """Session identity echoed back inside several payloads."""

    year: int
    event_name: str
    session_name: str


class TrackStatusPeriod(BaseModel):
    """One contiguous track-status window (GREEN/YELLOW/SC/VSC/RED).

    ``end_lap``/``end_time_s`` are ``None`` when the status was still active
    when the session ended.
    """

    status: str
    start_lap: Optional[int] = None
    end_lap: Optional[int] = None
    start_time_s: Optional[float] = None
    end_time_s: Optional[float] = None


class LapGapPoint(BaseModel):
    """One lap's gap-to-leader sample. ``gap_s`` is ``None`` on a red-flag lap
    (the plotted line breaks there rather than drawing a false segment)."""

    lap: int
    gap_s: Optional[float] = None


# ---------------------------------------------------------------------------
# Dashboard (latest session, all session types)
# ---------------------------------------------------------------------------

class DashboardResponse(BaseModel):
    """Latest finished session, routed by session category.

    Genuinely dynamic: the extra keys present depend on ``session_type``
    (``practice``/``qualifying``/``sprint_qualifying``/``sprint``/``race`` —
    see ``src/services/orchestrator.py``), and each data block
    (``top_speed``, ``throttle_comparison``, and ``qualifying_results`` for
    qualifying sessions) is *either* that feature's normal payload *or*
    ``{"error": "<message>"}`` if that block failed to generate. A fixed
    schema would misrepresent this, so only the four keys common to every
    session type are declared and the rest is left permissive.
    """

    session_type: str
    year: int
    round: int
    session_name: str

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Simple Analysis
# ---------------------------------------------------------------------------

class TopSpeedEntry(BaseModel):
    """One team's top speed for a session — from ``top_speed.py``.

    Shared shape for both the CarData-telemetry source and the official
    Speed Trap source; only the underlying data source differs.
    """

    Team: str
    top_speed_kmh: float = Field(alias="Top Speed (km/h)")
    Color: str = Field(description="Team hex color")

    model_config = {"populate_by_name": True}


TopSpeedTelemetryDataResponse = RootModel[List[TopSpeedEntry]]
TopSpeedSpeedTrapDataResponse = RootModel[List[TopSpeedEntry]]


class ThrottleComparisonEntry(BaseModel):
    """One driver's average throttle over their fastest lap — from ``throttle_comparison.py``."""

    Driver: str
    avg_throttle_pct: float = Field(alias="Average Throttle (%)")
    Color: str = Field(description="Driver hex color")

    model_config = {"populate_by_name": True}


ThrottleComparisonDataResponse = RootModel[List[ThrottleComparisonEntry]]


class SpeedDistributionPoint(BaseModel):
    """One telemetry sample along a driver's fastest lap — from ``speed_distribution.py``."""

    time_s: float = Field(alias="Time (s)")
    speed_kmh: float = Field(alias="Speed (km/h)")
    Driver: str
    Color: str

    model_config = {"populate_by_name": True}


SpeedDistributionDataResponse = RootModel[List[SpeedDistributionPoint]]


class LapTimesDistributionEntry(BaseModel):
    """One completed lap for a driver — from ``laptimes_distribution.py``."""

    driver: str
    lap_number: int
    lap_times_formatted: str
    lap_times_seconds: float
    compound: str = Field(description="Tyre compound active on this lap, or UNKNOWN")


LaptimesDistributionDataResponse = RootModel[List[LapTimesDistributionEntry]]


# ---------------------------------------------------------------------------
# Qualifying
# ---------------------------------------------------------------------------

class QualifyingResultEntry(BaseModel):
    """One driver's qualifying classification row — from ``qualifying_results.py``."""

    Driver: str
    Team: str
    LapTime: str = Field(description="Formatted lap time, e.g. '1:32.456'")
    LapTimeDelta: float = Field(description="Gap to pole in seconds; 0.0 for the pole-sitter")
    Color: str


QualifyingResultsDataResponse = RootModel[List[QualifyingResultEntry]]


class ThrottleBrakeTelemetryPoint(BaseModel):
    """One telemetry sample from ``throttle_brake_comparison.py``."""

    distance: float
    speed: float
    throttle: float
    brake: float
    lap_time: float = Field(description="Elapsed time since the start of the lap, in seconds")
    driver: str


class ThrottleBrakeComparisonResponse(BaseModel):
    """Speed/Throttle/Brake vs distance for two drivers' fastest laps."""

    driver1: str
    driver2: str
    driver1_color: str
    driver2_color: str
    telemetry: List[ThrottleBrakeTelemetryPoint] = Field(
        description="Interleaved samples for both drivers; filter by `driver` to split"
    )


class TrackComparisonTelemetryPoint(BaseModel):
    """One telemetry sample from ``track_comparison.py``, tagged with its minisector winner."""

    x: float
    y: float
    distance: float
    speed: float
    driver: str
    minisector: int
    fastest_driver: str = Field(description="TLA of whichever driver was fastest through this minisector")
    fastest_driver_int: int = Field(description="1 or 2 (driver1/driver2 index), 0 if undetermined")


class TrackComparisonResponse(BaseModel):
    """Track map data with per-minisector fastest-driver assignment for two drivers."""

    driver1: str
    driver2: str
    driver1_color: str
    driver2_color: str
    telemetry: List[TrackComparisonTelemetryPoint]
    session_info: SessionInfo


class LapTimeAnalysisTelemetryPoint(BaseModel):
    """One telemetry sample from ``lap_time_analysis.py``."""

    distance: float
    speed: float
    throttle: float
    lap_time: float = Field(description="Elapsed time since the start of the lap, in seconds")
    driver: str


class LapTimeAnalysisDeltaPoint(BaseModel):
    distance: float
    delta: float = Field(description="Gap of driver2 vs driver1 at this distance; positive = driver2 behind")


class LapTimeAnalysisResponse(BaseModel):
    """Speed/Throttle telemetry plus a delta-time series for two drivers' fastest laps."""

    driver1: str
    driver2: str
    driver1_color: str
    driver2_color: str
    driver1_laptime: float = Field(description="driver1's fastest lap time in seconds")
    driver2_laptime: float = Field(description="driver2's fastest lap time in seconds")
    reference_driver: str = Field(description="Always driver1 — the distance/delta reference lap")
    telemetry: List[LapTimeAnalysisTelemetryPoint]
    delta: List[LapTimeAnalysisDeltaPoint]
    session_info: SessionInfo


class TheoreticalBestEntry(BaseModel):
    """Theoretical vs actual best lap for one driver — from ``theoretical_best.py``.

    ``theoretical_s`` is the sum of the driver's best individual sector times;
    ``delta_s`` is clamped at 0.0 (sector-time rounding noise can otherwise put
    the sum a few ms above the actual lap).
    """

    driver: str
    team: str
    color: str
    theoretical_s: float
    actual_s: float
    delta_s: float = Field(description="Time left on the table: actual_s - theoretical_s, clamped >= 0")


TheoreticalBestDataResponse = RootModel[List[TheoreticalBestEntry]]


# ---------------------------------------------------------------------------
# Pace Analysis
# ---------------------------------------------------------------------------

class DriverPaceEntry(BaseModel):
    """One driver's lap-time distribution (107%-quicklap filtered) — from ``driver_pace.py``."""

    driver: str
    team: str
    color: str
    lap_times_seconds: List[float] = Field(description="Every filtered clean lap time, in seconds")
    lap_count: int
    min: float
    q1: float
    median: float
    q3: float
    max: float


DriverPaceDataResponse = RootModel[List[DriverPaceEntry]]


class TeamsPaceEntry(BaseModel):
    """One team's lap-time distribution (both cars, 107%-quicklap filtered) — from ``teams_pace.py``."""

    team: str
    color: str
    lap_times_seconds: List[float]
    lap_count: int
    min: float
    q1: float
    median: float
    q3: float
    max: float


TeamsPaceDataResponse = RootModel[List[TeamsPaceEntry]]


# ---------------------------------------------------------------------------
# Race Analysis
# ---------------------------------------------------------------------------

class TyreStintUsageEntry(BaseModel):
    """One stint for one driver — from ``tyre_stint_usage.py``."""

    driver: str
    team: str
    position: int = Field(description="Finishing position; 99 when unknown")
    stint_number: int
    compound: str
    start_lap: int
    end_lap: int
    lap_count: int
    tyre_life_end: Optional[int] = Field(default=None, description="Tyre age (laps) at the end of the stint")
    color: str


TyreStintUsageDataResponse = RootModel[List[TyreStintUsageEntry]]


class LapPositionPoint(BaseModel):
    """One lap's race position. ``lap`` 0 = starting grid."""

    lap: int
    position: int


class PositionChangesEntry(BaseModel):
    """One driver's position trace across the race — from ``position_changes.py``. Race/Sprint only."""

    driver: str
    team: str
    color: str
    start_pos: int
    end_pos: int
    positions: List[LapPositionPoint]


PositionChangesDataResponse = RootModel[List[PositionChangesEntry]]


class RaceGapsEntry(BaseModel):
    """One driver's per-lap gap series — from ``race_gaps.py``. Race/Sprint only.

    ``reference=leader``: gap to the per-lap leader (>= 0, leader = 0).
    ``reference=average``: signed gap to a constant-pace reference (positive =
    ahead of reference pace).
    """

    driver: str
    team: str
    color: str
    laps: List[LapGapPoint]


RaceGapsDataResponse = RootModel[List[RaceGapsEntry]]


class TyreDegradationPoint(BaseModel):
    driver: str
    tyre_age: int = Field(description="Laps on this set of tyres")
    lap_time_s: float
    fuel_corrected_s: Optional[float] = Field(
        default=None, description="Only present when fuel_corrected=true was requested"
    )


class TyreDegradationCompound(BaseModel):
    """One compound's degradation points and linear fit — from ``tyre_degradation.py``. Race/Sprint only."""

    compound: str
    color: str
    points: List[TyreDegradationPoint]
    deg_rate_s_per_lap: Optional[float] = Field(
        default=None, description="Linear-fit slope (s/lap); None if too few clean laps to fit"
    )
    r_squared: Optional[float] = None
    n_points: int


TyreDegradationDataResponse = RootModel[List[TyreDegradationCompound]]


class PitStopEntry(BaseModel):
    """One pit stop — from ``pit_strategy.py``."""

    driver: str
    team: str
    lap: int
    stop_n: int
    pit_lane_time_s: Optional[float] = None
    compound_in: Optional[str] = Field(default=None, description="Tyre removed")
    compound_out: Optional[str] = Field(default=None, description="Tyre fitted")
    under_sc: bool = Field(description="Whether the stop happened under Safety Car / VSC")
    drive_through: bool = Field(description="Pit-lane increment with no compound change (drive-through/red-flag)")


class UndercutEntry(BaseModel):
    """One detected undercut attempt — from ``pit_strategy.py``.

    ``gain_s`` is signed so positive means the attacker gained (closed the gap
    or jumped ahead); ``worked`` means the attacker ends up ahead after both
    pit cycles complete.
    """

    attacker: str
    defender: str
    lap: int = Field(description="Lap the attacker pitted on")
    gap_before_s: float
    gap_after_s: float
    gain_s: float
    worked: bool


class FastestStop(BaseModel):
    driver: str
    lap: int
    pit_lane_time_s: float


class TeamAvgStop(BaseModel):
    team: str
    avg_pit_lane_time_s: float
    n_stops: int


class PitStrategySummary(BaseModel):
    fastest_stop: Optional[FastestStop] = None
    avg_stop_by_team: List[TeamAvgStop]


class FreeChangeEntry(BaseModel):
    """A compound change not backed by a measured pit stop (e.g. a red-flag tyre change)."""

    driver: str
    lap: int
    compound_in: str
    compound_out: str


class PitStrategyResponse(BaseModel):
    """Full pit strategy payload — stops, undercuts, summary, free changes. Race/Sprint only."""

    stops: List[PitStopEntry]
    undercuts: List[UndercutEntry]
    summary: PitStrategySummary
    free_changes: List[FreeChangeEntry]


class WeatherSample(BaseModel):
    """One WeatherData sample — from ``session_weather.py``.

    ``lap`` is only populated for Race/Sprint sessions (a single well-ordered
    lap counter); it is ``null`` for Q/SQ/FP sessions where each car runs its
    own lap count.
    """

    time_s: float
    lap: Optional[int] = None
    air_temp: Optional[float] = None
    track_temp: Optional[float] = None
    humidity: Optional[float] = None
    rainfall: int = Field(description="1 if raining, else 0")
    wind_speed: Optional[float] = None
    wind_dir: Optional[float] = None


class RaceControlMessageEntry(BaseModel):
    time_s: Optional[float] = None
    lap: Optional[int] = None
    category: Optional[str] = None
    flag: Optional[str] = None
    message: str


class SessionWeatherResponse(BaseModel):
    """Weather series + track-status periods + race-control messages. Any session type."""

    weather: List[WeatherSample]
    track_status_periods: List[TrackStatusPeriod]
    race_control: List[RaceControlMessageEntry]


class RacePaceHeatmapResponse(BaseModel):
    """Driver x lap grid of delta to field median lap time — from ``race_pace_heatmap.py``. Race/Sprint only."""

    drivers: List[str] = Field(description="TLAs in finish order (row order, winner first)")
    laps: List[int] = Field(description="Sorted lap numbers forming the grid columns")
    grid: Dict[str, List[Optional[float]]] = Field(
        description="{tla: [delta_s_or_null, ...]} aligned to `laps`; null marks a masked/missing cell"
    )
    pit_laps: Dict[str, List[int]] = Field(description="{tla: [lap, ...]} laps where the driver pitted")
    sc_laps: List[int] = Field(description="Laps masked out for SC/VSC/RED track status")


class RunningBestPoint(BaseModel):
    minute: float = Field(description="Minutes since the first recorded lap timestamp")
    best_s: float = Field(description="Running-minimum lap time so far, in seconds")


class TrackTempPoint(BaseModel):
    minute: float
    track_temp: float


class TrackEvolutionResponse(BaseModel):
    """Session-best lap time evolution vs track temperature. Practice/Qualifying only."""

    overall: List[RunningBestPoint] = Field(description="Whole-session running-best step series")
    drivers: Dict[str, List[RunningBestPoint]] = Field(
        description="Per-driver running-best series for the plotted drivers (TLA keys)"
    )
    weather: List[TrackTempPoint] = Field(description="Empty if the WeatherData stream is unavailable")


class RaceStoryPitStop(BaseModel):
    lap: int
    compound: Optional[str] = Field(default=None, description="Compound fitted at this stop")


class RaceStoryDriverEntry(BaseModel):
    driver: str
    team: str
    color: str
    finish_rank: int
    laps: List[LapGapPoint] = Field(description="Gap-to-leader series, same convention as race-gaps (leader mode)")
    pit_stops: List[RaceStoryPitStop]
    last_lap: Optional[int] = None


class KeyMoment(BaseModel):
    """One numbered, captioned event on the race-story timeline."""

    lap: Optional[int] = None
    kind: str = Field(description="'lead_change', 'retirement', or 'penalty'")
    caption: str
    n: int = Field(description="1-based display number matching the plot's annotation chip")


class RaceStoryResponse(BaseModel):
    """Gap-to-leader traces, pit stops, and key moments for the top finishers. Race/Sprint only."""

    drivers: List[RaceStoryDriverEntry] = Field(description="Top ~10 finishers")
    key_moments: List[KeyMoment]
    track_status_periods: List[TrackStatusPeriod]


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

class TrackMapPoint(BaseModel):
    distance: float
    x: float
    y: float
    speed: Optional[float] = None
    gear: Optional[int] = None
    brake: Optional[float] = None


class BrakingSegment(BaseModel):
    """One contiguous braking zone (positional indices into `points`)."""

    start_idx: int
    end_idx: int
    start_distance: float
    end_distance: float


class TrackMapCallout(BaseModel):
    distance: float
    speed: float
    x: Optional[float] = None
    y: Optional[float] = None


class TrackMapCallouts(BaseModel):
    top_speed: Optional[TrackMapCallout] = None
    slowest_corner: Optional[TrackMapCallout] = None


class TrackMapResponse(BaseModel):
    """Fastest-lap telemetry track map for one driver — from ``telemetry_track_map.py``. Any session.

    ``circuit`` is the full ``CircuitLayout`` lookup (rotation, corners, track
    outline) when a matching circuit JSON was found, else ``null``; its shape
    comes from a separately-owned circuit-data file and is intentionally left
    permissive here rather than modeled field-by-field.
    """

    driver: str
    color_by: str = Field(description="'speed' or 'gear'")
    lap_time_s: float
    points: List[TrackMapPoint]
    braking_segments: List[BrakingSegment]
    callouts: TrackMapCallouts
    circuit: Optional[Dict[str, Any]] = None
    session_info: SessionInfo


class LapAllDataDriver(BaseModel):
    tla: str
    car_number: str
    name: str
    team: str
    color: str
    racing_number: str


class LapAllDataLap(BaseModel):
    number: int
    lap_time_s: float
    timestamp_s: float
    pit_in: bool
    pit_out: bool
    position: Optional[int] = None


class LapAllDataTyre(BaseModel):
    compound: Optional[str] = None
    stint_number: Optional[int] = None
    tyre_life_end: Optional[int] = None
    start_lap: Optional[int] = None
    end_lap: Optional[int] = None


class LapAllDataSectors(BaseModel):
    s1: Optional[float] = None
    s2: Optional[float] = None
    s3: Optional[float] = None


class LapAllDataTelemetryPoint(BaseModel):
    time: float
    distance: float
    speed: Optional[float] = None
    rpm: Optional[float] = None
    throttle: Optional[float] = None
    brake: Optional[float] = None
    gear: Optional[int] = None
    drs: Optional[int] = None
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None


class LapAllDataTrackStatus(BaseModel):
    status: Optional[str] = None
    start_lap: Optional[int] = None
    end_lap: Optional[int] = None


class LapAllDataResponse(BaseModel):
    """Everything for one driver's single lap — from ``lap_all_data.py``. Any session.

    ``weather`` is the raw nearest ``WeatherData`` stream entry (untouched
    upstream keys such as ``AirTemp``/``TrackTemp``/``Humidity`` as strings),
    unlike the normalized ``WeatherSample`` used by ``session-weather-data``;
    it is left as a permissive object rather than modeled field-by-field, and
    is ``null`` if no weather sample was available.
    """

    driver: LapAllDataDriver
    lap: LapAllDataLap
    tyre: LapAllDataTyre
    sectors: LapAllDataSectors
    weather: Optional[Dict[str, Any]] = None
    track_status: List[LapAllDataTrackStatus]
    telemetry: List[LapAllDataTelemetryPoint]
    session_info: SessionInfo


class CornerDuelDeltaPoint(BaseModel):
    distance: float
    delta_s: float = Field(description="t2(d) - t1(d); positive = driver2 behind driver1 at this point")


class CornerDuelSpeedPoint(BaseModel):
    dist_m: float
    speed_kmh: float


class CornerDuelCorner(BaseModel):
    """One corner's comparative data — from ``corner_duel.py``."""

    number: int = Field(description="Circuit corner number when matched, else sequential track order")
    apex_distance_m: float
    min_speed_kmh: Dict[str, Optional[float]] = Field(description="{driver_tla: min_speed_kmh}")
    braking_point_m: Dict[str, Optional[float]] = Field(
        description="{driver_tla: braking_point_m}; currently only driver1 is populated"
    )
    delta_gain_s: Optional[float] = Field(
        default=None, description="Positive = driver2 gained through this corner relative to driver1"
    )
    beneficiary: Optional[str] = Field(default=None, description="TLA of whoever gained, or null if undetermined")


class CornerDuelResponse(BaseModel):
    """Corner-by-corner duel: delta series, apex speeds, braking points, delta gain. Any session."""

    driver1: str
    driver2: str
    driver1_color: str
    driver2_color: str
    same_team: bool
    delta_series: List[CornerDuelDeltaPoint]
    speed_series: Dict[str, List[CornerDuelSpeedPoint]] = Field(description="{driver_tla: [speed samples]}")
    corners: List[CornerDuelCorner]
    session_info: SessionInfo


class LapDuelSide(BaseModel):
    """One side of a Lap Duel — from ``lap_duel._side_payload``.

    The core (``driverCode``, ``lapTime``, ``speed``, ``throttle``, ``brake``) matches the Remotion
    ``telemetry-compare`` schema. Every series has one value per entry of the response's ``distance`` array.
    """

    driverCode: str
    team: Optional[str] = None
    color: str = Field(description="Hex colour; side b is lightened when both drivers share a team colour")
    racingNumber: Optional[str] = None
    lapTime: str = Field(description="Formatted lap time, e.g. '1:42.526'")
    lap_time_s: float
    lap_number: Optional[int] = Field(default=None, description="Null when the lap has no matching lap number")
    segment: Optional[str] = Field(default=None, description="Q1|Q2|Q3 when the lap was picked by qualifying part")
    selection: str = Field(description="How the lap was chosen: 'fastest', 'lap' or 'segment'")
    year: int
    event_name: str
    session: str
    length_m: float = Field(description="This lap's own integrated distance (the shared axis uses side a's)")
    speed: List[float] = Field(description="km/h")
    throttle: List[float] = Field(description="0..1")
    brake: List[int] = Field(description="0 or 1")
    gear: List[int]
    rpm: List[int]
    drs: List[int] = Field(description="Raw DRS channel value (>= 10 means open)")


class LapDuelCorner(BaseModel):
    number: int
    distance_m: float


class LapDuelApex(BaseModel):
    distance_m: float
    min_speed_a: float
    min_speed_b: float
    braking_point_a_m: Optional[float] = None
    braking_point_b_m: Optional[float] = None
    corner: Optional[int] = Field(default=None, description="Nearest circuit corner within 120 m, else null")


class LapDuelSection(BaseModel):
    """A stretch of the lap (one corner, a merged chicane, or a straight) and who gained through it."""

    start_m: float
    end_m: float
    corners: List[int]
    delta_change_s: float = Field(description="Change in delta across the section; negative = b gained on a")
    label: str = Field(description="'T5', 'T8-T12' or 'straight'")
    gainer: str = Field(description="'a' or 'b'")


class LapDuelTrack(BaseModel):
    """Downsampled racing line of side a; ``faster`` is per point ('a' or 'b'). Null when there is no position data."""

    x: List[float] = Field(description="Raw position units (tenths of a metre)")
    y: List[float]
    faster: List[str]
    rotation: Optional[float] = Field(default=None, description="Degrees to rotate so the pit straight is horizontal")


class LapDuelSwing(BaseModel):
    where: str
    start_m: float
    end_m: float
    gainer: str = Field(description="Driver TLA")
    seconds: float


class LapDuelBrakingDelta(BaseModel):
    corner: Optional[int] = None
    distance_m: float
    later_braker: str = Field(description="Driver TLA")
    metres: float


class LapDuelHighlights(BaseModel):
    """Numbers worth quoting in a post; none of it is drawn as text on the plot."""

    gap_s: float = Field(description="lap_time_b - lap_time_a; positive = driver2 slower")
    faster: str = Field(description="Driver TLA")
    biggest_swings: List[LapDuelSwing]
    top_speed_kmh: Dict[str, float] = Field(description="{driver_tla: km/h}")
    top_speed_at_m: Dict[str, float] = Field(description="{driver_tla: metres}")
    slowest_apex: Optional[LapDuelApex] = None
    full_throttle_pct: Dict[str, float] = Field(description="{driver_tla: % of the lap at >= 98% throttle}")
    braking_deltas_m: List[LapDuelBrakingDelta]


class LapDuelAccelerationSide(BaseModel):
    long_g: List[Optional[float]]
    lat_g: List[Optional[float]] = Field(description="Left turns positive; null values when there is no position data")


class LapDuelAccelerations(BaseModel):
    """Only present with ``detail=full``; derived from ~4 Hz telemetry, clamped to +-6 g. Indicative only."""

    a: LapDuelAccelerationSide
    b: LapDuelAccelerationSide
    note: str


class LapDuelMeta(BaseModel):
    delta_convention: str
    distance_basis: str
    source: str


class LapDuelResponse(BaseModel):
    """Lap Duel: two laps on one distance grid — from ``lap_duel.py``. Any session.

    ``delta = t_b - t_a``; positive means side b (driver2) is behind side a (driver1). All series, ``distance``
    and ``delta`` have the same length. ``accelerations`` appears only when ``detail=full``.
    """

    a: LapDuelSide
    b: LapDuelSide
    distance: List[float] = Field(description="Metres of lap a; both laps are aligned by lap fraction")
    delta: List[float] = Field(description="Seconds, t_b - t_a; the last value is the lap-time difference")
    corners: List[LapDuelCorner]
    apexes: List[LapDuelApex]
    sections: List[LapDuelSection]
    track: Optional[LapDuelTrack] = None
    highlights: LapDuelHighlights
    detail: str = Field(description="'standard' or 'full'")
    same_session: bool
    same_team: bool
    session_info: SessionInfo
    meta: LapDuelMeta
    accelerations: Optional[LapDuelAccelerations] = None


class DriverRadarEntry(BaseModel):
    tla: str
    team: str
    color: str
    values: List[Optional[float]] = Field(
        description="0-100 scaled score per axis, aligned to the response's `axes` list; null = missing spoke"
    )
    raw: Dict[str, Optional[float]] = Field(description="Unscaled metric value per axis key")


class DriverRadarResponse(BaseModel):
    """Single-session driver performance radar — from ``driver_radar.py``. Any session."""

    scope: str = Field(description="'session' for this endpoint")
    axes: List[str] = Field(description="Axis display names, in the order `values`/`raw` are aligned to")
    drivers: List[DriverRadarEntry]
    hero: bool = Field(description="True when exactly one driver is charted")


# ---------------------------------------------------------------------------
# Energy clipping (2026+ power units)
# ---------------------------------------------------------------------------

class EnergyClippingZone(BaseModel):
    """One stretch where speed fell at full throttle — from ``_clipping_core.detect_clipping``."""

    start_m: float
    end_m: float
    length_m: float
    speed_in_kmh: float = Field(description="Speed when it started falling")
    speed_out_kmh: float = Field(description="Lowest speed before the driver lifted or braked")
    kmh_lost: float
    time_lost_s: float = Field(description="Time over the zone versus holding the entry speed")
    start_fraction: float = Field(description="start_m / lap length; maps the zone onto the shared track outline")
    end_fraction: float


class EnergyClippingTrace(BaseModel):
    """Downsampled (500 point) speed trace on the driver's own distance axis."""

    distance: List[float] = Field(description="Metres")
    speed: List[float] = Field(description="km/h")


class EnergyClippingDriver(BaseModel):
    driver: str
    team: Optional[str] = None
    color: str
    lap_time_s: float
    lapTime: str = Field(description="Formatted lap time, e.g. '1:42.526'")
    length_m: float
    clip_m: float = Field(description="Total metres spent clipping")
    kmh_lost_max: float = Field(description="Largest single-zone speed loss")
    time_lost_s: float = Field(description="Total estimated time lost to clipping")
    zones: List[EnergyClippingZone]
    trace: EnergyClippingTrace


class EnergyClippingTrack(BaseModel):
    """Pole lap's racing line, downsampled; zones from any driver map onto it by lap fraction."""

    x: List[float] = Field(description="Raw position units (tenths of a metre)")
    y: List[float]
    fraction: List[float]
    rotation: float = Field(description="Degrees to rotate so the pit straight is horizontal")


class EnergyClippingDrop(BaseModel):
    driver: str
    kmh: float
    start_m: float
    end_m: float
    speed_in_kmh: float
    speed_out_kmh: float


class EnergyClippingHighlights(BaseModel):
    most_time_lost: Dict[str, Any] = Field(description="{driver, seconds}")
    least_time_lost: Dict[str, Any] = Field(description="{driver, seconds}")
    field_median_time_lost_s: float
    field_median_clip_m: float
    biggest_single_drop: Optional[EnergyClippingDrop] = None
    pole_lap: Dict[str, Any] = Field(description="{driver, time_lost_s}")


class EnergyClippingMethod(BaseModel):
    definition: str
    min_zone_m: float
    min_loss_kmh: float
    brake_guard_m: float
    time_lost: str
    estimated: bool = Field(description="Always true: a conservative estimate, see the endpoint description")


class EnergyClippingResponse(BaseModel):
    """Energy clipping per driver on their fastest clean lap — from ``energy_clipping.py``. 2026+ only.

    ``drivers`` is sorted by ``time_lost_s`` descending. Requests for a season before 2026 are rejected
    with 404 because the earlier hybrid rules make the measurement meaningless.
    """

    drivers: List[EnergyClippingDriver]
    reference_driver: str = Field(description="Pole-lap driver whose racing line is ``track``")
    track: Optional[EnergyClippingTrack] = None
    highlights: EnergyClippingHighlights
    method: EnergyClippingMethod
    session_info: SessionInfo


# ---------------------------------------------------------------------------
# Car characteristics (corner speed profile, efficiency scatter)
# ---------------------------------------------------------------------------

class CarCharacteristicsCorner(BaseModel):
    number: int
    distance_m: float = Field(description="On the pole lap's distance frame")
    median_apex_kmh: float = Field(description="Field-median apex speed, which decides the class")
    class_: str = Field(alias="class", description="slow | medium | fast")
    merged: List[int] = Field(description="Corner numbers folded into this one (chicanes)")

    model_config = {"populate_by_name": True}


class CornerSpeedTeam(BaseModel):
    team: str
    short: str
    driver: Optional[str] = None
    color: Optional[str] = None
    avg_kmh: float = Field(description="Mean apex speed over the class's corners")
    delta_kmh: float = Field(description="Gap to the class best: 0 for the best, negative otherwise")


class CornerSpeedClass(BaseModel):
    corners: List[int] = Field(description="Corner numbers in this class")
    teams: List[CornerSpeedTeam] = Field(description="Fastest first; empty when the circuit has no such corners")


class CornerSpeedProfileResponse(BaseModel):
    """Mean apex speed per corner class for every team's best lap — from ``car_characteristics.py``. Any session.

    Classes come from the field-median apex speed at each corner: slow < 120 km/h, fast > 200 km/h, medium in
    between; flat-out kinks (> 280 km/h) are dropped. Apex speed is the minimum speed within 50 m of the corner.
    """

    classes: Dict[str, CornerSpeedClass] = Field(description="Keyed slow | medium | fast")
    corners: List[CarCharacteristicsCorner]
    highlights: Dict[str, Any] = Field(description="{best_per_class, biggest_spread}")
    rules: Dict[str, Any] = Field(description="Class thresholds, apex window, merge gap and skipped corners")
    reference: Dict[str, Any] = Field(description="{driver, team, lap_time_s, length_m} of the pole lap")
    session_info: SessionInfo


class EfficiencyTeam(BaseModel):
    team: str
    short: str
    driver: Optional[str] = None
    color: Optional[str] = None
    top_speed_kmh: float
    avg_apex_kmh: float = Field(description="Mean apex speed over the same classified corners as the profile")
    lap_time_s: float


class EfficiencyScatterResponse(BaseModel):
    """Top speed against mean apex speed per team (drag versus downforce) — from ``car_characteristics.py``."""

    teams: List[EfficiencyTeam]
    field_median: Dict[str, float] = Field(description="{x: top speed, y: apex speed}")
    highlights: Dict[str, Any] = Field(description="{top_speed, apex, most_efficient}")
    corners: List[int] = Field(description="Corner numbers the apex averages use")
    reference: Dict[str, Any] = Field(description="{driver, team, lap_time_s, length_m} of the pole lap")
    session_info: SessionInfo


# ---------------------------------------------------------------------------
# Field dominance map
# ---------------------------------------------------------------------------

class FieldDominanceMinisector(BaseModel):
    index: int
    start_fraction: float
    end_fraction: float
    owner: str = Field(description="Team name (mode=team) or driver TLA (mode=driver)")
    owner_color: str
    margin_s: float = Field(description="Time the runner-up lost in this minisector")


class FieldDominanceCandidate(BaseModel):
    name: str
    code: str = Field(description="Short label: three-letter team code or driver TLA")
    color: str
    count: int = Field(description="Minisectors owned")
    driver: str
    team: str
    lap_time_s: Optional[float] = Field(default=None, description="Only on ``candidates``")


class FieldDominanceTrack(BaseModel):
    x: List[float] = Field(description="Raw position units (tenths of a metre)")
    y: List[float]
    fraction: List[float]
    rotation: float = Field(description="Degrees to rotate so the pit straight is horizontal")


class FieldDominanceResponse(BaseModel):
    """Who owns each part of the lap — from ``field_dominance.py``. Any session.

    The lap is cut into 25 equal minisectors on the pole lap's racing line; the candidate with the least time
    spent in one owns it. ``mode=team`` ranks each team's faster driver, ``mode=driver`` every driver; ``top_n``
    keeps only the fastest N candidates.
    """

    mode: str = Field(description="team | driver")
    top_n: Optional[int] = None
    minisector_count: int
    minisectors: List[FieldDominanceMinisector]
    track: Optional[FieldDominanceTrack] = None
    owners: List[FieldDominanceCandidate] = Field(description="Candidates owning at least one minisector, most first")
    candidates: List[FieldDominanceCandidate] = Field(description="Every candidate, fastest lap first")
    highlights: Dict[str, Any] = Field(description="{pole, most_owned, biggest_margin, closest_margin, owner_count}")
    method: Dict[str, Any]
    session_info: SessionInfo


# ---------------------------------------------------------------------------
# Sector gap to pole (qualifying)
# ---------------------------------------------------------------------------

class SectorGapDriver(BaseModel):
    position: int
    driver: str
    color: str
    lap_time_s: float
    gap_s: float = Field(description="Lap-time gap to pole")
    sectors: List[float] = Field(description="[S1, S2, S3] seconds on this driver's fastest lap")
    sector_gaps_s: List[float] = Field(description="[S1, S2, S3] minus pole's; negative = faster than pole")


class SectorGapPole(BaseModel):
    driver: str
    color: str
    lap_time_s: float
    sectors: List[float]


class SectorGapResponse(BaseModel):
    """Gap to pole split into S1 / S2 / S3 for P2..P10 — from ``sector_gap.py``. Q and SQ only.

    Sectors are those timed on each driver's own fastest lap, so the three segments add up to the lap-time gap.
    Drivers whose lap could not be matched to a sector triple are listed in ``unmatched``.
    """

    pole: SectorGapPole
    drivers: List[SectorGapDriver]
    unmatched: List[str]
    highlights: Dict[str, Any] = Field(description="{sector_leaders, faster_than_pole, largest_sector_gap}")
    method: Dict[str, Any]
    session_info: SessionInfo
