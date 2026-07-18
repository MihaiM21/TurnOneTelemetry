"""Offline unit tests for the durable session-bundle MongoDB cache.

The Mongo collection is replaced with an in-memory double so no real database is
touched. These tests cover the numpy-type round-trip and the oversized-document
guard, plus basic get/miss behaviour.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.repositories import session_cache as sc


class _FakeCollection:
    """Minimal in-memory stand-in for a pymongo collection."""

    def __init__(self):
        self.docs: dict = {}

    def find_one(self, flt, projection=None):
        return self.docs.get(flt["_id"])

    def replace_one(self, flt, document, upsert=False):
        self.docs[flt["_id"]] = document
        return None


@pytest.fixture
def fake_collection(monkeypatch):
    coll = _FakeCollection()
    monkeypatch.setattr(sc, "_collection", lambda: coll)
    return coll


def test_store_and_get_round_trip(fake_collection):
    bundle = {"lap_times": {"1": [{"lap": 1, "time_s": 90.5}]}}
    assert sc.store_session_bundle(2024, 1, "Race", bundle) is True

    got = sc.get_session_bundle(2024, 1, "Race")
    assert got == bundle


def test_get_miss_returns_none(fake_collection):
    assert sc.get_session_bundle(2024, 99, "Race") is None


def test_numpy_types_are_converted(fake_collection):
    bundle = {
        "best_sectors": {"1": {"s1": np.float64(31.25)}},
        "counts": {"laps": np.int64(57)},
    }
    assert sc.store_session_bundle(2024, 1, "Race", bundle) is True

    stored = fake_collection.docs["2024_1_Race"]["bundle"]
    s1 = stored["best_sectors"]["1"]["s1"]
    laps = stored["counts"]["laps"]
    assert isinstance(s1, float) and not isinstance(s1, np.floating)
    assert isinstance(laps, int) and not isinstance(laps, np.integer)


def test_oversized_document_skips_write(fake_collection, monkeypatch):
    monkeypatch.setattr(sc, "_MAX_DOC_BYTES", 10)  # force the guard to trip
    assert sc.store_session_bundle(2024, 1, "Race", {"lap_times": {"1": [1, 2, 3]}}) is False
    assert "2024_1_Race" not in fake_collection.docs


def test_write_failure_is_swallowed(monkeypatch):
    class _BoomCollection:
        def replace_one(self, *a, **k):
            raise RuntimeError("mongo down")

    monkeypatch.setattr(sc, "_collection", lambda: _BoomCollection())
    # Must fail open — a cache write can never break the caller.
    assert sc.store_session_bundle(2024, 1, "Race", {"lap_times": {}}) is False
