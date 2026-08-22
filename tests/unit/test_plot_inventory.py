"""Unit tests for the admin plot inventory + backfill worker (offline)."""

import pytest

from src.services.analysis.v2.registry import PlotSpec, expected_data_types
from src.workers import plot_inventory


def _fake_schedule():
    return [
        {
            "round": 1,
            "grandPrix": "Test Grand Prix",
            "sessions": [{"name": "Race"}, {"name": "Qualifying"}],
        }
    ]


# Existing data is keyed by (normalized event name, session_type) — see
# plot_inventory._norm_name. "Test Grand Prix" folds to this.
_GP_KEY = "testgrandprix"


@pytest.fixture
def offline(monkeypatch):
    """Stub the schedule so no network/Mongo is touched."""
    monkeypatch.setattr(plot_inventory, "get_season_events", lambda y: _fake_schedule())


def test_norm_name_joins_schedule_to_stored_doc():
    # The schedule says "Australian Grand Prix"; Mongo stores "AustralianGrandPrix".
    # Both must fold to the same key so stored plots are recognised regardless of
    # the doc's round_nr (which may hold the offset livetiming round).
    assert plot_inventory._norm_name("Australian Grand Prix") == "australiangrandprix"
    assert plot_inventory._norm_name("AustralianGrandPrix") == "australiangrandprix"
    assert plot_inventory._norm_name("São Paulo Grand Prix") == "saopaulograndprix"
    assert plot_inventory._norm_name(None) == ""


def test_compute_missing_matches_by_name_not_round(offline, monkeypatch):
    # Stored data is keyed by folded name; the schedule round (1) is irrelevant.
    race_expected = expected_data_types("R")
    monkeypatch.setattr(
        plot_inventory, "_existing_data_types",
        lambda year: {(_GP_KEY, "R"): set(race_expected)},
    )
    report = plot_inventory.compute_missing(year=2025, session="R")
    sess = report["grand_prix"][0]["sessions"][0]
    assert sess["missing"] == []
    assert report["total_missing_plots"] == 0


def test_compute_missing_reports_single_gap(offline, monkeypatch):
    race_expected = expected_data_types("R")
    # Everything present except driver_pace for the race; qualifying entirely absent.
    present = {
        (_GP_KEY, "R"): set(race_expected) - {"driver_pace"},
    }
    monkeypatch.setattr(plot_inventory, "_existing_data_types", lambda year: present)

    report = plot_inventory.compute_missing(year=2025, session="R")

    assert report["version"] == "v2"
    assert report["total_sessions"] == 1
    gp = report["grand_prix"][0]
    assert gp["round_nr"] == 1
    assert gp["event_name"] == "Test Grand Prix"
    sess = gp["sessions"][0]
    assert sess["session_type"] == "R"
    assert sess["missing"] == ["driver_pace"]
    assert "driver_pace" not in sess["present"]


def test_compute_missing_flags_absent_session(offline, monkeypatch):
    # No data at all for the year -> every expected plot is missing.
    monkeypatch.setattr(plot_inventory, "_existing_data_types", lambda year: {})

    report = plot_inventory.compute_missing(year=2025)

    # Both Race and Qualifying enumerated from the schedule.
    session_types = {s["session_type"] for gp in report["grand_prix"] for s in gp["sessions"]}
    assert session_types == {"R", "Q"}
    for gp in report["grand_prix"]:
        for sess in gp["sessions"]:
            assert sess["present"] == []
            assert sess["missing"] == sess["expected"]
    assert report["total_missing_plots"] == report["total_expected_plots"]


def test_session_filter_normalizes_full_name(offline, monkeypatch):
    monkeypatch.setattr(plot_inventory, "_existing_data_types", lambda year: {})

    report = plot_inventory.compute_missing(year=2025, session="Race")
    session_types = {s["session_type"] for gp in report["grand_prix"] for s in gp["sessions"]}
    assert session_types == {"R"}


