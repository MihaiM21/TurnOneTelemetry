"""Per-session orchestration: tier order, resume, failure isolation, cancellation."""

import types

import pandas as pd
import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.dataset_export import manifest as manifest_mod, session_export as se
from src.services.dataset_export.schema import TABLES, empty_frame, key_values, tables_for_tier
from src.services.dataset_export.writer import FileStat

TARGET = (2024, 5, "Chinese Grand Prix", "R")
KEY = "2024_05_R"


def _tables_frames():
    out = {}
    for spec in tables_for_tier("tables"):
        df = empty_frame(spec)
        out[spec.name] = df
    laps = pd.DataFrame([{**key_values(TARGET), "driver_number": "1", "lap": 1, "lap_time_s": 90.0}])
    out["laps"] = laps
    return out


@pytest.fixture
def stubs(monkeypatch, tmp_path):
    """Replace every tier implementation with a recording stub; return the call log."""
    log = []

    fake_builders = types.SimpleNamespace(
        build_tables=lambda store, target: (log.append("tables") or (_tables_frames(), ["weather: absent"]))
    )
    fake_corpus = types.SimpleNamespace(
        write_corpus=lambda target, tables, root, lock: (log.append("corpus") or {"narratives": 1, "qa": 5})
    )

    def fake_raw(store, dest, root, streams=None, *, cancelled=None):
        log.append("raw")
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "timing_data.jsonl.gz").write_bytes(b"gz")
        return ({"timing_data": FileStat("raw/x/timing_data.jsonl.gz", 2, 2, "")}, ["lap_count"])

    fake_raw_mod = types.SimpleNamespace(write_raw_streams=fake_raw)

    def fake_car(store, target):
        log.append("telemetry")
        spec = TABLES["car_telemetry"]
        yield pd.DataFrame([{**key_values(target), "session_time_s": 1.0, "driver_number": "1", "speed": 300}],
                           columns=spec.column_names)

    def fake_pos(store, target):
        raise DataNotAvailableError("no Position.z")
        yield  # pragma: no cover

    fake_tel = types.SimpleNamespace(iter_car_telemetry=fake_car, iter_position_telemetry=fake_pos)

    monkeypatch.setattr(se, "builders", fake_builders)
    monkeypatch.setattr(se, "corpus", fake_corpus)
    monkeypatch.setattr(se, "raw", fake_raw_mod)
    monkeypatch.setattr(se, "telemetry", fake_tel)
    monkeypatch.setattr(se, "build_session_store", lambda year, ident, session: object())
    return log


def test_all_tiers_run_in_order_and_are_recorded(stubs, tmp_path):
    m = manifest_mod.new_manifest("e", {}, list(se.TIER_ORDER))
    out = se.export_session(TARGET, tmp_path, ["telemetry", "raw", "corpus", "tables"], m)

    assert stubs == ["tables", "corpus", "raw", "telemetry"]
    assert out.per_tier == {"tables": "done", "corpus": "done", "raw": "done", "telemetry": "done"}
    assert not out.failed
    assert (tmp_path / "laps" / "year=2024" / "round=05" / "session=R" / "part.parquet").is_file()
    assert (tmp_path / "car_telemetry" / "year=2024" / "round=05" / "session=R" / "part.parquet").is_file()
    assert not (tmp_path / "position_telemetry").exists()

    entry = m.sessions[KEY]
    assert set(entry.tiers) == {"tables", "corpus", "raw", "telemetry"}
    assert entry.tiers["tables"].rows >= 1 and len(entry.tiers["tables"].files) == len(tables_for_tier("tables"))
    assert entry.tiers["corpus"].rows == 6
    assert entry.tiers["raw"].absent_streams == ["lap_count"]
    assert entry.tiers["telemetry"].absent_streams == ["position_telemetry"]
    assert any("weather: absent" in w for w in out.warnings)
    assert out.bytes_written == sum(t.bytes for t in entry.tiers.values())


def test_resume_skips_done_tiers_without_building_store(stubs, tmp_path, monkeypatch):
    m = manifest_mod.new_manifest("e", {}, ["tables"])
    m.mark(KEY, "tables", manifest_mod.TierEntry(status="done"), year=2024, round=5, gp_name="C", session="R")
    called = []
    monkeypatch.setattr(se, "build_session_store", lambda *a: called.append(a) or object())
    out = se.export_session(TARGET, tmp_path, ["tables"], m)
    assert out.per_tier == {"tables": "skipped"}
    assert called == []

    # corpus still needs tables in memory -> rebuilds them without re-writing parquet
    out = se.export_session(TARGET, tmp_path, ["tables", "corpus"], m)
    assert out.per_tier == {"tables": "skipped", "corpus": "done"}
    assert stubs == ["tables", "corpus"]
    assert called and called[0] == (2024, 5, "R")  # abbreviation, not "Race"


def test_tier_failure_is_isolated(stubs, tmp_path, monkeypatch):
    def boom(store, dest, root, streams=None, *, cancelled=None):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(se.raw, "write_raw_streams", boom)
    m = manifest_mod.new_manifest("e", {}, ["tables", "raw", "telemetry"])
    out = se.export_session(TARGET, tmp_path, ["tables", "raw", "telemetry"], m)
    assert out.per_tier == {"tables": "done", "raw": "failed", "telemetry": "done"}
    assert out.failed
    assert "disk on fire" in m.sessions[KEY].tiers["raw"].error


def test_unresolvable_session_marks_every_tier_failed(stubs, tmp_path, monkeypatch):
    monkeypatch.setattr(se, "build_session_store", lambda *a: None)
    m = manifest_mod.new_manifest("e", {}, ["tables", "corpus"])
    out = se.export_session(TARGET, tmp_path, ["tables", "corpus"], m)
    assert out.per_tier == {"tables": "failed", "corpus": "failed"}
    assert m.sessions[KEY].tiers["tables"].error == "session could not be resolved"


def test_cancel_between_tiers(stubs, tmp_path):
    flags = iter([False, True, True, True])
    m = manifest_mod.new_manifest("e", {}, ["tables", "raw"])
    out = se.export_session(TARGET, tmp_path, ["tables", "raw", "telemetry"], m, cancelled=lambda: next(flags))
    assert out.per_tier == {"tables": "done", "raw": "cancelled", "telemetry": "cancelled"}
    assert "raw" not in m.sessions[KEY].tiers
    assert not m.is_done(KEY, "raw")
