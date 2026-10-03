"""Social pack worker: planning, pair derivation, the run, cancellation and the single-job guard.

Fully offline. Chart callables are replaced by fakes that write tiny PNGs into
``tmp_path``; the session store is a stand-in exposing only what the worker reads.
"""

from __future__ import annotations

import json
import threading
import time
import zipfile

import pandas as pd
import pytest

from src.core.config import settings
from src.repositories import admin_jobs
from src.workers import social_pack as sp

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image"

DRIVERS = {
    "1": {"tla": "VER", "team": "Red Bull"},
    "22": {"tla": "TSU", "team": "Red Bull"},
    "4": {"tla": "NOR", "team": "McLaren"},
    "81": {"tla": "PIA", "team": "McLaren"},
    "16": {"tla": "LEC", "team": "Ferrari"},
    "44": {"tla": "HAM", "team": "Ferrari"},
}


class FakeStore:
    event_name = "Italian Grand Prix"
    base_url = "https://example.invalid/"
    client = object()

    def driver_list(self):
        return DRIVERS


def _order(monkeypatch, nums):
    """Classification: ``nums`` in finishing order, for both race and quali helpers."""
    monkeypatch.setattr(sp, "get_finishing_order", lambda *a, **k: {n: i + 1 for i, n in enumerate(nums)})
    monkeypatch.setattr(sp, "get_qualifying_classification",
                        lambda *a, **k: pd.DataFrame({"DriverNum": list(nums)}))


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "social_dir", str(tmp_path / "social"))
    monkeypatch.setattr(sp, "build_session_store", lambda *a, **k: FakeStore())
    _order(monkeypatch, ["1", "4", "81", "16", "44", "22"])
    admin_jobs._collection().delete_many({})
    with sp._JOBS_LOCK:
        sp._JOBS.clear()
    sp.CANCELLED.clear()
    yield
    with sp._JOBS_LOCK:
        sp._JOBS.clear()
    sp.CANCELLED.clear()


@pytest.fixture
def fake_charts(tmp_path, monkeypatch):
    """Replace every plot/data caller with fakes; returns the call log."""
    src = tmp_path / "rendered"
    src.mkdir()
    calls = []

    def make(chart):
        def plot(y, gp, e, task):
            calls.append((chart, task.fmt, task.drivers))
            path = src / f"{task.slug}_{task.fmt}.png"
            path.write_bytes(PNG)
            return str(path)
        return plot

    monkeypatch.setattr(sp, "PLOT_CALLERS", {slug: make(slug) for slug in sp.PLOT_CALLERS})
    monkeypatch.setattr(sp, "DATA_CALLERS", {
        "field_dominance": lambda y, gp, e, t: {"highlights": {"dominant_team": "McLaren"}},
        "lap_duel": lambda y, gp, e, t: {"highlights": {"top_speed_kmh": 331, "pair": list(t.drivers)}},
        "sector_gap": lambda y, gp, e, t: {"no_highlights_here": True},
    })
    return calls


def _slugs(tasks):
    return sorted({t.slug for t in tasks})


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #
def test_format_names_match_the_canvas():
    from src.services.plotting import canvas

    assert sp.FORMAT_NAMES == tuple(canvas.FORMAT_NAMES)


def test_plan_qualifying_picks_quali_charts_only():
    tasks = sp.plan_social_pack(2025, "Italian Grand Prix", "Q", ["portrait"])
    slugs = _slugs(tasks)
    assert {"quali_results", "theoretical_best", "sector_gap", "top_speed",
            "field_dominance", "corner_speed_profile", "efficiency_scatter"} <= set(slugs)
    assert not {"race_story", "position_changes", "pit_strategy"} & set(slugs)


def test_plan_race_picks_race_charts_only():
    slugs = _slugs(sp.plan_social_pack(2025, 16, "R", ["portrait"]))
    assert {"race_story", "position_changes", "pit_strategy", "top_speed", "field_dominance"} <= set(slugs)
    assert not {"quali_results", "theoretical_best", "sector_gap"} & set(slugs)


