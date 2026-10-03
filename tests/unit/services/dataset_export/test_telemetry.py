"""Long-format telemetry export: column shape, channel mapping, chunking, dtypes."""

import pandas as pd

from src.services.dataset_export import telemetry as T
from src.services.dataset_export.schema import TABLES, key_values

TARGET = (2024, 3, "Australian Grand Prix", "R")


class _FakeCarStore:
    def __init__(self, n_samples=1, n_drivers=2):
        self._samples = []
        drivers = ["1", "44"][:n_drivers]
        for i in range(n_samples):
            cars = {
                num: {"Channels": {"0": 11000 + i, "2": 280 + i, "3": 7, "4": 99, "5": 0, "45": 12}}
                for num in drivers
            }
            self._samples.append({
                "_timestamp": f"00:00:{i + 1:02d}.000",
                "Entries": [{
                    "Utc": f"2024-03-02T15:00:{i + 1:02d}.000Z",
                    "Cars": cars,
                }],
            })

    def car_data(self):
        return self._samples


class _FakePositionStore:
    def __init__(self, n_samples=1, n_drivers=2):
        self._samples = []
        drivers = ["1", "44"][:n_drivers]
        for i in range(n_samples):
            entries = {
                num: {"Status": "OnTrack", "X": 1000 + i, "Y": -55 - i, "Z": 7}
                for num in drivers
            }
            self._samples.append({
                "_timestamp": f"00:00:{i + 1:02d}.000",
                "Position": [{
                    "Timestamp": f"2024-03-02T15:00:{i + 1:02d}.000Z",
                    "Entries": entries,
                }],
            })

    def position_data(self):
        return self._samples


def _concat(chunks):
    chunks = list(chunks)
    if not chunks:
        return pd.DataFrame()
    return pd.concat(chunks, ignore_index=True)


def test_car_telemetry_columns_and_row_count():
    store = _FakeCarStore(n_samples=3, n_drivers=2)
    df = _concat(T.iter_car_telemetry(store, TARGET))

    assert list(df.columns) == TABLES["car_telemetry"].column_names
    assert len(df) == 3 * 2


def test_car_telemetry_channel_mapping():
    store = _FakeCarStore(n_samples=1, n_drivers=1)
    df = _concat(T.iter_car_telemetry(store, TARGET))

    row = df.iloc[0]
    assert row["gear"] == 7  # channel '3'
    assert row["drs"] == 12  # channel '45'
    assert row["rpm"] == 11000  # channel '0'
    assert row["speed"] == 280  # channel '2'
    assert row["throttle"] == 99
    assert row["brake"] == 0


def test_car_telemetry_missing_channel_is_none():
    store = _FakeCarStore(n_samples=1, n_drivers=1)
    store._samples[0]["Entries"][0]["Cars"]["1"]["Channels"].pop("45")
    df = _concat(T.iter_car_telemetry(store, TARGET))

    assert df.iloc[0]["drs"] is None or pd.isna(df.iloc[0]["drs"])


def test_car_telemetry_key_columns_filled():
    store = _FakeCarStore(n_samples=1, n_drivers=1)
    df = _concat(T.iter_car_telemetry(store, TARGET))

    expected = key_values(TARGET)
    row = df.iloc[0]
    for key, value in expected.items():
        assert row[key] == value


def test_car_telemetry_chunking():
    store = _FakeCarStore(n_samples=7, n_drivers=1)
    chunks = list(T.iter_car_telemetry(store, TARGET, chunk_rows=3))

    assert [len(c) for c in chunks] == [3, 3, 1]


def test_car_telemetry_empty_yields_nothing():
    store = _FakeCarStore(n_samples=0, n_drivers=1)
    assert list(T.iter_car_telemetry(store, TARGET)) == []


def test_car_telemetry_dtypes():
    store = _FakeCarStore(n_samples=1, n_drivers=1)
    df = _concat(T.iter_car_telemetry(store, TARGET))

    for col in TABLES["car_telemetry"].columns:
        assert str(df[col.name].dtype) == col.dtype


def test_position_telemetry_columns_and_row_count():
    store = _FakePositionStore(n_samples=2, n_drivers=2)
    df = _concat(T.iter_position_telemetry(store, TARGET))

    assert list(df.columns) == TABLES["position_telemetry"].column_names
    assert len(df) == 2 * 2


def test_position_telemetry_status_and_xyz():
    store = _FakePositionStore(n_samples=1, n_drivers=1)
    df = _concat(T.iter_position_telemetry(store, TARGET))

    row = df.iloc[0]
    assert row["status"] == "OnTrack"
    assert row["x"] == 1000
    assert row["y"] == -55
    assert row["z"] == 7


def test_position_telemetry_keeps_zero_xy_rows():
    store = _FakePositionStore(n_samples=1, n_drivers=1)
    store._samples[0]["Position"][0]["Entries"]["1"]["X"] = 0
    store._samples[0]["Position"][0]["Entries"]["1"]["Y"] = 0
    df = _concat(T.iter_position_telemetry(store, TARGET))

    assert len(df) == 1
    assert df.iloc[0]["x"] == 0
    assert df.iloc[0]["y"] == 0


def test_position_telemetry_chunking():
    store = _FakePositionStore(n_samples=7, n_drivers=1)
    chunks = list(T.iter_position_telemetry(store, TARGET, chunk_rows=3))

    assert [len(c) for c in chunks] == [3, 3, 1]


def test_position_telemetry_empty_yields_nothing():
    store = _FakePositionStore(n_samples=0, n_drivers=1)
    assert list(T.iter_position_telemetry(store, TARGET)) == []


def test_position_telemetry_dtypes():
    store = _FakePositionStore(n_samples=1, n_drivers=1)
    df = _concat(T.iter_position_telemetry(store, TARGET))

    for col in TABLES["position_telemetry"].columns:
        assert str(df[col.name].dtype) == col.dtype


def test_position_telemetry_key_columns_filled():
    store = _FakePositionStore(n_samples=1, n_drivers=1)
    df = _concat(T.iter_position_telemetry(store, TARGET))

    expected = key_values(TARGET)
    row = df.iloc[0]
    for key, value in expected.items():
        assert row[key] == value


def test_session_time_and_utc_present():
    store = _FakeCarStore(n_samples=1, n_drivers=1)
    df = _concat(T.iter_car_telemetry(store, TARGET))

    row = df.iloc[0]
    assert row["utc"] == "2024-03-02T15:00:01.000Z"
    assert row["session_time_s"] is not None
