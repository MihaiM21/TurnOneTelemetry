"""Tests for scoped storage cleanup.

This module deletes things, so the tests are weighted toward what must NOT
happen: no escaping the plots root, no purging the auth namespace out of Redis,
no unscoped wipe without an explicit opt-in, and no deleting anything the
preview did not show.

Every filesystem test builds a real tree under ``tmp_path`` and ``chdir``s into
it, because ``plots_root()`` resolves against the current working directory by
design (see ``src/services/plotting/output.py``).
"""
import pytest

from src.services.plotting.output import UnsafeOutputPath
from src.services.storage_cleanup import (
    ALL_LAYERS,
    CleanupError,
    CleanupItem,
    CleanupScope,
    LAYER_BUNDLES,
    LAYER_PLOTS,
    LAYER_RAW,
    LAYER_REDIS,
    _delete_plots,
    _delete_redis,
    _plot_matches_data_type,
    _redis_patterns,
    execute_cleanup,
    find_orphan_plot_dirs,
    normalize_session,
    plan_cleanup,
    storage_totals,
)

PLOTS_ONLY = (LAYER_PLOTS,)


@pytest.fixture
def plot_tree(tmp_path, monkeypatch):
    """A miniature ``outputs/plots`` reproducing the real tree's quirks:
    a GP stored under two spellings, and both long and short session names."""
    files = {
        "2023/BritishGrandPrix/Race/Top speed comparison 2023 British GP Race.png": 100,
        "2023/BritishGrandPrix/Race/Speed Distribution 2023 British GP Race VER.png": 200,
        "2023/BritishGrandPrix/Q/Speed Distribution 2023 British GP Q VER.png": 300,
        "2025/British Grand Prix/R/Speed Distribution 2025 British GP R NOR.png": 400,
        "2025/BritishGrandPrix/R/Top speed comparison 2025 British GP R.png": 500,
    }
    root = tmp_path / "outputs" / "plots"
    for rel, size in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    monkeypatch.chdir(tmp_path)
    return root


# --------------------------------------------------------------------------- #
# Scope matching
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "value,expected",
    [("R", "R"), ("Race", "R"), ("race", "R"), ("SPRINT", "S"), ("Qualifying", "Q"),
     ("quali", "Q"), ("Practice 1", "FP1"), ("FP2", "FP2"), ("Sprint Shootout", "SQ")],
)
def test_session_spellings_fold_onto_one_token(value, expected):
    """The plot tree contains both `Race` and `R` for the same thing, so a
    scope of `session=R` has to reach the legacy directories too."""
    assert normalize_session(value) == expected


def test_gp_matches_any_identifier_a_layer_knows():
    """No single layer knows all three identifiers, so any one matching wins."""
    scope = CleanupScope(gp="Italian Grand Prix")
    assert scope.matches_gp("2025_ITA", 16, "Italian Grand Prix")
    assert scope.matches_gp(None, None, "ItalianGrandPrix")  # spacing ignored
    assert not scope.matches_gp("2025_MON", 8, "Monaco Grand Prix")


def test_absent_filters_match_everything():
    scope = CleanupScope()
    assert scope.is_unscoped
    assert scope.matches_year(2025) and scope.matches_session("R")
    assert scope.matches_gp("anything") and scope.matches_data_type("anything")


def test_unknown_layer_is_rejected():
    with pytest.raises(ValueError, match="Unknown storage layer"):
        CleanupScope(layers=("mongo", "not_a_layer"))


def test_empty_layer_selection_is_rejected():
    with pytest.raises(ValueError, match="At least one storage layer"):
        CleanupScope(layers=())


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #

def test_plan_enumerates_every_file(plot_tree):
    plan = plan_cleanup(CleanupScope(layers=PLOTS_ONLY))
    assert plan["total_items"] == 5
    assert plan["total_bytes"] == 1500
    assert plan["unscoped"] is True


def test_year_scope_narrows(plot_tree):
    plan = plan_cleanup(CleanupScope(year=2023, layers=PLOTS_ONLY))
    assert plan["total_items"] == 3
    assert plan["unscoped"] is False


def test_session_scope_matches_legacy_long_spelling(plot_tree):
    """`session=R` must find 2023's `Race/` directory, not just 2025's `R/`."""
    plan = plan_cleanup(CleanupScope(session="R", layers=PLOTS_ONLY))
    keys = {i["key"] for i in plan["layers"][LAYER_PLOTS]["items"]}
    assert any("/Race/" in k for k in keys), "legacy 'Race' directory was missed"
    assert any("/R/" in k for k in keys)
    assert len(keys) == 4


def test_data_type_scope_matches_human_plot_filenames(plot_tree):
    plan = plan_cleanup(CleanupScope(data_type="speed_distribution", layers=PLOTS_ONLY))
    assert plan["total_items"] == 3
    assert all("Speed Distribution" in i["key"] for i in plan["layers"][LAYER_PLOTS]["items"])


