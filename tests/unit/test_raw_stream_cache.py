"""Offline unit tests for the durable GridFS raw-stream cache.

GridFS is replaced with a minimal in-memory double so no real database is
touched. Tests cover the gzip round-trip, the schema-version gate, upsert
(single current copy), miss behaviour and fail-open writes.
"""
from __future__ import annotations

import itertools

import numpy as np
import pytest

from src.repositories import raw_stream_cache as rsc


class _FakeGridOut:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeFile:
    _ids = itertools.count(1)

    def __init__(self, filename: str, metadata: dict, data: bytes):
        self._id = next(self._ids)
        self.filename = filename
        self.metadata = metadata
        self.data = data


class _FakeGridFS:
    """Minimal in-memory stand-in for :class:`gridfs.GridFS`."""

    def __init__(self):
        self.files: list[_FakeFile] = []

    def _matches(self, f: _FakeFile, query: dict) -> bool:
        if "filename" in query and f.filename != query["filename"]:
            return False
        if "metadata.schema_version" in query:
            if f.metadata.get("schema_version") != query["metadata.schema_version"]:
                return False
        return True

    def find_one(self, query: dict):
        for f in self.files:
            if self._matches(f, query):
                return _FakeGridOut(f.data)
        return None

    def find(self, query: dict):
        return [f for f in self.files if self._matches(f, query)]

    def delete(self, file_id):
        self.files = [f for f in self.files if f._id != file_id]

    def put(self, data: bytes, filename: str, metadata: dict):
        self.files.append(_FakeFile(filename, metadata, data))


@pytest.fixture
def fake_bucket(monkeypatch):
    bucket = _FakeGridFS()
    monkeypatch.setattr(rsc, "_bucket", lambda: bucket)
    return bucket


def test_store_and_get_round_trip(fake_bucket):
    data = [{"Entries": [{"Cars": {"1": {"Channels": {"2": 300}}}}]}]
    assert rsc.store_raw_stream(2024, 1, "Race", "car_data", data) is True
    assert rsc.get_raw_stream(2024, 1, "Race", "car_data") == data


def test_get_miss_returns_none(fake_bucket):
    assert rsc.get_raw_stream(2024, 99, "Race", "car_data") is None


def test_upsert_keeps_single_current_copy(fake_bucket):
    rsc.store_raw_stream(2024, 1, "Race", "car_data", [{"v": 1}])
    rsc.store_raw_stream(2024, 1, "Race", "car_data", [{"v": 2}])
    # Old blob deleted; exactly one file remains and it is the latest.
    assert len(fake_bucket.files) == 1
    assert rsc.get_raw_stream(2024, 1, "Race", "car_data") == [{"v": 2}]


def test_numpy_types_are_converted(fake_bucket):
    data = {"speed": np.float64(305.5), "laps": np.int64(57)}
    assert rsc.store_raw_stream(2024, 1, "Race", "timing_data", data) is True
    got = rsc.get_raw_stream(2024, 1, "Race", "timing_data")
    assert got == {"speed": 305.5, "laps": 57}
    assert isinstance(got["laps"], int)


def test_schema_version_mismatch_is_a_miss(fake_bucket, monkeypatch):
    rsc.store_raw_stream(2024, 1, "Race", "car_data", [{"v": 1}])
    # A future schema version must not read an old blob.
    monkeypatch.setattr(rsc, "SCHEMA_VERSION", rsc.SCHEMA_VERSION + 1)
    assert rsc.get_raw_stream(2024, 1, "Race", "car_data") is None


def test_write_failure_is_swallowed(monkeypatch):
    class _BoomBucket:
        def find(self, *a, **k):
            return []

        def put(self, *a, **k):
            raise RuntimeError("gridfs down")

    monkeypatch.setattr(rsc, "_bucket", lambda: _BoomBucket())
    # Must fail open — a cache write can never break the caller.
    assert rsc.store_raw_stream(2024, 1, "Race", "car_data", [{"v": 1}]) is False


def test_read_failure_is_swallowed(monkeypatch):
    class _BoomBucket:
        def find_one(self, *a, **k):
            raise RuntimeError("gridfs down")

    monkeypatch.setattr(rsc, "_bucket", lambda: _BoomBucket())
    assert rsc.get_raw_stream(2024, 1, "Race", "car_data") is None