def test_generate_missing_skips_present_and_counts(offline, monkeypatch):
    calls = []

    def _make_spec(dt):
        return PlotSpec(dt, dt, frozenset(), lambda y, ident, e: calls.append((dt, y, ident, e)) or [1])

    fake_specs = [_make_spec("alpha"), _make_spec("beta")]
    monkeypatch.setattr(plot_inventory, "specs_for_session", lambda s: fake_specs)
    # 'alpha' already present for the race session; 'beta' missing.
    monkeypatch.setattr(
        plot_inventory, "_existing_data_types", lambda year: {(_GP_KEY, "R"): {"alpha"}}
    )

    job = plot_inventory.PlotGenJob(job_id="test", scope={})
    plot_inventory.generate_missing(job, year=2025, identifier=None, session="R")

    assert job.status == "completed"
    assert job.success == 1  # only beta generated
    assert job.skipped == 1  # alpha skipped
    assert [c[0] for c in calls] == ["beta"]
    # GP name preferred as the identifier (avoids pre-season offset).
    assert calls[0][2] == "Test Grand Prix"


def test_persist_result_specs_are_persisted(offline, monkeypatch):
    # Class-based features (persist_result=True) must be written to Mongo; plain
    # ones persist themselves and must NOT be double-stored here.
    persisted = []
    monkeypatch.setattr(
        plot_inventory, "persist_generated",
        lambda y, ident, e, dt, data: persisted.append(dt),
    )
    specs = [
        PlotSpec("plain", "plain", frozenset(), lambda y, i, e: [1], persist_result=False),
        PlotSpec("classy", "classy", frozenset(), lambda y, i, e: [1], persist_result=True),
    ]
    monkeypatch.setattr(plot_inventory, "specs_for_session", lambda s: specs)
    monkeypatch.setattr(plot_inventory, "_existing_data_types", lambda year: {})

    job = plot_inventory.PlotGenJob(job_id="test", scope={})
    plot_inventory.generate_missing(job, year=2025, identifier=None, session="R")

    assert job.success == 2
    assert persisted == ["classy"]  # only the persist_result spec


def test_parameterized_generates_per_driver_and_all_pairs(offline, monkeypatch):
    from src.services.analysis.v2.registry import DriverPlotSpec, PairPlotSpec

    dcalls, pcalls, persisted = [], [], []
    monkeypatch.setattr(plot_inventory, "session_drivers", lambda y, i, e: ["AAA", "BBB", "CCC"])
    monkeypatch.setattr(
        plot_inventory, "V2_PER_DRIVER_PLOTS",
        [DriverPlotSpec("d", lambda y, i, e, drv: dcalls.append(drv) or [1],
                        persist_key=lambda drv: f"d_{drv}")],
    )
    monkeypatch.setattr(
        plot_inventory, "V2_PAIR_PLOTS",
        [PairPlotSpec("p", lambda y, i, e, a, b: pcalls.append((a, b)) or [1])],
    )
    monkeypatch.setattr(plot_inventory, "specs_for_session", lambda s: [])  # isolate params
    monkeypatch.setattr(plot_inventory, "_existing_data_types", lambda year: {})
    monkeypatch.setattr(
        plot_inventory, "persist_generated",
        lambda y, i, e, dt, data: persisted.append(dt),
    )

    job = plot_inventory.PlotGenJob(job_id="t", scope={})
    plot_inventory.generate_missing(
        job, year=2025, identifier=None, session="R", include_comparisons=True
    )

    # 3 drivers x 1 per-driver spec = 3; C(3,2)=3 pairs x 1 pair spec = 3.
    assert job.total == 6
    assert job.success == 6
    assert sorted(dcalls) == ["AAA", "BBB", "CCC"]
    assert sorted(pcalls) == [("AAA", "BBB"), ("AAA", "CCC"), ("BBB", "CCC")]
    # Per-driver spec had a persist_key -> persisted; pair spec did not.
    assert sorted(persisted) == ["d_AAA", "d_BBB", "d_CCC"]


def test_parameterized_off_by_default(offline, monkeypatch):
    calls = []
    monkeypatch.setattr(plot_inventory, "session_drivers", lambda y, i, e: calls.append(1) or ["X"])
    monkeypatch.setattr(plot_inventory, "specs_for_session", lambda s: [])
    monkeypatch.setattr(plot_inventory, "_existing_data_types", lambda year: {})

    job = plot_inventory.PlotGenJob(job_id="t", scope={})
    plot_inventory.generate_missing(job, year=2025, identifier=None, session="R")
    # No include_comparisons -> driver list never fetched.
    assert calls == []
    assert job.total == 0


