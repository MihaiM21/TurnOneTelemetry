"""Unit tests for src.repositories.circuit_layouts (mongomock-backed)."""
from __future__ import annotations

from uuid import uuid4

import pytest
from pymongo.errors import PyMongoError

from src.repositories import circuit_layouts


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """Give every test its own mongomock database so collections never collide."""
    monkeypatch.setenv("MONGODB_DATABASE", f"t1_test_{uuid4().hex}")


def _layout(circuit_id="153", year=2026, name="Madring", source="multiviewer"):
    return {
        "circuit_id": circuit_id,
        "year": year,
        "name": name,
        "country": "Spain",
        "country_code": "ESP",
        "location": "Madrid",
        "rotation": 0.0,
        "track_outline": {"x": [0.0, 1.0], "y": [0.0, 1.0]},
        "corners": [],
        "marshal_lights": [],
        "marshal_sectors": [],
        "source": source,
    }


def _summary(circuit_id="153", name="Madring"):
    return {
        "circuit_id": circuit_id,
        "name": name,
        "country": "Spain",
        "country_code": "ESP",
        "years_available": [2026],
    }


def test_store_and_get_layout_round_trips_fields():
    layout = _layout()
    summary = _summary()

    assert circuit_layouts.store_layout(layout, summary) is True

    doc = circuit_layouts.get_layout(2026, "153")
    assert doc is not None
    assert doc["layout"] == layout
    assert doc["summary"] == summary
    assert doc["year"] == 2026
    assert doc["circuit_id"] == "153"
    assert doc["slug"] == "madring"
    assert doc["source"] == "multiviewer"
    assert doc["name"] == "Madring"


def test_store_layout_twice_replaces_not_duplicates():
    circuit_layouts.store_layout(_layout(source="multiviewer"), _summary())
    circuit_layouts.store_layout(_layout(source="telemetry"), _summary())

    assert circuit_layouts._collection().count_documents({}) == 1
    doc = circuit_layouts.get_layout(2026, "153")
    assert doc["source"] == "telemetry"


def test_list_layouts_excludes_payload_and_filters_by_year():
    circuit_layouts.store_layout(_layout(circuit_id="153", year=2026), _summary(circuit_id="153"))
    circuit_layouts.store_layout(_layout(circuit_id="10", year=2025, name="Silverstone"), _summary(circuit_id="10"))

    rows = circuit_layouts.list_layouts()
    assert len(rows) == 2
    for row in rows:
        assert "layout" not in row
        assert "summary" not in row

    filtered = circuit_layouts.list_layouts(year=2026)
    assert len(filtered) == 1
    assert filtered[0]["circuit_id"] == "153"


def test_iter_layouts_yields_full_docs():
    circuit_layouts.store_layout(_layout(), _summary())

    docs = list(circuit_layouts.iter_layouts())
    assert len(docs) == 1
    assert docs[0]["layout"]["circuit_id"] == "153"


def test_delete_layout_then_missing():
    circuit_layouts.store_layout(_layout(), _summary())

    assert circuit_layouts.delete_layout(2026, "153") is True
    assert circuit_layouts.delete_layout(2026, "153") is False
    assert circuit_layouts.get_layout(2026, "153") is None


def test_store_layout_write_failure_returns_false(monkeypatch):
    class _BoomCollection:
        def replace_one(self, *args, **kwargs):
            raise PyMongoError("boom")

    monkeypatch.setattr(circuit_layouts, "_collection", lambda: _BoomCollection())

    assert circuit_layouts.store_layout(_layout(), _summary()) is False


def test_doc_id_matches_regardless_of_circuit_id_type():
    assert circuit_layouts._doc_id(2026, "153") == "2026_153"
    assert circuit_layouts._doc_id(2026, 153) == "2026_153"