def test_data_type_scope_excludes_per_session_layers():
    """Raw streams and bundles are per-session. Deleting them for one feature
    would force every other feature of that session to re-download, so a
    data_type scope must return none of them."""
    scope = CleanupScope(year=2025, data_type="speed_distribution",
                         layers=(LAYER_RAW, LAYER_BUNDLES))
    plan = plan_cleanup(scope)
    assert plan["layers"][LAYER_RAW]["count"] == 0
    assert plan["layers"][LAYER_BUNDLES]["count"] == 0


def test_plan_never_mutates_storage(plot_tree):
    before = sorted(p.name for p in plot_tree.rglob("*") if p.is_file())
    plan_cleanup(CleanupScope(layers=PLOTS_ONLY))
    plan_cleanup(CleanupScope(year=2023, layers=PLOTS_ONLY))
    after = sorted(p.name for p in plot_tree.rglob("*") if p.is_file())
    assert before == after


# --------------------------------------------------------------------------- #
# Confirm token
# --------------------------------------------------------------------------- #

def test_token_is_stable_for_unchanged_storage(plot_tree):
    scope = CleanupScope(year=2023, layers=PLOTS_ONLY)
    assert plan_cleanup(scope)["confirm_token"] == plan_cleanup(scope)["confirm_token"]


def test_token_changes_when_storage_changes(plot_tree):
    scope = CleanupScope(year=2023, layers=PLOTS_ONLY)
    first = plan_cleanup(scope)["confirm_token"]
    new_file = plot_tree / "2023" / "BritishGrandPrix" / "Race" / "extra.png"
    new_file.write_bytes(b"y" * 50)
    assert plan_cleanup(scope)["confirm_token"] != first


def test_purge_refuses_a_stale_token(plot_tree):
    """The whole safety property: a purge can only delete what a preview showed."""
    scope = CleanupScope(year=2023, layers=PLOTS_ONLY)
    token = plan_cleanup(scope)["confirm_token"]
    (plot_tree / "2023" / "BritishGrandPrix" / "Race" / "appeared_later.png").write_bytes(b"z")

    with pytest.raises(CleanupError, match="changed since the preview"):
        execute_cleanup(scope, token)

    assert (plot_tree / "2023" / "BritishGrandPrix" / "Race" / "appeared_later.png").exists()


def test_purge_refuses_a_wrong_token(plot_tree):
    scope = CleanupScope(year=2023, layers=PLOTS_ONLY)
    with pytest.raises(CleanupError, match="changed since the preview"):
        execute_cleanup(scope, "0" * 32)
    assert len(list(plot_tree.rglob("*.png"))) == 5


# --------------------------------------------------------------------------- #
# Unscoped-purge guard
# --------------------------------------------------------------------------- #

def test_unscoped_purge_refused_without_explicit_opt_in(plot_tree):
    scope = CleanupScope(layers=PLOTS_ONLY)
    token = plan_cleanup(scope)["confirm_token"]
    with pytest.raises(CleanupError, match="Refusing an unscoped purge"):
        execute_cleanup(scope, token)
    assert len(list(plot_tree.rglob("*.png"))) == 5


def test_unscoped_purge_allowed_with_opt_in(plot_tree):
    scope = CleanupScope(layers=PLOTS_ONLY)
    token = plan_cleanup(scope)["confirm_token"]
    result = execute_cleanup(scope, token, allow_full_purge=True)
    assert result["ok"] and result["total_deleted"] == 5
    assert list(plot_tree.rglob("*.png")) == []


# --------------------------------------------------------------------------- #
# Deletion behaviour
# --------------------------------------------------------------------------- #

def test_scoped_purge_deletes_only_the_scope(plot_tree):
    scope = CleanupScope(year=2023, layers=PLOTS_ONLY)
    token = plan_cleanup(scope)["confirm_token"]
    result = execute_cleanup(scope, token)

    assert result["ok"] and result["total_deleted"] == 3
    assert result["bytes_reclaimed"] == 600
    assert not (plot_tree / "2023").exists()
    assert len(list((plot_tree / "2025").rglob("*.png"))) == 2


def test_purge_prunes_directories_it_empties(plot_tree):
    """A cleared scope should not leave an empty skeleton behind."""
    scope = CleanupScope(year=2023, session="Q", layers=PLOTS_ONLY)
    token = plan_cleanup(scope)["confirm_token"]
    execute_cleanup(scope, token)

    assert not (plot_tree / "2023" / "BritishGrandPrix" / "Q").exists()
    # The sibling session still has files, so its parents must survive.
    assert (plot_tree / "2023" / "BritishGrandPrix" / "Race").is_dir()


def test_purge_reports_missing_items_without_raising(plot_tree):
    items = [CleanupItem(layer=LAYER_PLOTS, key="2023/Nope/R/gone.png", label="gone")]
    deleted, errors = _delete_plots(items)
    assert deleted == 0
    assert errors and "not found" in errors[0]