def test_generate_missing_force_regenerates(offline, monkeypatch):
    calls = []
    spec = PlotSpec("alpha", "alpha", frozenset(), lambda y, ident, e: calls.append(ident) or [1])
    monkeypatch.setattr(plot_inventory, "specs_for_session", lambda s: [spec])
    monkeypatch.setattr(
        plot_inventory, "_existing_data_types", lambda year: {(_GP_KEY, "R"): {"alpha"}}
    )

    job = plot_inventory.PlotGenJob(job_id="test", scope={})
    plot_inventory.generate_missing(job, year=2025, identifier=None, session="R", force=True)

    assert job.success == 1
    assert job.skipped == 0
    assert len(calls) == 1


def test_generate_missing_records_generator_errors(offline, monkeypatch):
    def _boom(y, ident, e):
        raise RuntimeError("kaboom")

    spec = PlotSpec("alpha", "alpha", frozenset(), _boom)
    monkeypatch.setattr(plot_inventory, "specs_for_session", lambda s: [spec])
    monkeypatch.setattr(plot_inventory, "_existing_data_types", lambda year: {})

    job = plot_inventory.PlotGenJob(job_id="test", scope={})
    plot_inventory.generate_missing(job, year=2025, identifier=None, session="R")

    assert job.status == "completed"  # one bad plot doesn't fail the whole job
    assert job.failed == 1
    assert any("kaboom" in err for err in job.errors)


def test_job_registry_roundtrip():
    job = plot_inventory.PlotGenJob(job_id="abc123", scope={"year": 2025}, started_at="t")
    plot_inventory._register_job(job)
    assert plot_inventory.get_job("abc123") is job
    assert any(j["job_id"] == "abc123" for j in plot_inventory.list_jobs())


# --------------------------------------------------------------------------- #
# Planner
# --------------------------------------------------------------------------- #
from src.services.analysis.v2.registry import (  # noqa: E402  (after the fixtures above)
    KIND_PER_DRIVER,
    KIND_PER_PAIR,
    DriverPlotSpec,
    PairPlotSpec,
)
from src.workers.plot_inventory import Selection  # noqa: E402


@pytest.fixture
def planner(offline, monkeypatch):
    """Catalog stubbed down to one spec per kind so unit counts are exact."""
    monkeypatch.setattr(plot_inventory, "session_drivers", lambda y, i, e: ["AAA", "BBB", "CCC"])
    monkeypatch.setattr(
        plot_inventory, "specs_for_session",
        lambda s: [PlotSpec("solo", "Solo", frozenset(), lambda y, i, e: [1])],
    )
    monkeypatch.setattr(
        plot_inventory, "V2_PER_DRIVER_PLOTS",
        [DriverPlotSpec("drv", lambda y, i, e, d: [1], stored_key=lambda d: f"drv_{d}")],
    )
    monkeypatch.setattr(
        plot_inventory, "V2_PAIR_PLOTS",
        [PairPlotSpec("pair", lambda y, i, e, a, b: [1],
                      stored_key=lambda a, b: f"pair_{a}_{b}")],
    )
    monkeypatch.setattr(plot_inventory, "V2_SEASON_PLOTS", [])
    monkeypatch.setattr(plot_inventory, "V2_CAREER_PLOTS", [])


def _plan(**kw):
    kw.setdefault("year", 2025)
    kw.setdefault("identifier", None)
    kw.setdefault("session", "R")
    return plot_inventory.build_plan(**kw)


def test_default_selection_is_singletons_only(planner):
    """Parameterized work is never implicit — it costs orders of magnitude more."""
    plan = _plan(selection=Selection())
    assert plan.by_feature == {"solo": 1}


def test_selecting_a_feature_by_key(planner):
    plan = _plan(selection=Selection(features=["drv"]))
    assert plan.by_feature == {"drv": 3}  # one per driver
    assert [u.data_type for u in plan.units] == ["drv_AAA", "drv_BBB", "drv_CCC"]


def test_selecting_a_whole_kind(planner):
    """Group selection stays correct as the catalog grows, unlike a frozen key list."""
    plan = _plan(selection=Selection(include_kinds={KIND_PER_DRIVER}))
    assert plan.by_feature == {"drv": 3}


def test_explicit_driver_list_narrows_the_plan(planner):
    plan = _plan(selection=Selection(features=["drv"], drivers=["bbb"]))
    # Lower-case input is normalized to the stored TLA casing.
    assert [u.data_type for u in plan.units] == ["drv_BBB"]


