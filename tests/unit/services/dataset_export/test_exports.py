"""Export directory read-model: listing, path guard, archive and delete."""

import json
import zipfile

import pytest

from src.core.config import settings
from src.services.dataset_export import exports, manifest as manifest_mod
from src.services.dataset_export.manifest import TierEntry
from src.services.dataset_export.writer import FileStat


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "export_dir", str(tmp_path / "exports"))
    return exports.export_root()


def _make_export(root, export_id="export_1", *, done=True):
    base = root / export_id
    (base / "laps" / "year=2024" / "round=01" / "session=R").mkdir(parents=True)
    part = base / "laps" / "year=2024" / "round=01" / "session=R" / "part.parquet"
    part.write_bytes(b"PAR1")
    (base / "laps" / "year=2024" / "round=01" / "session=R" / "part.parquet.tmp").write_bytes(b"x")
    m = manifest_mod.new_manifest(export_id, {"years": [2024]}, ["tables"])
    entry = TierEntry(status="done" if done else "failed",
                      files=[FileStat(path="laps/year=2024/round=01/session=R/part.parquet", rows=3,
                                      bytes=4, sha256="")], rows=3, bytes=4)
    m.mark("2024_01_R", "tables", entry, year=2024, round=1, gp_name="Bahrain", session="R")
    manifest_mod.save(m, base)
    return base


def test_list_exports_and_summary(root):
    _make_export(root, "export_a")
    _make_export(root, "export_b", done=False)
    (root / "not-an-export").mkdir()
    (root / "bad id!").mkdir()
    listed = {s.export_id: s for s in exports.list_exports()}
    assert set(listed) == {"export_a", "export_b"}
    assert listed["export_a"].sessions_done == 1 and listed["export_a"].sessions_failed == 0
    assert listed["export_b"].sessions_failed == 1
    assert listed["export_a"].rows_by_table == {"laps": 3}
    assert listed["export_a"].archive["status"] == "none"


def test_list_export_files_uses_manifest_and_hides_tmp(root):
    _make_export(root)
    paths = [f.path for f in exports.list_export_files("export_1")]
    assert "laps/year=2024/round=01/session=R/part.parquet" in paths
    assert "manifest.json" in paths
    assert not any(p.endswith(".tmp") for p in paths)
    assert [f.path for f in exports.list_export_files("export_1", prefix="manifest")] == ["manifest.json"]


@pytest.mark.parametrize("relative", [
    "../export_2/manifest.json",
    "..\\manifest.json",
    "/etc/passwd",
    "C:/Windows/win.ini",
    "laps/year=2024/round=01/session=R/part.parquet.tmp",
    "",
    "laps",  # a directory, not a file
])
def test_resolve_export_file_rejects_escapes(root, relative):
    _make_export(root)
    with pytest.raises(exports.ExportNotFound):
        exports.resolve_export_file("export_1", relative)


def test_resolve_export_file_ok(root):
    base = _make_export(root)
    path = exports.resolve_export_file("export_1", "laps/year=2024/round=01/session=R/part.parquet")
    assert path == (base / "laps" / "year=2024" / "round=01" / "session=R" / "part.parquet").resolve()


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x" * 65, "-lead", "id with space"])
def test_invalid_export_id(root, bad):
    with pytest.raises(exports.ExportNotFound):
        exports.export_dir(bad)


def test_build_archive_skips_tmp_and_is_atomic(root):
    _make_export(root)
    path = exports.build_archive("export_1")
    assert path == root / "export_1.zip"
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    assert "export_1/manifest.json" in names
    assert "export_1/laps/year=2024/round=01/session=R/part.parquet" in names
    assert not any(n.endswith(".tmp") for n in names)
    assert exports.archive_status("export_1")["status"] == "ready"
    assert exports.archive_stat("export_1").bytes == path.stat().st_size


def test_build_archive_cancel_leaves_nothing(root):
    _make_export(root)
    with pytest.raises(exports.ExportBusy):
        exports.build_archive("export_1", cancelled=lambda: True)
    assert not (root / "export_1.zip").exists()
    assert not (root / "export_1.zip.tmp").exists()


def test_delete_export_refuses_when_in_use_and_frees(root):
    _make_export(root)
    exports.build_archive("export_1")
    with pytest.raises(exports.ExportBusy):
        exports.delete_export("export_1", in_use=lambda eid: True)
    assert (root / "export_1").is_dir()
    freed = exports.delete_export("export_1", in_use=lambda eid: False)
    assert freed > 0
    assert not (root / "export_1").exists() and not (root / "export_1.zip").exists()
    with pytest.raises(exports.ExportNotFound):
        exports.delete_export("export_1")


def test_load_manifest_missing(root):
    (root / "empty_dir").mkdir()
    with pytest.raises(exports.ExportNotFound):
        exports.load_manifest("empty_dir")
    with pytest.raises(exports.ExportNotFound):
        exports.load_manifest("nope")


def test_summary_round_trips_json(root):
    _make_export(root)
    json.dumps(exports.summarize("export_1").to_dict())