def test_plan_sprint_counts_as_race_and_sprint_quali_as_quali():
    assert "race_story" in _slugs(sp.plan_social_pack(2025, 16, "S", ["story"]))
    assert "quali_results" in _slugs(sp.plan_social_pack(2025, 16, "SQ", ["story"]))
    assert "quali_results" in _slugs(sp.plan_social_pack(2025, 16, "Qualifying", ["story"]))


def test_plan_practice_has_neither_quali_nor_race_charts():
    slugs = _slugs(sp.plan_social_pack(2025, 16, "FP1", ["portrait"]))
    assert "top_speed" in slugs and "field_dominance" in slugs
    assert not {"race_story", "quali_results", "theoretical_best"} & set(slugs)


def test_plan_energy_clipping_is_gated_to_2026():
    assert "energy_clipping" not in _slugs(sp.plan_social_pack(2025, 16, "R", ["portrait"]))
    assert "energy_clipping" in _slugs(sp.plan_social_pack(2026, 16, "R", ["portrait"]))
    assert sp.gated_out(2025, "R") == ["energy_clipping"]
    assert sp.gated_out(2026, "R") == []


def test_plan_multiplies_charts_by_formats_in_canonical_order():
    tasks = sp.plan_social_pack(2025, 16, "R", ["story", "portrait", "story"])
    per_chart = [t.fmt for t in tasks if t.chart == "race_story"]
    assert per_chart == ["portrait", "story"]


def test_plan_pairs_add_pair_charts_per_format():
    tasks = sp.plan_social_pack(2025, 16, "R", ["portrait", "story"], pairs=[["ver", "nor"]])
    duel = [t for t in tasks if t.chart == "lap_duel"]
    assert [(t.slug, t.fmt) for t in duel] == [("lap_duel_VER_NOR", "portrait"), ("lap_duel_VER_NOR", "story")]
    assert {t.chart for t in tasks if t.drivers} == {"lap_duel", "track_comparison"}


def test_plan_without_pairs_has_no_pair_charts_and_season_is_opt_in():
    tasks = sp.plan_social_pack(2025, 16, "R", ["portrait"])
    assert not [t for t in tasks if t.drivers]
    assert "season_teammate_battle" not in _slugs(tasks)
    with_season = sp.plan_social_pack(2025, 16, "R", ["portrait"], include_season=True)
    assert "season_teammate_battle" in _slugs(with_season)


def test_plan_defaults_and_validation():
    tasks = sp.plan_social_pack(2025, 16, "R")
    assert {t.fmt for t in tasks} == {"portrait", "story", "landscape"}
    with pytest.raises(ValueError):
        sp.plan_social_pack(2025, 16, "XX")
    with pytest.raises(ValueError):
        sp.plan_social_pack(2025, 16, "R", ["widescreen"])
    with pytest.raises(ValueError):
        sp.plan_social_pack(2025, 16, "R", pairs=[["VER", "VER"]])
    with pytest.raises(ValueError):
        sp.plan_social_pack(2025, 16, "R", pairs=[["VERSTAPPEN", "NOR"]])


def test_parse_pairs_text():
    assert sp.parse_pairs_text("") is None
    assert sp.parse_pairs_text("ver,nor\n  LEC HAM \n") == [("VER", "NOR"), ("LEC", "HAM")]
    with pytest.raises(ValueError):
        sp.parse_pairs_text("VER")


# --------------------------------------------------------------------------- #
# Pair derivation
# --------------------------------------------------------------------------- #
def test_derive_pairs_p1_p2_then_top3_vs_teammate(monkeypatch):
    _order(monkeypatch, ["1", "4", "81", "16", "44", "22"])  # VER NOR PIA LEC HAM TSU
    pairs = sp.derive_pairs(FakeStore(), "R")
    # (PIA, NOR) is the same duel as (NOR, PIA) and is dropped.
    assert pairs == [("VER", "NOR"), ("VER", "TSU"), ("NOR", "PIA")]


