import pytest

from src.services.dataset_export.manifest import (
    Manifest,
    SchemaMismatch,
    TierEntry,
    check_resumable,
    load,
    new_manifest,
    save,
    session_key,
)
from src.services.dataset_export.schema import DATASET_SCHEMA_VERSION
from src.services.dataset_export.writer import FileStat


def test_session_key_format():
    assert session_key(2026, 1, "R") == "2026_01_R"
    assert session_key(2026, 12, "FP1") == "2026_12_FP1"


def test_new_manifest_defaults():
    manifest = new_manifest("exp1", {"years": [2026]}, ["tables"])
    assert manifest.export_id == "exp1"
    assert manifest.schema_version == DATASET_SCHEMA_VERSION
    assert manifest.sessions == {}


def test_mark_creates_session_and_sets_tier():
    manifest = new_manifest("exp1", {}, ["tables"])
    key = session_key(2026, 1, "R")
    entry = TierEntry(status="done", rows=10, bytes=100)
    manifest.mark(key, "tables", entry, year=2026, round=1, gp_name="Bahrain", session="R")

    assert key in manifest.sessions
    assert manifest.sessions[key].tiers["tables"].status == "done"
    assert manifest.is_done(key, "tables")
    assert not manifest.is_done(key, "telemetry")
    assert not manifest.is_done("missing", "tables")


def test_is_done_treats_absent_as_done():
    manifest = new_manifest("exp1", {}, ["tables"])
    key = session_key(2026, 1, "R")
    manifest.mark(key, "tables", TierEntry(status="absent"), year=2026, round=1, gp_name="Bahrain", session="R")
    assert manifest.is_done(key, "tables")


def test_to_dict_from_dict_round_trip():
    manifest = new_manifest("exp1", {"years": [2026]}, ["tables", "telemetry"])
    key = session_key(2026, 1, "R")
    stat = FileStat(path="laps/year=2026/round=01/session=R/part.parquet", rows=5, bytes=123, sha256="abc")
    manifest.mark(
        key,
        "tables",
        TierEntry(status="done", files=[stat], rows=5, bytes=123),
        year=2026,
        round=1,
        gp_name="Bahrain",
        session="R",
    )

    data = manifest.to_dict()
    restored = Manifest.from_dict(data)

    assert restored.export_id == manifest.export_id
    assert restored.sessions[key].tiers["tables"].files[0].to_dict() == stat.to_dict()
    assert restored.to_dict() == data


def test_totals_counts_done_failed_and_rows_by_table():
    manifest = new_manifest("exp1", {}, ["tables", "telemetry"])
    key1 = session_key(2026, 1, "R")
    key2 = session_key(2026, 2, "R")

    stat = FileStat(path="laps/year=2026/round=01/session=R/part.parquet", rows=10, bytes=100, sha256="x")
    manifest.mark(
        key1, "tables", TierEntry(status="done", files=[stat], rows=10, bytes=100),
        year=2026, round=1, gp_name="Bahrain", session="R",
    )
    manifest.mark(key1, "telemetry", TierEntry(status="done"), year=2026, round=1, gp_name="Bahrain", session="R")

    manifest.mark(
        key2, "tables", TierEntry(status="failed", error="boom"),
        year=2026, round=2, gp_name="Saudi", session="R",
    )
    manifest.mark(
        key2, "telemetry", TierEntry(status="pending"),
        year=2026, round=2, gp_name="Saudi", session="R",
    )

    totals = manifest.totals()
    assert totals["sessions_total"] == 2
    assert totals["sessions_done"] == 1
    assert totals["sessions_failed"] == 1
    assert totals["bytes"] == 100
    assert totals["rows_by_table"] == {"laps": 10}
    assert totals["by_tier"]["tables"]["done"] == 1
    assert totals["by_tier"]["tables"]["failed"] == 1
    assert totals["by_tier"]["telemetry"]["done"] == 1
    assert totals["by_tier"]["telemetry"]["pending"] == 1


def test_save_and_load_round_trip(tmp_path):
    manifest = new_manifest("exp1", {"years": [2026]}, ["tables"])
    key = session_key(2026, 1, "R")
    manifest.mark(
        key, "tables", TierEntry(status="done", rows=1, bytes=1),
        year=2026, round=1, gp_name="Bahrain", session="R",
    )

    save(manifest, tmp_path)
    assert not (tmp_path / "manifest.json.tmp").exists()

    loaded = load(tmp_path)
    assert loaded is not None
    assert loaded.export_id == "exp1"
    assert loaded.is_done(key, "tables")


def test_load_missing_returns_none(tmp_path):
    assert load(tmp_path) is None


def test_check_resumable_raises_on_mismatch():
    manifest = new_manifest("exp1", {}, ["tables"])
    manifest.schema_version = "0.0.1"
    with pytest.raises(SchemaMismatch):
        check_resumable(manifest)


def test_check_resumable_passes_on_match():
    manifest = new_manifest("exp1", {}, ["tables"])
    check_resumable(manifest)
