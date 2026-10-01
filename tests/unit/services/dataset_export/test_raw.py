"""Raw-stream export: file naming, gz round-trip, absent streams, cancellation."""

import gzip
import json

import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.dataset_export import raw as R


class _FakeStore:
    """Minimal stand-in for SessionDataStore exposing only what write_raw_streams needs."""

    def __init__(self):
        self._cache = {"car_data": ["cached"], "position_data": ["cached"]}

    def timing_data(self):
        return [{"_timestamp": "00:00:01.000", "Lines": {"1": {"Position": "1"}}}]

    def timing_app_data(self):
        return [{"_timestamp": "00:00:01.000", "Lines": {}}]

    def track_status(self):
        return [{"_timestamp": "00:00:01.000", "Status": "1"}]

    def weather_data(self):
        return [{"_timestamp": "00:00:01.000", "AirTemp": "20.0"}]

    def race_control(self):
        return [{"Category": "Flag", "Message": "GREEN LIGHT"}]

    def extra_stream(self, name):
        if name in ("team_radio", "top_three"):
            raise DataNotAvailableError(year=2024, gp="Test", session="R", reason=f"{name} missing")
        return [{"_timestamp": "00:00:01.000", "stream": name}]

    def car_data(self):
        return [{"_timestamp": "00:00:01.000", "Entries": []}]

    def position_data(self):
        return [{"_timestamp": "00:00:01.000", "Position": []}]

    def session_info(self):
        return {"Meeting": {"Name": "Test GP"}}

    def driver_list(self):
        return {"1": {"Tla": "VER"}}


class _BoomStore(_FakeStore):
    def track_status(self):
        raise RuntimeError("boom")


def _all_stream_names():
    return list(R.RAW_STREAMS.keys()) + list(R.JSON_DOCS)


def test_writes_expected_files_with_correct_names(tmp_path):
    store = _FakeStore()
    written, absent = R.write_raw_streams(store, tmp_path, tmp_path)

    for name in R.RAW_STREAMS:
        if name in ("team_radio", "top_three"):
            continue
        assert (tmp_path / f"{name}.jsonl.gz").exists()
        assert written[name].path == f"{name}.jsonl.gz"

    for name in R.JSON_DOCS:
        assert (tmp_path / f"{name}.json").exists()
        assert written[name].path == f"{name}.json"

    assert sorted(absent) == sorted(["team_radio", "top_three"])


def test_gz_round_trip_preserves_records(tmp_path):
    store = _FakeStore()
    written, _absent = R.write_raw_streams(store, tmp_path, tmp_path, streams=["timing_data"])

    with gzip.open(tmp_path / "timing_data.jsonl.gz", "rt", encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh]

    assert lines == store.timing_data()
    assert written["timing_data"].rows == 1


def test_json_doc_written_uncompressed(tmp_path):
    store = _FakeStore()
    R.write_raw_streams(store, tmp_path, tmp_path, streams=["session_info"])

    with open(tmp_path / "session_info.json", "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    assert payload == store.session_info()


def test_absent_stream_recorded_and_others_continue(tmp_path):
    store = _FakeStore()
    written, absent = R.write_raw_streams(store, tmp_path, tmp_path, streams=["team_radio", "timing_data"])

    assert absent == ["team_radio"]
    assert "timing_data" in written
    assert not (tmp_path / "team_radio.jsonl.gz").exists()


def test_unexpected_exception_propagates(tmp_path):
    store = _BoomStore()
    with pytest.raises(RuntimeError):
        R.write_raw_streams(store, tmp_path, tmp_path, streams=["track_status"])


def test_cancellation_stops_early(tmp_path):
    store = _FakeStore()
    order = ["timing_data", "timing_app_data", "track_status"]
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 1  # allow the first stream through, then stop

    written, absent = R.write_raw_streams(store, tmp_path, tmp_path, streams=order, cancelled=cancelled)

    assert len(written) == 1
    assert absent == []


def test_big_streams_evicted_from_in_process_cache(tmp_path):
    store = _FakeStore()
    R.write_raw_streams(store, tmp_path, tmp_path, streams=["car_data", "position_data"])

    assert "car_data" not in store._cache
    assert "position_data" not in store._cache


def test_default_streams_cover_every_named_and_json_stream():
    written = set(R.RAW_STREAMS.keys()) | set(R.JSON_DOCS)
    assert "car_data" in written and "position_data" in written
    assert "session_info" in written and "driver_list" in written
    # Big streams must be last so a cancellation stops before touching them.
    order = list(R.RAW_STREAMS.keys())
    assert order[-2:] == ["car_data", "position_data"]
