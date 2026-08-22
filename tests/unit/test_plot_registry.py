"""Unit tests for the V2 plot registry (single source of truth)."""

from src.services.analysis.v2.registry import (
    V2_SINGLETON_PLOTS,
    PlotSpec,
    expected_data_types,
    specs_for_session,
)


def test_data_types_are_unique():
    keys = [spec.data_type for spec in V2_SINGLETON_PLOTS]
    assert len(keys) == len(set(keys)), "duplicate data_type in registry"


def test_every_spec_has_a_generator():
    for spec in V2_SINGLETON_PLOTS:
        assert callable(spec.generate), f"{spec.data_type} has no generator"


def test_data_types_match_module_constants():
    """Guard against drift between the registry keys and each module's stored key."""
    from src.services.analysis.v2 import (
        pit_strategy,
        position_changes,
        race_gaps,
        race_pace_heatmap,
        race_story,
        session_weather,
        theoretical_best,
        track_evolution,
        tyre_degradation,
        tyre_stint_usage,
    )

    expected_constants = {
        "position_changes": position_changes.DATA_TYPE,
        "pit_strategy": pit_strategy.DATA_TYPE,
        "tyre_degradation": tyre_degradation.DATA_TYPE,
        "tyre_stint_usage": tyre_stint_usage.DATA_TYPE,
        "race_pace_heatmap": race_pace_heatmap.DATA_TYPE,
        "race_story": race_story.DATA_TYPE,
        "session_weather": session_weather.DATA_TYPE,
        "theoretical_best": theoretical_best.DATA_TYPE,
        "track_evolution": track_evolution.DATA_TYPE,
        "race_gaps_leader": race_gaps.DATA_TYPE_LEADER,
        "race_gaps_average": race_gaps.DATA_TYPE_AVERAGE,
    }
    registry_keys = {spec.data_type for spec in V2_SINGLETON_PLOTS}
    for key, module_value in expected_constants.items():
        assert key == module_value, f"registry key {key!r} != module constant {module_value!r}"
        assert key in registry_keys


def test_speed_distribution_uses_overall_capital_key():
    # The overall series stores under 'speed_distribution_Overall' — a mismatch
    # here would produce false "missing" reports.
    keys = {spec.data_type for spec in V2_SINGLETON_PLOTS}
    assert "speed_distribution_Overall" in keys


def test_race_session_applicability():
    race = set(expected_data_types("R"))
    assert {"position_changes", "race_story", "race_gaps_leader", "race_gaps_average"} <= race
    assert "theoretical_best" not in race
    assert "track_evolution" not in race
    assert "qualifying_results" not in race
    # All-session singletons still apply to the race.
    assert "top_speed_telemetry" in race
    # Sprint mirrors race.
    assert set(expected_data_types("S")) == race


def test_qualifying_applicability():
    quali = set(expected_data_types("Q"))
    assert {"qualifying_results", "theoretical_best", "track_evolution"} <= quali
    assert "position_changes" not in quali
    assert set(expected_data_types("SQ")) == quali


def test_practice_applicability():
    fp1 = set(expected_data_types("FP1"))
    assert "track_evolution" in fp1
    assert "qualifying_results" not in fp1
    assert "theoretical_best" not in fp1
    assert "position_changes" not in fp1
    assert "top_speed_telemetry" in fp1


def test_specs_for_session_is_normalized():
    # Lower-case input resolves the same as upper-case.
    assert [s.data_type for s in specs_for_session("r")] == [
        s.data_type for s in specs_for_session("R")
    ]


def test_empty_applies_to_means_all():
    spec = PlotSpec("x", "X", frozenset(), lambda *a: True)
    assert spec.applies("FP1")
    assert spec.applies("R")