def test_derive_pairs_dedupes_when_p1_and_p2_are_teammates(monkeypatch):
    _order(monkeypatch, ["4", "81", "1", "16", "44", "22"])  # NOR PIA VER ...
    pairs = sp.derive_pairs(FakeStore(), "R")
    assert pairs == [("NOR", "PIA"), ("VER", "TSU")]


def test_derive_pairs_is_capped(monkeypatch):
    _order(monkeypatch, ["1", "4", "16", "81", "44", "22"])  # VER NOR LEC: three different teams
    pairs = sp.derive_pairs(FakeStore(), "R", max_pairs=3)
    assert pairs == [("VER", "NOR"), ("VER", "TSU"), ("NOR", "PIA")]
    assert len(sp.derive_pairs(FakeStore(), "R")) <= sp.MAX_DERIVED_PAIRS


def test_derive_pairs_uses_the_qualifying_classification_for_q(monkeypatch):
    seen = {}
    monkeypatch.setattr(sp, "get_qualifying_classification",
                        lambda *a, **k: seen.setdefault("q", True) and pd.DataFrame({"DriverNum": ["16", "44"]}))
    pairs = sp.derive_pairs(FakeStore(), "Q")
    assert seen == {"q": True}
    assert pairs[0] == ("LEC", "HAM")
    assert len(pairs) == 1  # LEC's teammate is HAM: the same duel


def test_derive_pairs_survives_an_empty_classification(monkeypatch):
    monkeypatch.setattr(sp, "get_finishing_order", lambda *a, **k: {})
    assert sp.derive_pairs(FakeStore(), "R") == []


# --------------------------------------------------------------------------- #
# Running a pack
# --------------------------------------------------------------------------- #
def _pack_dir(year=2025, session="R"):
    return sp.pack_dir(f"{year}/ItalianGrandPrix/{session}")


def test_run_writes_layout_highlights_manifest_and_zip(fake_charts):
    admin_jobs.create_job("a00000000001", kind=sp.JOB_KIND, scope={}, total=0)
    job = sp.run_social_pack("a00000000001", 2025, 16, "R", ["portrait", "story"], include_season=False)

    assert job.status == "completed", job.errors
    base = _pack_dir()
    for fmt in ("portrait", "story"):
        assert (base / fmt / "race_story.png").read_bytes() == PNG
        assert (base / fmt / "lap_duel_VER_NOR.png").is_file()
        assert (base / fmt / "track_comparison_VER_TSU.png").is_file()
    assert not (base / "landscape").exists()
    assert job.failed == 0 and job.success == job.done == job.total == job.files

    highlights = json.loads((base / "highlights.json").read_text(encoding="utf-8"))
    assert highlights["field_dominance"] == {"dominant_team": "McLaren"}
    assert highlights["lap_duel_VER_NOR"]["top_speed_kmh"] == 331
    assert highlights["lap_duel_NOR_PIA"]["pair"] == ["NOR", "PIA"]
    assert "sector_gap" not in highlights and "race_story" not in highlights

    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["pairs_source"] == "derived"
    assert manifest["pairs"] == [["VER", "NOR"], ["VER", "TSU"], ["NOR", "PIA"]]
    assert manifest["errors"] == []
    assert len(manifest["items"]) == job.success
    item = next(i for i in manifest["items"] if i["slug"] == "race_story" and i["format"] == "story")
    assert item["file"] == "story/race_story.png" and item["bytes"] == len(PNG)
    assert manifest["timings"]["total_seconds"] >= 0
    assert any("energy_clipping skipped" in w for w in manifest["warnings"])

    zip_file = base / "social_pack.zip"
    assert zip_file.is_file() and not (base / "social_pack.zip.tmp").exists()
    with zipfile.ZipFile(zip_file) as zf:
        names = zf.namelist()
        assert zf.getinfo(names[0]).compress_type == zipfile.ZIP_STORED
    top = "2025_ItalianGrandPrix_R"
    assert f"{top}/story/race_story.png" in names and f"{top}/manifest.json" in names
    assert f"{top}/highlights.json" in names
    assert not any(n.endswith(".zip") or n.endswith(".tmp") for n in names)

    stored = admin_jobs.get_job("a00000000001")
    assert stored["status"] == "completed" and stored["zip_ready"] is True
    assert stored["pack"] == "2025/ItalianGrandPrix/R"
    assert sp.pack_download_path("a00000000001") == zip_file.resolve()


