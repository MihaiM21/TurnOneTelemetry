"""Unit tests for :mod:`src.ingestion.circuits_store` (the on-disk writer).

``circuits_loader.CIRCUITS_DIR`` is monkeypatched to a temp directory so
reads/writes never touch ``src/domain/data/circuits``; the Mongo mirror
(``src.repositories.circuit_layouts.store_layout``) is monkeypatched to a
recorder so no real Mongo client is involved.
"""
from __future__ import annotations

import json

import pytest

from src.domain.models.circuits import CircuitLayout, CircuitSummary, Point, TrackMarker, TrackOutline
from src.ingestion import circuits_loader, circuits_store
from src.repositories import circuit_layouts as circuit_layouts_repo
from src.services.analysis.v2._helpers import get_circuit_info_for_session


@pytest.fixture(autouse=True)
def _circuits_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(circuits_loader, "CIRCUITS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def recorder(monkeypatch):
    calls = []

    def _record(layout_dict, summary_dict):
        calls.append((layout_dict, summary_dict))
        return True

    monkeypatch.setattr(circuit_layouts_repo, "store_layout", _record)
    return calls


def make_layout(**overrides) -> CircuitLayout:
    base = dict(
        circuit_id="153", year=2026, name="Madring", country="Spain", country_code="ESP",
        location="Madrid", rotation=170.7,
        track_outline=TrackOutline(x=[0.0, 1.0, 2.0, 3.0], y=[0.0, 1.0, 0.0, -1.0]),
        corners=[TrackMarker(number=1, angle=10.0, length=100.0, position=Point(x=1.0, y=1.0))],
        marshal_lights=[], marshal_sectors=[], source="telemetry",
    )
    base.update(overrides)
    return CircuitLayout(**base)


def make_summary(**overrides) -> CircuitSummary:
    base = dict(
        circuit_id="153", name="Madring", country="Spain", country_code="ESP",
        circuit_key=153, years_available=[2026],
    )
    base.update(overrides)
    return CircuitSummary(**base)


# ---------------------------------------------------------------------------
# save_circuit_layout
# ---------------------------------------------------------------------------

def test_save_circuit_layout_writes_file_manifest_and_mirror(tmp_path, recorder):
    layout = make_layout()
    summary = make_summary()

    path = circuits_store.save_circuit_layout(layout, summary)

    assert path == tmp_path / "2026" / "153_madring.json"
    with open(path, "r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    CircuitLayout(**loaded)  # loadable / round-trips through the schema

    manifest = json.loads((tmp_path / "2026" / "all_circuits.json").read_text())
    assert len(manifest["circuits"]) == 1
    assert manifest["circuits"][0]["circuit_id"] == "153"

    assert len(recorder) == 1
    assert recorder[0][0]["circuit_id"] == "153"
    assert recorder[0][1]["circuit_id"] == "153"


def test_save_circuit_layout_removes_stale_file_for_same_id(tmp_path, recorder):
    year_dir = tmp_path / "2026"
    year_dir.mkdir(parents=True)
    old_file = year_dir / "153_old_name.json"
    old_file.write_text(json.dumps({"source": "multiviewer"}))

    circuits_store.save_circuit_layout(make_layout(), make_summary())

    assert not old_file.exists()
    files = circuits_store.list_circuit_files(2026)
    assert list(files.keys()) == ["153"]
    assert files["153"]["slug"] == "madring"
    assert files["153"]["source"] == "telemetry"


def test_upsert_manifest_entry_replaces_and_merges_years(tmp_path):
    year_dir = tmp_path / "2026"
    year_dir.mkdir(parents=True)
    (year_dir / "all_circuits.json").write_text(json.dumps({
        "year": 2026, "total_circuits": 2,
        "circuits": [
            {"circuit_id": "153", "name": "OldMadring", "country": "Spain", "country_code": "ESP",
             "circuit_key": 153, "years_available": [2025]},
            {"circuit_id": "39", "name": "Monza", "country": "Italy", "country_code": "ITA",
             "circuit_key": 39, "years_available": [2025, 2026]},
        ],
    }))

    circuits_store.upsert_manifest_entry(2026, make_summary(years_available=[2026]))

    manifest = json.loads((year_dir / "all_circuits.json").read_text())
    assert len(manifest["circuits"]) == 2
    by_id = {e["circuit_id"]: e for e in manifest["circuits"]}
    assert by_id["153"]["years_available"] == [2025, 2026]
    assert by_id["153"]["name"] == "Madring"
    assert by_id["39"]["years_available"] == [2025, 2026]
    assert by_id["39"]["name"] == "Monza"


def test_save_circuit_layout_mirror_to_mongo_false_skips_recorder(recorder):
    circuits_store.save_circuit_layout(make_layout(), make_summary(), mirror_to_mongo=False)
    assert recorder == []


# ---------------------------------------------------------------------------
# hydrate_from_mongo
# ---------------------------------------------------------------------------

def test_hydrate_from_mongo_creates_only_missing_files(tmp_path):
    existing_layout = make_layout(circuit_id="1", name="A")
    existing_summary = make_summary(circuit_id="1", name="A", circuit_key=1)
    circuits_store.save_circuit_layout(existing_layout, existing_summary, mirror_to_mongo=False)

    docs = [
        {
            "year": 2026, "circuit_id": "1",
            "layout": existing_layout.model_dump(), "summary": existing_summary.model_dump(),
            "_id": "docA",
        },
        {
            "year": 2026, "circuit_id": "2",
            "layout": make_layout(circuit_id="2", name="B").model_dump(),
            "summary": make_summary(circuit_id="2", name="B", circuit_key=2).model_dump(),
            "_id": "docB",
        },
        {"year": 2026, "circuit_id": "3", "layout": {"bad": "data"}, "summary": {}, "_id": "docC"},
    ]

    written = circuits_store.hydrate_from_mongo(docs)

    assert written == 1
    assert circuits_store.find_circuit_file(2026, "2") is not None


# ---------------------------------------------------------------------------
# Integration with the loader / _helpers.get_circuit_info_for_session
# ---------------------------------------------------------------------------

class _FakeSessionStore:
    def __init__(self, year, circuit_key):
        self.year = year
        self._circuit_key = circuit_key

    def session_info(self):
        return {"Meeting": {"Circuit": {"Key": self._circuit_key}}}


def test_loader_and_helpers_read_back_saved_layout():
    circuits_store.save_circuit_layout(make_layout(), make_summary(), mirror_to_mongo=False)

    data = circuits_loader.get_circuit_data_file("153", 2026)
    assert data["name"] == "Madring"

    info = get_circuit_info_for_session(_FakeSessionStore(year=2026, circuit_key=153))
    assert info is not None
    assert info["rotation"] == 170  # int()-truncated from 170.7
    assert len(info["corners"]) == 1


# ---------------------------------------------------------------------------
# list_circuit_files: invalid / missing-source files
# ---------------------------------------------------------------------------

def test_list_circuit_files_reports_invalid_and_default_source(tmp_path):
    year_dir = tmp_path / "2027"
    year_dir.mkdir(parents=True)
    (year_dir / "5_bad.json").write_text("{not valid json")
    (year_dir / "6_nosource.json").write_text(json.dumps({"name": "NoSource"}))

    files = circuits_store.list_circuit_files(2027)

    assert files["5"]["source"] == "invalid"
    assert files["6"]["source"] == "multiviewer"