def test_pairs_are_combinations_by_default(planner):
    plan = _plan(selection=Selection(features=["pair"]))
    assert plan.by_feature == {"pair": 3}  # C(3,2)


def test_ordered_pair_features_plan_both_directions(offline, monkeypatch):
    """``track_comparison_{A}_{B}`` and ``..._{B}_{A}`` are different documents,
    so a backfill that only emits one ordering leaves the reverse a permanent miss.
    """
    monkeypatch.setattr(plot_inventory, "session_drivers", lambda y, i, e: ["AAA", "BBB"])
    monkeypatch.setattr(plot_inventory, "specs_for_session", lambda s: [])
    monkeypatch.setattr(plot_inventory, "V2_PER_DRIVER_PLOTS", [])
    monkeypatch.setattr(plot_inventory, "V2_SEASON_PLOTS", [])
    monkeypatch.setattr(plot_inventory, "V2_CAREER_PLOTS", [])
    monkeypatch.setattr(
        plot_inventory, "V2_PAIR_PLOTS",
        [PairPlotSpec("ord", lambda y, i, e, a, b: [1], ordered=True,
                      stored_key=lambda a, b: f"ord_{a}_{b}")],
    )

    plan = _plan(selection=Selection(include_kinds={KIND_PER_PAIR}))
    assert sorted(u.data_type for u in plan.units) == ["ord_AAA_BBB", "ord_BBB_AAA"]


def test_per_driver_applies_to_filters_by_session(offline, monkeypatch):
    """A race-only per-driver feature must not be planned for practice."""
    monkeypatch.setattr(plot_inventory, "session_drivers", lambda y, i, e: ["AAA"])
    monkeypatch.setattr(plot_inventory, "specs_for_session", lambda s: [])
    monkeypatch.setattr(plot_inventory, "V2_PAIR_PLOTS", [])
    monkeypatch.setattr(plot_inventory, "V2_SEASON_PLOTS", [])
    monkeypatch.setattr(plot_inventory, "V2_CAREER_PLOTS", [])
    monkeypatch.setattr(
        plot_inventory, "V2_PER_DRIVER_PLOTS",
        [DriverPlotSpec("raceonly", lambda y, i, e, d: [1],
                        applies_to=frozenset({"R", "S"}))],
    )

    assert _plan(session="R", selection=Selection(features=["raceonly"])).units
    assert _plan(session="Q", selection=Selection(features=["raceonly"])).units == []


def test_lap_feature_requires_an_explicit_range(offline, monkeypatch):
    """drivers x laps is the largest key space in the catalog; unbounded is a mistake."""
    from src.services.analysis.v2.registry import DriverLapPlotSpec

    monkeypatch.setattr(plot_inventory, "session_drivers", lambda y, i, e: ["AAA"])
    monkeypatch.setattr(plot_inventory, "specs_for_session", lambda s: [])
    monkeypatch.setattr(plot_inventory, "V2_PER_DRIVER_PLOTS", [])
    monkeypatch.setattr(plot_inventory, "V2_PAIR_PLOTS", [])
    monkeypatch.setattr(plot_inventory, "V2_SEASON_PLOTS", [])
    monkeypatch.setattr(plot_inventory, "V2_CAREER_PLOTS", [])
    monkeypatch.setattr(
        plot_inventory, "V2_PER_DRIVER_LAP_PLOTS",
        [DriverLapPlotSpec("laps", lambda y, i, e, d, lap: [1],
                           stored_key=lambda d, lap: f"laps_{d}_{lap}")],
    )
    monkeypatch.setattr(
        plot_inventory, "session_driver_laps", lambda y, i, e: {"AAA": [1, 2, 3, 4, 5]}
    )

    without = _plan(selection=Selection(features=["laps"]))
    assert without.units == []
    assert any("lap range" in w for w in without.warnings)

    ranged = _plan(selection=Selection(features=["laps"], lap_from=2, lap_to=4))
    assert [u.data_type for u in ranged.units] == ["laps_AAA_2", "laps_AAA_3", "laps_AAA_4"]


def test_plan_is_capped(planner):
    plan = _plan(selection=Selection(features=["drv"], max_units=2))
    assert len(plan.units) == 2
    assert plan.truncated is True
    assert any("truncated" in w for w in plan.warnings)


