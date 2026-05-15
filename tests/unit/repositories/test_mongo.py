"""Unit tests for src.repositories.mongo helpers."""
import numpy as np
import pytest

from src.repositories.mongo import NumpyEncoder, convert_numpy_types, MongoDBManager


def test_convert_numpy_int():
    out = convert_numpy_types({"x": np.int64(5)})
    assert out == {"x": 5}
    assert isinstance(out["x"], int)


def test_convert_numpy_float():
    out = convert_numpy_types({"x": np.float64(3.14)})
    assert out["x"] == pytest.approx(3.14)
    assert isinstance(out["x"], float)


def test_convert_numpy_array_to_list():
    out = convert_numpy_types({"arr": np.array([1, 2, 3])})
    assert out == {"arr": [1, 2, 3]}


def test_convert_numpy_bool():
    assert convert_numpy_types(np.bool_(True)) is True


def test_convert_nested_structures():
    payload = {"a": [np.int32(1), {"b": np.float32(2.5)}]}
    out = convert_numpy_types(payload)
    assert out == {"a": [1, {"b": pytest.approx(2.5)}]}


def test_convert_passes_through_native_types():
    payload = {"a": "string", "b": 1, "c": [1.0, True, None]}
    assert convert_numpy_types(payload) == payload


def test_numpy_encoder_handles_int_float_array():
    import json
    s = json.dumps({"i": np.int64(7), "f": np.float64(1.5), "a": np.array([1, 2])}, cls=NumpyEncoder)
    out = json.loads(s)
    assert out == {"i": 7, "f": 1.5, "a": [1, 2]}


def test_mongomanager_constructs_with_mongomock(monkeypatch):
    # conftest already patches MongoClient with mongomock.
    monkeypatch.setenv("MONGODB_PASSWORD", "test-password")
    mgr = MongoDBManager(year=2024, version="v1")
    assert mgr.collection.name == "2024_processed_data"
    mgr.set_year(2025)
    assert mgr.collection.name == "2025_processed_data"
    mgr.close()


def test_mongomanager_v2_collection_naming():
    mgr = MongoDBManager(year=2024, version="v2")
    assert mgr.collection.name == "2024_processed_data_v2"
    mgr.close()


def test_mongomanager_get_or_create_roundtrip():
    mgr = MongoDBManager(year=2024, version="v1")
    doc = mgr.get_or_create_gp(2024, 1, "BahrainGP", "2024_BHR")
    assert doc["gp_id"] == "2024_BHR"
    # Calling again returns the existing document.
    again = mgr.get_or_create_gp(2024, 1, "BahrainGP", "2024_BHR")
    assert again["gp_id"] == "2024_BHR"
    mgr.close()