# --------------------------------------------------------------------------- #
# Containment
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "evil",
    ["../../../../etc/passwd", "../../secret.txt", "2023/../../../outside.png"],
)
def test_deletion_refuses_paths_escaping_the_plots_root(plot_tree, evil):
    """The plan round-trips through an HTTP request as JSON, so a crafted key
    would otherwise reach unlink() directly. Re-resolved through the same sink
    guard the write path uses."""
    outside = plot_tree.parent.parent / "outside.png"
    outside.write_bytes(b"do not delete me")

    deleted, errors = _delete_plots([
        CleanupItem(layer=LAYER_PLOTS, key=evil, label=evil)
    ])

    assert deleted == 0
    assert errors and "Refusing to write outside" in errors[0]
    assert outside.exists(), "a path outside the plots root was deleted"


def test_resolve_within_still_raises_directly(plot_tree):
    from src.services.plotting.output import resolve_within
    with pytest.raises(UnsafeOutputPath):
        resolve_within("../../../../etc/passwd")


# --------------------------------------------------------------------------- #
# Redis safety
# --------------------------------------------------------------------------- #

def test_redis_patterns_stay_in_the_v2_namespace():
    for scope in (CleanupScope(), CleanupScope(year=2025)):
        for pattern in _redis_patterns(scope):
            assert pattern.startswith("t1api:v2:")


def test_redis_purge_refuses_the_auth_namespace(monkeypatch):
    """`t1api:auth:*` holds live API-key resolutions. Purging it would sign
    every caller out mid-request, so the prefix guard is asserted, not assumed."""
    called = []

    class _Spy:
        enabled = True

        def delete_pattern(self, pattern):
            called.append(pattern)
            return 1

    monkeypatch.setattr("src.core.cache.redis_cache.get_sync_cache", lambda: _Spy())

    deleted, errors = _delete_redis(["t1api:auth:key:*"])
    assert deleted == 0
    assert errors and "refused" in errors[0]
    assert called == [], "a non-v2 pattern reached Redis"


def test_redis_purge_allows_the_v2_namespace(monkeypatch):
    called = []

    class _Spy:
        enabled = True

        def delete_pattern(self, pattern):
            called.append(pattern)
            return 3

    monkeypatch.setattr("src.core.cache.redis_cache.get_sync_cache", lambda: _Spy())

    deleted, errors = _delete_redis(["t1api:v2:plot:2025:*"])
    assert deleted == 3 and errors == []
    assert called == ["t1api:v2:plot:2025:*"]


def test_redis_is_excluded_from_the_confirm_token(plot_tree):
    """Redis contents shift under TTL, so including them would invalidate every
    token within seconds. The token must depend only on durable layers."""
    with_redis = CleanupScope(year=2023, layers=(LAYER_PLOTS, LAYER_REDIS))
    without = CleanupScope(year=2023, layers=PLOTS_ONLY)
    assert plan_cleanup(with_redis)["confirm_token"] == plan_cleanup(without)["confirm_token"]


# --------------------------------------------------------------------------- #
# data_type filename matching
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "filename,data_type,expected",
    [
        ("Speed Distribution 2023 British GP Race VER.png", "speed_distribution", True),
        ("Top speed comparison 2023 British GP Race.png", "speed_distribution", False),
        ("Top speed comparison Telemetry 2023 GP Race.png", "top_speed", True),
        ("Speed Distribution 2023 GP Race.png", "top_speed", False),
    ],
)
def test_plot_filename_matching(filename, data_type, expected):
    assert _plot_matches_data_type(filename, data_type) is expected


# --------------------------------------------------------------------------- #
# Orphan detection
# --------------------------------------------------------------------------- #

def test_orphan_detection_finds_duplicate_gp_spellings(plot_tree):
    groups = find_orphan_plot_dirs()
    assert len(groups) == 1

    group = groups[0]
    assert group["year"] == 2025
    assert {v["name"] for v in group["variants"]} == {"British Grand Prix", "BritishGrandPrix"}
    assert group["reclaimable_bytes"] == 400


def test_orphan_detection_never_deletes(plot_tree):
    before = sorted(str(p) for p in plot_tree.rglob("*"))
    find_orphan_plot_dirs()
    assert sorted(str(p) for p in plot_tree.rglob("*")) == before


def test_storage_totals_counts_the_plot_tree(plot_tree):
    totals = storage_totals()
    assert totals["plots"]["files"] == 5
    assert totals["plots"]["bytes"] == 1500
    assert totals["orphan_plot_groups"] == 1


def test_all_layers_are_covered_by_the_default_scope():
    """A new layer added to ALL_LAYERS must be reachable by a default cleanup,
    otherwise it silently accumulates forever -- which is how the plots layer
    got into this state in the first place."""
    assert set(CleanupScope().layers) == set(ALL_LAYERS)