def test_estimate_reports_per_feature_breakdown(planner):
    est = plot_inventory.estimate_plan(
        year=2025, identifier=None, session="R",
        selection=Selection(features=["solo", "drv", "pair"]),
    )
    assert est["units"] == 1 + 3 + 3
    by_feature = {row["feature"]: row["units"] for row in est["by_feature"]}
    assert by_feature == {"solo": 1, "drv": 3, "pair": 3}


def test_units_group_by_session_for_concurrency(planner):
    """Units sharing a session must share a group so they share a warm store."""
    plan = plot_inventory.build_plan(
        year=2025, identifier=None, session=None,
        selection=Selection(features=["drv"]),
    )
    groups = {u.group_key() for u in plan.units}
    # The fake schedule has one GP with Race + Qualifying.
    assert len(groups) == 2


def test_execute_plan_honours_cancellation(planner, monkeypatch):
    ran = []
    monkeypatch.setattr(plot_inventory, "_existing_data_types", lambda year: {})
    monkeypatch.setattr(plot_inventory.admin_jobs, "is_cancelled", lambda jid: True)

    spec = DriverPlotSpec("drv", lambda y, i, e, d: ran.append(d) or [1])
    monkeypatch.setattr(plot_inventory, "V2_PER_DRIVER_PLOTS", [spec])

    plan = _plan(selection=Selection(features=["drv"]))
    job = plot_inventory.PlotGenJob(job_id="cancel-me", scope={})
    plot_inventory.execute_plan(job, plan)

    assert job.status == "cancelled"
    assert ran == []  # cancelled before the first unit


def test_execute_plan_records_per_feature_outcomes(planner, monkeypatch):
    monkeypatch.setattr(plot_inventory, "_existing_data_types", lambda year: {})
    monkeypatch.setattr(plot_inventory.admin_jobs, "is_cancelled", lambda jid: False)

    def _half_broken(y, i, e, d):
        if d == "BBB":
            raise RuntimeError("nope")
        return [1]

    monkeypatch.setattr(
        plot_inventory, "V2_PER_DRIVER_PLOTS",
        [DriverPlotSpec("drv", _half_broken, stored_key=lambda d: f"drv_{d}")],
    )

    plan = _plan(selection=Selection(features=["drv"]))
    job = plot_inventory.PlotGenJob(job_id="mixed", scope={})
    plot_inventory.execute_plan(job, plan)

    assert job.status == "completed"
    assert job.per_feature["drv"] == {"success": 2, "failed": 1, "skipped": 0}


def test_scopes_overlap_treats_missing_fields_as_everything():
    whole_year = {"year": 2025, "gp": None, "session": None}
    one_race = {"year": 2025, "gp": "Monaco Grand Prix", "session": "R"}
    other_year = {"year": 2024, "gp": "Monaco Grand Prix", "session": "R"}

    assert plot_inventory.scopes_overlap(whole_year, one_race) is True
    assert plot_inventory.scopes_overlap(one_race, whole_year) is True
    assert plot_inventory.scopes_overlap(other_year, one_race) is False
    assert plot_inventory.scopes_overlap(
        one_race, {"year": 2025, "gp": "Monaco Grand Prix", "session": "Q"}
    ) is False


def test_extra_stored_keys_are_grouped_not_listed(offline, monkeypatch):
    """A race with 1,000 lap_all_data documents must render as one row."""
    extras = {f"lap_all_data_VER_{lap}" for lap in range(1, 60)}
    extras |= {"track_map_speed_VER", "track_map_speed_NOR", "some_legacy_key"}
    monkeypatch.setattr(
        plot_inventory, "_existing_data_types", lambda year: {(_GP_KEY, "R"): extras},
    )

    report = plot_inventory.compute_inventory(year=2025, session="R")
    sess = report["grand_prix"][0]["sessions"][0]
    groups = {g["prefix"]: g["count"] for g in sess["extra_groups"]}

    assert groups["lap_all_data"] == 59
    assert groups["track_map_speed"] == 2
    assert groups["other"] == 1
    assert sess["extra_count"] == len(extras)
    # Sample is bounded so the payload stays small.
    assert all(len(g["sample"]) <= 6 for g in sess["extra_groups"])