def test_run_uses_explicit_pairs_and_skips_derivation(fake_charts, monkeypatch):
    monkeypatch.setattr(sp, "derive_pairs", lambda *a, **k: pytest.fail("must not derive"))
    job = sp.run_social_pack("a00000000002", 2025, 16, "R", ["portrait"], pairs=[["lec", "ham"]])
    assert job.status == "completed"
    assert job.pairs == [["LEC", "HAM"]]
    manifest = json.loads((_pack_dir() / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pairs_source"] == "request"
    assert (_pack_dir() / "portrait" / "lap_duel_LEC_HAM.png").is_file()


def test_run_records_a_failing_chart_and_carries_on(fake_charts, monkeypatch):
    def boom(y, gp, e, task):
        raise RuntimeError("no telemetry for that lap")

    callers = dict(sp.PLOT_CALLERS)
    callers["track_comparison"] = boom
    callers["pit_strategy"] = lambda *a: ""  # a chart that quietly renders nothing
    monkeypatch.setattr(sp, "PLOT_CALLERS", callers)

    job = sp.run_social_pack("a00000000003", 2025, 16, "R", ["portrait"])

    assert job.status == "completed"
    n_track = 3  # three derived pairs
    assert job.failed == n_track + 1
    assert job.success == job.total - job.failed
    assert any("track_comparison_VER_NOR portrait" in e and "no telemetry" in e for e in job.errors)
    manifest = json.loads((_pack_dir() / "manifest.json").read_text(encoding="utf-8"))
    errs = {(e["slug"], e["format"]): e["error"] for e in manifest["errors"]}
    assert "RuntimeError: no telemetry for that lap" == errs[("track_comparison_VER_NOR", "portrait")]
    assert ("pit_strategy", "portrait") in errs
    assert not (_pack_dir() / "portrait" / "track_comparison_VER_NOR.png").exists()
    assert (_pack_dir() / "portrait" / "race_story.png").is_file()
    assert (_pack_dir() / "social_pack.zip").is_file()


def test_run_when_every_chart_fails_the_job_fails(fake_charts, monkeypatch):
    def boom(*a):
        raise RuntimeError("down")

    monkeypatch.setattr(sp, "PLOT_CALLERS", {slug: boom for slug in sp.PLOT_CALLERS})
    job = sp.run_social_pack("a00000000004", 2025, 16, "R", ["portrait"])
    assert job.status == "failed" and job.failed == job.total and job.success == 0


def test_run_fails_cleanly_when_the_session_cannot_be_resolved(fake_charts, monkeypatch):
    monkeypatch.setattr(sp, "build_session_store", lambda *a, **k: None)
    job = sp.run_social_pack("a00000000005", 2025, 16, "R", ["portrait"])
    assert job.status == "failed"
    assert any("could not resolve" in e for e in job.errors)
    assert job.pack is None and not job.zip_ready


def test_run_survives_a_pair_derivation_failure(fake_charts, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("classification unavailable")

    monkeypatch.setattr(sp, "derive_pairs", boom)
    job = sp.run_social_pack("a00000000006", 2025, 16, "R", ["portrait"])
    assert job.status == "completed"
    assert any("pair derivation failed" in w for w in job.warnings)
    assert not (_pack_dir() / "portrait" / "lap_duel_VER_NOR.png").exists()
    assert (_pack_dir() / "portrait" / "race_story.png").is_file()


def test_run_includes_the_season_chart_when_asked(fake_charts):
    job = sp.run_social_pack("a00000000007", 2025, 16, "R", ["square"], pairs=[], include_season=True)
    assert job.status == "completed"
    assert (_pack_dir() / "square" / "season_teammate_battle.png").is_file()


def test_run_a_failing_highlights_lookup_is_only_a_warning(fake_charts, monkeypatch):
    def boom(y, gp, e, t):
        raise RuntimeError("payload gone")

    data = dict(sp.DATA_CALLERS)
    data["field_dominance"] = boom
    monkeypatch.setattr(sp, "DATA_CALLERS", data)
    job = sp.run_social_pack("a00000000008", 2025, 16, "R", ["portrait"])
    assert job.status == "completed" and job.failed == 0
    assert any("field_dominance: highlights unavailable" in w for w in job.warnings)


def test_run_rerun_overwrites_in_place(fake_charts):
    sp.run_social_pack("a00000000009", 2025, 16, "R", ["portrait"])
    job = sp.run_social_pack("a0000000000a", 2025, 16, "R", ["portrait"])
    assert job.status == "completed"
    with zipfile.ZipFile(_pack_dir() / "social_pack.zip") as zf:
        assert len(zf.namelist()) == len(set(zf.namelist()))


def test_run_rejects_a_bad_session_before_touching_disk():
    with pytest.raises(ValueError):
        sp.run_social_pack("a0000000000b", 2025, 16, "../../etc", ["portrait"])


def test_event_dir_name_is_filesystem_safe():
    assert sp.event_dir_name("Italian Grand Prix") == "ItalianGrandPrix"
    assert sp.event_dir_name("../../São Paulo/Grand Prix") == "SoPauloGrandPrix"
    assert sp.event_dir_name("///") == "Event"


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #
def test_cancel_stops_between_charts_and_keeps_the_partial_pack(fake_charts, monkeypatch):
    seen = []
    callers = dict(sp.PLOT_CALLERS)
    base_plot = callers["race_story"]

    def cancelling(y, gp, e, task):
        seen.append(task.slug)
        out = base_plot(y, gp, e, task)
        sp.CANCELLED.add("a0000000000c")
        return out

    callers["race_story"] = cancelling
    monkeypatch.setattr(sp, "PLOT_CALLERS", callers)

    job = sp.run_social_pack("a0000000000c", 2025, 16, "R", ["portrait"])

    assert job.status == "cancelled"
    assert seen == ["race_story"]
    assert job.done == 1 and job.done < job.total
    assert "a0000000000c" not in sp.CANCELLED
    manifest = json.loads((_pack_dir() / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "cancelled" and len(manifest["items"]) == 1
    with zipfile.ZipFile(_pack_dir() / "social_pack.zip") as zf:
        assert "2025_ItalianGrandPrix_R/portrait/race_story.png" in zf.namelist()


def test_cancel_job_flags_memory_and_mongo():
    job = sp.SocialJob(job_id="a0000000000d", status="running")
    sp._register_job(job)
    admin_jobs.create_job("a0000000000d", kind=sp.JOB_KIND, scope={}, total=1)
    assert sp.cancel_job("a0000000000d") is True
    assert "a0000000000d" in sp.CANCELLED
    assert admin_jobs.is_cancelled("a0000000000d") is True


def test_cancel_job_for_a_finished_or_unknown_job_is_false():
    assert sp.cancel_job("nope00000000") is False


# --------------------------------------------------------------------------- #
# Job gating / listing
# --------------------------------------------------------------------------- #
def _wait(job, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if job.status in ("completed", "failed", "cancelled"):
            return
        time.sleep(0.02)
    raise AssertionError(f"job stuck in {job.status}")


def test_start_job_refuses_while_one_runs(fake_charts, monkeypatch):
    started, release = threading.Event(), threading.Event()
    callers = dict(sp.PLOT_CALLERS)
    inner = callers["race_story"]

    def slow(y, gp, e, task):
        started.set()
        release.wait(10)
        return inner(y, gp, e, task)

    callers["race_story"] = slow
    monkeypatch.setattr(sp, "PLOT_CALLERS", callers)

    job = sp.start_social_job(year=2025, gp="16", session="Race", formats=["portrait"], pairs=[])
    try:
        assert started.wait(5)
        assert job.scope["gp"] == 16 and job.scope["session"] == "R"
        with pytest.raises(RuntimeError):
            sp.start_social_job(year=2025, gp=16, session="R")
        assert [d["job_id"] for d in sp.running_jobs()] == [job.job_id]
    finally:
        release.set()
    _wait(job)
    assert job.status == "completed", job.errors
    assert sp.get_job_dict(job.job_id)["status"] == "completed"
    listed = sp.list_jobs()
    assert listed and listed[0]["job_id"] == job.job_id and listed[0]["kind"] == sp.JOB_KIND
    # ...and once it has finished a new one is accepted again.
    again = sp.start_social_job(year=2025, gp=16, session="R", formats=["portrait"], pairs=[])
    _wait(again)


def test_start_job_validates_its_scope():
    with pytest.raises(ValueError):
        sp.start_social_job(year=1999, gp=16, session="R")
    with pytest.raises(ValueError):
        sp.start_social_job(year=2025, gp=16, session="nonsense")
    with pytest.raises(ValueError):
        sp.start_social_job(year=2025, gp="../etc", session="R")
    with pytest.raises(ValueError):
        sp.start_social_job(year=2025, gp=99, session="R")
    with pytest.raises(ValueError):
        sp.start_social_job(year=2025, gp=16, session="R", formats=["huge"])
    assert sp.running_jobs() == []


def test_get_job_dict_ignores_other_kinds():
    admin_jobs.create_job("plot00000001", kind="plot_backfill", scope={}, total=1)
    assert sp.get_job_dict("plot00000001") is None
    assert sp.get_job_dict("missing") is None


# --------------------------------------------------------------------------- #
# Download path guard
# --------------------------------------------------------------------------- #
def test_pack_download_path_resolves_a_finished_zip(tmp_path):
    base = sp.pack_dir("2025/X/R")
    base.mkdir(parents=True)
    (base / "social_pack.zip").write_bytes(b"PK")
    sp._register_job(sp.SocialJob(job_id="aaaaaaaaaaaa", pack="2025/X/R", zip_ready=True))
    assert sp.pack_download_path("aaaaaaaaaaaa") == (base / "social_pack.zip").resolve()


def test_pack_download_path_refuses_a_path_outside_the_root(tmp_path):
    outside = tmp_path / "evil"
    outside.mkdir()
    (outside / "social_pack.zip").write_bytes(b"PK")
    sp._register_job(sp.SocialJob(job_id="bbbbbbbbbbbb", pack="../evil", zip_ready=True))
    assert sp.pack_download_path("bbbbbbbbbbbb") is None


def test_pack_download_path_needs_a_ready_zip(tmp_path):
    sp._register_job(sp.SocialJob(job_id="cccccccccccc", pack="2025/X/R", zip_ready=False))
    assert sp.pack_download_path("cccccccccccc") is None
    sp._register_job(sp.SocialJob(job_id="dddddddddddd", pack="2025/X/R", zip_ready=True))
    assert sp.pack_download_path("dddddddddddd") is None  # flagged ready but the file is gone
    assert sp.pack_download_path("../../etc") is None
    assert sp.pack_download_path("eeeeeeeeeeee") is None


def test_build_zip_removes_its_tmp_on_failure(tmp_path):
    base = tmp_path / "pack"
    base.mkdir()
    (base / "a.png").write_bytes(PNG)
    with pytest.raises(RuntimeError):
        sp.build_zip(base, cancelled=lambda: True)
    assert not (base / "social_pack.zip").exists() and not (base / "social_pack.zip.tmp").exists()
