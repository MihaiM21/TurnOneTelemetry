"""Canonical registry of V2 analysis features (single source of truth).

Both the admin "which plots are missing?" inventory and the "generate the
missing plots" backfill consume this registry so the two paths can never drift
apart. See ``src/workers/plot_inventory.py`` and the ``/api/admin/plots/*``
endpoints in ``src/api/routers/admin.py``.

Only **singleton** features live here — features that produce exactly one
deterministic ``data_type`` per session with no driver/pair/lap arguments.
Completeness is only well defined for those. Parameterized features
(per-driver, per-pair, per-lap: ``track_comparison_{d1}_{d2}``,
``driver_radar_{driver}_...``, ``lap_all_data_{driver}_{lap}`` and friends)
have an open-ended set of variants, so "missing" is undefined for them; they
are generated on demand as a default top-N set, not tracked here.

Design notes:
- ``data_type`` strings are the exact keys each module stores under in MongoDB
  (verified against each module's ``store_data_dict_to_mongo`` /
  ``cached_or_generate`` call). ``speed_distribution`` in particular stores the
  overall series under ``speed_distribution_Overall`` (capital O), which is the
  kind of mismatch that would otherwise produce false "missing" reports.
  ``tests/unit/test_plot_registry.py`` asserts these stay in sync with each
  module's ``DATA_TYPE`` constant.
- Generator entrypoints are imported lazily inside each ``generate`` callable so
  importing this registry (e.g. from the admin router) stays cheap and does not
  pull matplotlib / ingestion clients into the request path until generation
  actually runs. Every entrypoint persists to MongoDB itself (``store_to_mongo``
  default ``True`` or via ``cached_or_generate``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple, Union

# Normalized session abbreviations as produced by
# ``simplify_session_name`` (src/services/orchestrator_helpers.py).
Identifier = Union[int, str]
Generator = Callable[[int, Identifier, str], Any]

# Session-applicability groups (abbreviations only). An empty ``applies_to`` on a
# PlotSpec means "all session types".
_RACE: FrozenSet[str] = frozenset({"R", "S"})
_QUALI: FrozenSet[str] = frozenset({"Q", "SQ"})
_PRACTICE_QUALI: FrozenSet[str] = frozenset({"FP1", "FP2", "FP3", "Q", "SQ"})


@dataclass(frozen=True)
class PlotSpec:
    """One singleton V2 feature: its stored key, applicability and generator."""

    data_type: str
    label: str
    # Empty set == applies to every session type.
    applies_to: FrozenSet[str] = field(default_factory=frozenset)
    generate: Generator = None  # type: ignore[assignment]
    # True for class-based features that route through ``cached_or_generate``,
    # which only warms Redis and does NOT write Mongo. The admin backfill must
    # persist their result explicitly (see ``persist_generated``). Function-style
    # entrypoints already store themselves via ``store_to_mongo=True``.
    persist_result: bool = False

    def applies(self, session_type: str) -> bool:
        if not self.applies_to:
            return True
        return session_type.strip().upper() in self.applies_to


def persist_generated(y: int, ident: Identifier, e: str, data_type: str, data: Any) -> None:
    """Store a generated V2 payload into the Mongo ``*_processed_data_v2`` collection.

    Needed for ``persist_result`` specs, whose generators only warm Redis. Uses
    the same storage entrypoint as the function-style features so the document
    shape is identical. Resolves the canonical event name/round via the client.
    """
    if not data:
        return
    from src.ingestion.static_client import F1StaticClient
    from src.repositories.plots import store_data_dict_to_mongo

    info = F1StaticClient().get_event_info(y, ident)
    if not info:
        return
    store_data_dict_to_mongo(
        year=y,
        round_nr=info["round_nr"],
        session_name=e,
        event_name=info["name"],
        data_type=data_type,
        data=data,
        version="v2",
    )


# --------------------------------------------------------------------------- #
# Generator wrappers. Lazy imports keep registry import cheap. Each returns the
# generated payload (truthy on success); persistence happens inside.
# --------------------------------------------------------------------------- #
def _gen_top_speed_telemetry(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.top_speed import TopSpeedData_Telemetry
    return TopSpeedData_Telemetry(y, ident, e, store_to_mongo=True)


def _gen_top_speed_speedtrap(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.top_speed import TopSpeedData_SpeedTrap
    return TopSpeedData_SpeedTrap(y, ident, e, store_to_mongo=True)


def _gen_throttle_comparison(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.throttle_comparison import ThrottleCompData
    return ThrottleCompData(y, ident, e, store_to_mongo=True)


def _gen_speed_distribution(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.speed_distribution import SpeedDistributionData
    return SpeedDistributionData(y, ident, e, driver=None, store_to_mongo=True)


def _gen_driver_pace(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.driver_pace import DriverPaceData
    return DriverPaceData(y, ident, e, store_to_mongo=True)


def _gen_teams_pace(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.teams_pace import TeamsPaceData
    return TeamsPaceData(y, ident, e, store_to_mongo=True)


def _gen_tyre_stint_usage(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.tyre_stint_usage import TyreStintUsageData
    return TyreStintUsageData(y, ident, e, store_to_mongo=True)


def _gen_session_weather(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.session_weather import SessionWeatherData
    return SessionWeatherData()(y, ident, e)


def _gen_qualifying_results(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.qualifying_results import QualiResultsData
    return QualiResultsData(y, ident, e, store_to_mongo=True)


def _gen_theoretical_best(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.theoretical_best import TheoreticalBestData
    return TheoreticalBestData()(y, ident, e)


def _gen_track_evolution(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.track_evolution import TrackEvolutionData
    return TrackEvolutionData()(y, ident, e)


def _gen_position_changes(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.position_changes import PositionChangesData
    return PositionChangesData()(y, ident, e)


def _gen_pit_strategy(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.pit_strategy import PitStrategyData
    return PitStrategyData()(y, ident, e)


def _gen_tyre_degradation(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.tyre_degradation import TyreDegradationData
    return TyreDegradationData()(y, ident, e, None, False)


def _gen_race_gaps_leader(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.race_gaps import RaceGapsData
    return RaceGapsData()(y, ident, e, "leader", None)


def _gen_race_gaps_average(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.race_gaps import RaceGapsData
    return RaceGapsData()(y, ident, e, "average", None)


def _gen_race_pace_heatmap(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.race_pace_heatmap import RacePaceHeatmapData
    return RacePaceHeatmapData()(y, ident, e)


def _gen_race_story(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.race_story import RaceStoryData
    return RaceStoryData()(y, ident, e)


def _gen_tyre_degradation_fc(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.tyre_degradation import TyreDegradationData
    return TyreDegradationData()(y, ident, e, None, True)


def _gen_driver_radar_auto(y: int, ident: Identifier, e: str) -> Any:
    from src.services.analysis.v2.driver_radar import DriverRadarData
    return DriverRadarData()(y, ident, e, None)


# --------------------------------------------------------------------------- #
# Stored-key accessors. Every key below is produced by the owning module's own
# builder rather than re-spelled here, so a key format change cannot silently
# desync the backfill from the request path (which is exactly what
# ``track_map_speed_{d}`` hardcoding used to risk).
# --------------------------------------------------------------------------- #
def _key_tyre_degradation(driver: Optional[str], fuel_corrected: bool) -> str:
    from src.services.analysis.v2.tyre_degradation import _data_type
    return _data_type(driver, fuel_corrected)


def _key_track_map(drv: str, color_by: str) -> str:
    from src.services.analysis.v2.telemetry_track_map import _data_type
    return _data_type(drv, color_by)


def _key_driver_radar(drv: Optional[str]) -> str:
    from src.services.analysis.v2.driver_radar import session_data_type
    return session_data_type(drv)


def _key_speed_distribution(drv: Optional[str]) -> str:
    from src.services.analysis.v2.speed_distribution import _data_type
    return _data_type(drv)


def _key_laptimes_distribution(drv: str) -> str:
    from src.services.analysis.v2.laptimes_distribution import _data_type
    return _data_type(drv)


def _key_lap_all_data(drv: str, lap: int) -> str:
    from src.services.analysis.v2.lap_all_data import _data_type
    return _data_type(drv, lap)


# --------------------------------------------------------------------------- #
# The catalog. Order is the generation order used by the backfill worker.
# --------------------------------------------------------------------------- #
V2_SINGLETON_PLOTS: List[PlotSpec] = [
    # ---- All session types ----
    PlotSpec("top_speed_telemetry", "Top Speed (telemetry)", frozenset(), _gen_top_speed_telemetry),
    PlotSpec("top_speed_speedtrap", "Top Speed (speed trap)", frozenset(), _gen_top_speed_speedtrap),
    PlotSpec("throttle_comparison", "Throttle Comparison", frozenset(), _gen_throttle_comparison),
    PlotSpec("speed_distribution_Overall", "Speed Distribution (overall)", frozenset(), _gen_speed_distribution),
    PlotSpec("driver_pace", "Driver Pace", frozenset(), _gen_driver_pace),
    PlotSpec("teams_pace", "Teams Pace", frozenset(), _gen_teams_pace),
    PlotSpec("tyre_stint_usage", "Tyre Stint Usage", frozenset(), _gen_tyre_stint_usage),
    PlotSpec("session_weather", "Session Weather", frozenset(), _gen_session_weather, persist_result=True),
    # ---- Qualifying / Sprint Qualifying ----
    PlotSpec("qualifying_results", "Qualifying Results", _QUALI, _gen_qualifying_results),
    PlotSpec("theoretical_best", "Theoretical Best Lap", _QUALI, _gen_theoretical_best, persist_result=True),
    # ---- Practice + Qualifying ----
    PlotSpec("track_evolution", "Track Evolution", _PRACTICE_QUALI, _gen_track_evolution, persist_result=True),
    # ---- Race / Sprint only ----
    PlotSpec("position_changes", "Position Changes", _RACE, _gen_position_changes, persist_result=True),
    PlotSpec("pit_strategy", "Pit Strategy & Undercuts", _RACE, _gen_pit_strategy, persist_result=True),
    PlotSpec("tyre_degradation", "Tyre Degradation", _RACE, _gen_tyre_degradation, persist_result=True),
    PlotSpec("race_gaps_leader", "Race Gaps (to leader)", _RACE, _gen_race_gaps_leader, persist_result=True),
    PlotSpec("race_gaps_average", "Race Gaps (to average)", _RACE, _gen_race_gaps_average, persist_result=True),
    PlotSpec("race_pace_heatmap", "Race Pace Heatmap", _RACE, _gen_race_pace_heatmap, persist_result=True),
    PlotSpec("race_story", "Race Story", _RACE, _gen_race_story, persist_result=True),
    PlotSpec(
        _key_tyre_degradation(None, True), "Tyre Degradation (fuel-corrected)",
        _RACE, _gen_tyre_degradation_fc, persist_result=True,
    ),
    # The unfiltered radar. This is the payload the public API serves for a
    # driver-less /driver-radar-data request, so it belongs in the completeness
    # inventory even though sibling per-driver radars are parameterized.
    PlotSpec(
        _key_driver_radar(None), "Driver Radar (auto selection)",
        frozenset(), _gen_driver_radar_auto, persist_result=True,
    ),
]


def specs_for_session(session_type: str) -> List[PlotSpec]:
    """Singleton specs applicable to a normalized session abbreviation."""
    return [spec for spec in V2_SINGLETON_PLOTS if spec.applies(session_type)]


def expected_data_types(session_type: str) -> List[str]:
    """Singleton ``data_type`` keys expected for a session (e.g. FP1/Q/R)."""
    return [spec.data_type for spec in specs_for_session(session_type)]


# --------------------------------------------------------------------------- #
# Parameterized (per-driver / per-pair) features
#
# These have an open-ended variant count (N drivers, N*(N-1)/2 pairs), so they
# aren't part of the singleton completeness inventory. The admin backfill can
# still generate the full set on demand. Each spec's ``generate`` returns the
# payload; ``persist_key`` (when set) names the stored data_type for class-based
# features that route through ``cached_or_generate`` and don't self-persist.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DriverPlotSpec:
    name: str
    generate: Callable[[int, Identifier, str, str], Any]  # (y, ident, session, driver)
    # Set when the generator only warms Redis and the caller must write Mongo.
    persist_key: Optional[Callable[[str], str]] = None    # driver -> data_type
    label: str = ""
    applies_to: FrozenSet[str] = field(default_factory=frozenset)
    cost: str = "heavy"
    # Always the stored data_type, whether or not the caller has to persist it.
    # Needed so the planner can skip work that already exists in Mongo — the
    # self-persisting features have no ``persist_key`` but still have a key.
    stored_key: Optional[Callable[[str], str]] = None

    def key_for(self, driver: str) -> Optional[str]:
        fn = self.stored_key or self.persist_key
        return fn(driver) if fn else None

    def applies(self, session_type: str) -> bool:
        if not self.applies_to:
            return True
        return session_type.strip().upper() in self.applies_to


@dataclass(frozen=True)
class PairPlotSpec:
    name: str
    generate: Callable[[int, Identifier, str, str, str], Any]  # (y, ident, session, d1, d2)
    persist_key: Optional[Callable[[str, str], str]] = None    # (d1, d2) -> data_type
    label: str = ""
    applies_to: FrozenSet[str] = field(default_factory=frozenset)
    cost: str = "heavy"
    stored_key: Optional[Callable[[str, str], str]] = None
    # True when the stored key is ``{D1}_{D2}`` in the order given, so (A,B) and
    # (B,A) are different documents and a user asking for the reverse order gets
    # a cache miss. Those features must be planned in both directions. Features
    # that sort their key (corner_duel) stay False — one document covers both.
    ordered: bool = False

    def key_for(self, d1: str, d2: str) -> Optional[str]:
        fn = self.stored_key or self.persist_key
        return fn(d1, d2) if fn else None

    def applies(self, session_type: str) -> bool:
        if not self.applies_to:
            return True
        return session_type.strip().upper() in self.applies_to


@dataclass(frozen=True)
class DriverLapPlotSpec:
    """A feature parameterized on both a driver and a lap number.

    The variant count is drivers x laps (~1,000+ for a race), so these are never
    part of the completeness inventory and the admin UI requires an explicit lap
    range before it will plan them.
    """

    name: str
    generate: Callable[[int, Identifier, str, str, int], Any]  # (y, ident, session, driver, lap)
    persist_key: Optional[Callable[[str, int], str]] = None
    label: str = ""
    applies_to: FrozenSet[str] = field(default_factory=frozenset)
    cost: str = "extreme"
    stored_key: Optional[Callable[[str, int], str]] = None

    def key_for(self, driver: str, lap: int) -> Optional[str]:
        fn = self.stored_key or self.persist_key
        return fn(driver, lap) if fn else None

    def applies(self, session_type: str) -> bool:
        if not self.applies_to:
            return True
        return session_type.strip().upper() in self.applies_to


@dataclass(frozen=True)
class SeasonPlotSpec:
    """A season-wide feature: one payload per year, no session.

    Stored under the synthetic ``{year}_SEASON`` document (see
    ``src/repositories/plots.py``). ``variants`` expands a parameterized season
    feature (season form's rolling window) into concrete keys.
    """

    name: str
    generate: Callable[..., Any]          # (year, *variant) -> payload
    stored_key: Callable[..., str]        # (*variant) -> data_type
    label: str = ""
    cost: str = "light"
    # Default variant tuples, e.g. [(3,)] for season_form window=3. A single
    # empty tuple means the feature takes no extra parameters.
    default_variants: Tuple[Tuple[Any, ...], ...] = ((),)


def _gen_speed_distribution_driver(y: int, ident: Identifier, e: str, drv: str) -> Any:
    from src.services.analysis.v2.speed_distribution import SpeedDistributionData
    return SpeedDistributionData(y, ident, e, driver=drv, store_to_mongo=True)


def _gen_track_map_driver(y: int, ident: Identifier, e: str, drv: str) -> Any:
    from src.services.analysis.v2.telemetry_track_map import TrackMapData
    return TrackMapData()(y, ident, e, drv, "speed")


def _gen_driver_radar_driver(y: int, ident: Identifier, e: str, drv: str) -> Any:
    from src.services.analysis.v2.driver_radar import DriverRadarData
    return DriverRadarData()(y, ident, e, drv)


def _gen_track_comparison(y: int, ident: Identifier, e: str, d1: str, d2: str) -> Any:
    from src.services.analysis.v2.track_comparison import TrackComparisonData
    return TrackComparisonData(y, ident, e, d1, d2, store_to_mongo=True)


def _gen_throttle_brake(y: int, ident: Identifier, e: str, d1: str, d2: str) -> Any:
    from src.services.analysis.v2.throttle_brake_comparison import ThrottleBrakeCompData
    return ThrottleBrakeCompData(y, ident, e, d1, d2, store_to_mongo=True)


def _gen_lap_time_analysis(y: int, ident: Identifier, e: str, d1: str, d2: str) -> Any:
    from src.services.analysis.v2.lap_time_analysis import LapTimeAnalysisData
    return LapTimeAnalysisData(y, ident, e, d1, d2, store_to_mongo=True)


def _gen_corner_duel(y: int, ident: Identifier, e: str, d1: str, d2: str) -> Any:
    from src.services.analysis.v2.corner_duel import CornerDuelData
    return CornerDuelData()(y, ident, e, d1, d2)


def _gen_track_map_gear(y: int, ident: Identifier, e: str, drv: str) -> Any:
    from src.services.analysis.v2.telemetry_track_map import TrackMapData
    return TrackMapData()(y, ident, e, drv, "gear")


def _gen_laptimes_distribution(y: int, ident: Identifier, e: str, drv: str) -> Any:
    from src.services.analysis.v2.laptimes_distribution import LaptimesDistribution
    return LaptimesDistribution(y, ident, e, drv, store_to_mongo=True)


def _gen_tyre_degradation_driver(y: int, ident: Identifier, e: str, drv: str) -> Any:
    from src.services.analysis.v2.tyre_degradation import TyreDegradationData
    return TyreDegradationData()(y, ident, e, drv, False)


def _gen_tyre_degradation_driver_fc(y: int, ident: Identifier, e: str, drv: str) -> Any:
    from src.services.analysis.v2.tyre_degradation import TyreDegradationData
    return TyreDegradationData()(y, ident, e, drv, True)


def _gen_lap_all_data(y: int, ident: Identifier, e: str, drv: str, lap: int) -> Any:
    from src.services.analysis.v2.lap_all_data import LapAllData
    return LapAllData()(y, ident, e, drv, lap)


V2_PER_DRIVER_PLOTS: List[DriverPlotSpec] = [
    DriverPlotSpec(
        "speed_distribution", _gen_speed_distribution_driver,
        label="Speed Distribution (per driver)", stored_key=_key_speed_distribution,
    ),
    DriverPlotSpec(
        "track_map", _gen_track_map_driver,
        persist_key=lambda d: _key_track_map(d, "speed"),
        label="Track Map (speed)",
    ),
    DriverPlotSpec(
        "track_map_gear", _gen_track_map_gear,
        persist_key=lambda d: _key_track_map(d, "gear"),
        label="Track Map (gear)",
    ),
    DriverPlotSpec(
        "driver_radar", _gen_driver_radar_driver,
        persist_key=_key_driver_radar,
        label="Driver Radar (per driver)",
    ),
    DriverPlotSpec(
        "laptimes_distribution", _gen_laptimes_distribution,
        label="Lap Times Distribution", stored_key=_key_laptimes_distribution,
    ),
    DriverPlotSpec(
        "tyre_degradation_driver", _gen_tyre_degradation_driver,
        persist_key=lambda d: _key_tyre_degradation(d, False),
        label="Tyre Degradation (per driver)", applies_to=_RACE,
    ),
    DriverPlotSpec(
        "tyre_degradation_driver_fc", _gen_tyre_degradation_driver_fc,
        persist_key=lambda d: _key_tyre_degradation(d, True),
        label="Tyre Degradation (per driver, fuel-corrected)", applies_to=_RACE,
    ),
]

V2_PAIR_PLOTS: List[PairPlotSpec] = [
    PairPlotSpec(
        "track_comparison", _gen_track_comparison,
        label="Track Comparison (minisectors)", ordered=True,
        stored_key=lambda a, b: f"track_comparison_{a.upper()}_{b.upper()}",
    ),
    PairPlotSpec(
        "throttle_brake_comparison", _gen_throttle_brake,
        label="Throttle / Brake Comparison", ordered=True,
        stored_key=lambda a, b: f"throttle_brake_comparison_{a.upper()}_{b.upper()}",
    ),
    PairPlotSpec(
        "lap_time_analysis", _gen_lap_time_analysis,
        label="Lap Time Analysis", ordered=True,
        stored_key=lambda a, b: f"lap_time_analysis_{a.upper()}_{b.upper()}",
    ),
    PairPlotSpec(
        "corner_duel", _gen_corner_duel,
        lambda a, b: "corner_duel_" + "_".join(sorted([a.upper(), b.upper()])),
        label="Corner Duel",
    ),
]

V2_PER_DRIVER_LAP_PLOTS: List[DriverLapPlotSpec] = [
    DriverLapPlotSpec(
        "lap_all_data", _gen_lap_all_data,
        label="Full Lap Telemetry", stored_key=_key_lap_all_data,
    ),
]


# --------------------------------------------------------------------------- #
# Season / career scope
#
# These are not tied to a session. Their generators route through
# ``season_cached_or_generate``, which persists to Mongo only for *past*
# seasons — and even then only via a read path that did not work before
# ``store_season_data_to_mongo`` existed. The admin backfill therefore always
# persists explicitly through :func:`persist_season_generated`.
# --------------------------------------------------------------------------- #
def _gen_teammate_battle(y: int) -> Any:
    from src.services.analysis.v2.teammate_battle import TeammateBattleData
    return TeammateBattleData()(y)


def _gen_season_form(y: int, window: int) -> Any:
    from src.services.analysis.v2.season_form import SeasonFormData
    return SeasonFormData()(y, window)


def _gen_season_radar(y: int) -> Any:
    from src.services.analysis.v2.driver_radar import SeasonRadarData
    return SeasonRadarData()(y)


def _key_teammate_battle() -> str:
    from src.services.analysis.v2.teammate_battle import DATA_TYPE
    return DATA_TYPE


def _key_season_form(window: int) -> str:
    from src.services.analysis.v2.season_form import _data_type
    return _data_type(window)


def _key_season_radar() -> str:
    from src.services.analysis.v2.driver_radar import SEASON_DATA_TYPE
    return SEASON_DATA_TYPE


# Windows the public /form-guide endpoint accepts (2..10). Only the default is
# generated unless the admin explicitly asks for more.
SEASON_FORM_WINDOWS: Tuple[int, ...] = tuple(range(2, 11))
DEFAULT_SEASON_FORM_WINDOW = 3

V2_SEASON_PLOTS: List[SeasonPlotSpec] = [
    SeasonPlotSpec(
        "teammate_battle", _gen_teammate_battle, _key_teammate_battle,
        label="Teammate Battle",
    ),
    SeasonPlotSpec(
        "season_form", _gen_season_form, _key_season_form,
        label="Season Form Guide",
        default_variants=((DEFAULT_SEASON_FORM_WINDOW,),),
    ),
    SeasonPlotSpec(
        "driver_radar_season", _gen_season_radar, _key_season_radar,
        label="Driver Radar (season)",
    ),
]


def _gen_career_radar(years: Sequence[int]) -> Any:
    from src.services.analysis.v2.driver_radar import CareerRadarData
    return CareerRadarData()(list(years))


def _key_career_radar(years: Sequence[int]) -> str:
    from src.services.analysis.v2.driver_radar import career_data_type
    return career_data_type(list(years))


def career_radar_spec() -> SeasonPlotSpec:
    """Career radar, whose ``variant`` is the year range itself.

    Stored against ``max(years)`` (matching ``CareerRadarData``), so the admin
    must supply the range; there is no meaningful default.
    """
    return SeasonPlotSpec(
        "driver_radar_career",
        lambda _year, years: _gen_career_radar(years),
        lambda years: _key_career_radar(years),
        label="Driver Radar (career)",
        default_variants=(),
    )


V2_CAREER_PLOTS: List[SeasonPlotSpec] = [career_radar_spec()]


def persist_season_generated(year: int, data_type: str, data: Any) -> None:
    """Store a season-scope payload so it survives beyond the Redis TTL.

    ``season_cached_or_generate`` deliberately keeps the *current* season in
    Redis only (results change weekly). An admin backfill is a deliberate act,
    so it writes through to Mongo regardless of season — the request path still
    prefers its short-TTL Redis copy for the live year.
    """
    if not data:
        return
    from src.repositories.plots import store_season_data_to_mongo

    store_season_data_to_mongo(year=year, data_type=data_type, data=data)


# --------------------------------------------------------------------------- #
# Unified catalog
#
# One flat view over every spec list above, so the admin UI's feature selector
# and the backfill planner read from the same place and neither hardcodes a
# feature list. ``key`` is the stable slug used in API selections.
# --------------------------------------------------------------------------- #
KIND_SINGLETON = "singleton"
KIND_PER_DRIVER = "per_driver"
KIND_PER_PAIR = "per_pair"
KIND_PER_DRIVER_LAP = "per_driver_lap"
KIND_SEASON = "season"
KIND_CAREER = "career"

_KIND_GROUP = {
    KIND_SINGLETON: "Field-wide",
    KIND_PER_DRIVER: "Per driver",
    KIND_PER_PAIR: "Per driver pair",
    KIND_PER_DRIVER_LAP: "Per lap",
    KIND_SEASON: "Season",
    KIND_CAREER: "Career",
}


@dataclass(frozen=True)
class FeatureEntry:
    """A selectable feature in the admin generator."""

    key: str                    # stable selection slug
    label: str                  # human name shown in the UI
    kind: str                   # one of the KIND_* constants
    group: str                  # UI section heading
    applies_to: FrozenSet[str]  # empty == every session type
    cost: str                   # light | heavy | extreme
    spec: Any                   # the underlying *PlotSpec

    @property
    def session_scoped(self) -> bool:
        return self.kind not in (KIND_SEASON, KIND_CAREER)

    def applies(self, session_type: str) -> bool:
        if not self.applies_to:
            return True
        return session_type.strip().upper() in self.applies_to

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "group": self.group,
            "applies_to": sorted(self.applies_to),
            "cost": self.cost,
            "session_scoped": self.session_scoped,
        }


def _build_catalog() -> List[FeatureEntry]:
    entries: List[FeatureEntry] = []
    for spec in V2_SINGLETON_PLOTS:
        entries.append(FeatureEntry(
            key=spec.data_type, label=spec.label, kind=KIND_SINGLETON,
            group=_KIND_GROUP[KIND_SINGLETON], applies_to=spec.applies_to,
            cost="light", spec=spec,
        ))
    for kind, specs in (
        (KIND_PER_DRIVER, V2_PER_DRIVER_PLOTS),
        (KIND_PER_PAIR, V2_PAIR_PLOTS),
        (KIND_PER_DRIVER_LAP, V2_PER_DRIVER_LAP_PLOTS),
    ):
        for spec in specs:
            entries.append(FeatureEntry(
                key=spec.name, label=spec.label or spec.name, kind=kind,
                group=_KIND_GROUP[kind], applies_to=spec.applies_to,
                cost=spec.cost, spec=spec,
            ))
    for kind, specs in ((KIND_SEASON, V2_SEASON_PLOTS), (KIND_CAREER, V2_CAREER_PLOTS)):
        for spec in specs:
            entries.append(FeatureEntry(
                key=spec.name, label=spec.label or spec.name, kind=kind,
                group=_KIND_GROUP[kind], applies_to=frozenset(),
                cost=spec.cost, spec=spec,
            ))
    return entries


FEATURE_CATALOG: List[FeatureEntry] = _build_catalog()
_CATALOG_BY_KEY: Dict[str, FeatureEntry] = {entry.key: entry for entry in FEATURE_CATALOG}


def feature_by_key(key: str) -> Optional[FeatureEntry]:
    return _CATALOG_BY_KEY.get(key)


def catalog_for_session(session_type: str) -> List[FeatureEntry]:
    """Session-scoped features applicable to a normalized session abbreviation."""
    return [e for e in FEATURE_CATALOG if e.session_scoped and e.applies(session_type)]


def catalog_for_scope(kind: str) -> List[FeatureEntry]:
    """Features of one kind (e.g. every season-scope feature)."""
    return [e for e in FEATURE_CATALOG if e.kind == kind]


def session_drivers(y: int, ident: Identifier, e: str) -> List[str]:
    """Sorted list of driver TLAs participating in a session (V2/livetiming)."""
    from src.services.analysis.v2._helpers import get_all_driver_codes
    from src.services.analysis.v2.session_store import SessionDataStore

    store = SessionDataStore(y, ident, e)
    codes = get_all_driver_codes(store.base_url, store.client)
    return sorted({tla for tla in codes.values() if tla})


def session_driver_laps(y: int, ident: Identifier, e: str) -> Dict[str, List[int]]:
    """Map driver TLA -> the lap numbers they completed, for the lap pickers.

    Reads the derived ``lap_times`` bundle (keyed by car number) and re-keys it
    by TLA. Using the bundle rather than re-parsing the raw stream means this is
    cheap once a session has been prewarmed.
    """
    from src.services.analysis.v2._helpers import get_all_driver_codes
    from src.services.analysis.v2.session_store import SessionDataStore

    store = SessionDataStore(y, ident, e)
    codes = get_all_driver_codes(store.base_url, store.client)  # {car_number: tla}

    laps_by_driver: Dict[str, List[int]] = {}
    for car_number, records in (store.lap_times() or {}).items():
        tla = str(codes.get(car_number) or "").strip().upper()
        if not tla:
            continue
        laps = {int(r["lap"]) for r in records if r.get("lap") is not None}
        if laps:
            laps_by_driver.setdefault(tla, []).extend(sorted(laps))
    return {tla: sorted(set(laps)) for tla, laps in sorted(laps_by_driver.items())}
