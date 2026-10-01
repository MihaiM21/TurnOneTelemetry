import gzip
import json
import threading

import pandas as pd
import pytest

from src.services.dataset_export.schema import PARTITION_COLUMNS, TABLES, empty_frame, file_columns
from src.services.dataset_export.writer import (
    ParquetChunkWriter,
    append_jsonl,
    partition_dir,
    relpath,
    rewrite_jsonl_without,
    sha256_of,
    write_json,
    write_jsonl_gz,
    write_parquet,
)


def _sample_laps_df(n=3):
    spec = TABLES["laps"]
    base = empty_frame(spec)
    rows = []
    for i in range(n):
        row = {c: None for c in spec.column_names}
        row.update(
            {
                "year": 2026,
                "round": 1,
                "session": "R",
                "session_key": "2026_01_R",
                "lap": i + 1,
                "lap_time_s": 90.0 + i,
            }
        )
        rows.append(row)
    return pd.concat([base, pd.DataFrame(rows)], ignore_index=True)


def test_partition_dir_layout(tmp_path):
    d = partition_dir(tmp_path, "laps", 2026, 1, "R")
    assert d == tmp_path / "laps" / "year=2026" / "round=01" / "session=R"


def test_relpath_is_posix(tmp_path):
    p = tmp_path / "laps" / "year=2026" / "file.parquet"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    assert relpath(tmp_path, p) == "laps/year=2026/file.parquet"


def test_write_parquet_round_trip_and_no_tmp_left(tmp_path):
    spec = TABLES["laps"]
    df = _sample_laps_df()
    path = tmp_path / "laps" / "part.parquet"
    stat = write_parquet(df, path, spec, tmp_path)

    assert path.exists()
    assert not path.with_name(path.name + ".tmp").exists()
    assert stat.rows == 3
    assert stat.bytes == path.stat().st_size
    assert stat.sha256 == sha256_of(path)

    read_back = pd.read_parquet(path)
    assert list(read_back.columns) == file_columns(spec)


def test_write_parquet_schema_equality_with_different_missing_columns(tmp_path):
    spec = TABLES["laps"]
    df1 = _sample_laps_df(1)
    df2 = _sample_laps_df(1).drop(columns=["lap_time_s"])

    path1 = tmp_path / "a.parquet"
    path2 = tmp_path / "b.parquet"
    write_parquet(df1, path1, spec, tmp_path)
    write_parquet(df2, path2, spec, tmp_path)

    import pyarrow.parquet as pq

    schema1 = pq.read_schema(path1)
    schema2 = pq.read_schema(path2)
    assert schema1.equals(schema2)


def test_chunk_writer_zero_chunks(tmp_path):
    spec = TABLES["laps"]
    path = tmp_path / "chunked.parquet"
    with ParquetChunkWriter(path, spec, tmp_path) as writer:
        pass
    assert writer.stat is not None
    assert writer.stat.rows == 0
    assert path.exists()
    assert not path.with_name(path.name + ".tmp").exists()
    df = pd.read_parquet(path)
    assert list(df.columns) == file_columns(spec)


def test_chunk_writer_one_chunk(tmp_path):
    spec = TABLES["laps"]
    path = tmp_path / "chunked_one.parquet"
    df = _sample_laps_df(2)
    with ParquetChunkWriter(path, spec, tmp_path) as writer:
        writer.write(df)
    assert writer.stat.rows == 2
    result = pd.read_parquet(path)
    assert len(result) == 2


def test_chunk_writer_three_chunks(tmp_path):
    spec = TABLES["laps"]
    path = tmp_path / "chunked_three.parquet"
    with ParquetChunkWriter(path, spec, tmp_path) as writer:
        writer.write(_sample_laps_df(1))
        writer.write(_sample_laps_df(2))
        writer.write(_sample_laps_df(3))
    assert writer.stat.rows == 6
    result = pd.read_parquet(path)
    assert len(result) == 6


def test_chunk_writer_deletes_tmp_on_exception(tmp_path):
    spec = TABLES["laps"]
    path = tmp_path / "failing.parquet"
    with pytest.raises(RuntimeError):
        with ParquetChunkWriter(path, spec, tmp_path) as writer:
            writer.write(_sample_laps_df(1))
            raise RuntimeError("boom")
    assert not path.exists()
    assert not path.with_name(path.name + ".tmp").exists()


def test_write_jsonl_gz_round_trip(tmp_path):
    records = [{"a": 1}, {"a": 2}, {"a": 3}]
    path = tmp_path / "data.jsonl.gz"
    stat = write_jsonl_gz(records, path, tmp_path)
    assert stat.rows == 3
    assert not path.with_name(path.name + ".tmp").exists()

    with gzip.open(path, "rt", encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh]
    assert lines == records


def test_write_json(tmp_path):
    path = tmp_path / "obj.json"
    stat = write_json({"hello": "world"}, path, tmp_path)
    assert stat.rows == 1
    assert json.loads(path.read_text(encoding="utf-8")) == {"hello": "world"}


def test_append_jsonl_and_rewrite_without(tmp_path):
    lock = threading.Lock()
    path = tmp_path / "corpus.jsonl"

    count1 = append_jsonl([{"session_key": "a", "x": 1}, {"session_key": "b", "x": 2}], path, lock)
    assert count1 == 2

    count2 = append_jsonl([{"session_key": "a", "x": 3}], path, lock)
    assert count2 == 1

    removed = rewrite_jsonl_without(path, "a", lock)
    assert removed == 2

    remaining = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert remaining == [{"session_key": "b", "x": 2}]


def test_rewrite_jsonl_without_missing_file_is_noop(tmp_path):
    lock = threading.Lock()
    path = tmp_path / "missing.jsonl"
    assert rewrite_jsonl_without(path, "a", lock) == 0


def test_partitioned_dataset_reads_back_with_hive_columns(tmp_path):
    """Partition columns live in the path only; a directory read restores them without a schema clash."""
    from src.services.dataset_export.writer import partition_dir, write_parquet

    spec = TABLES["laps"]
    for year, rnd, sess in [(2024, 1, "R"), (2024, 2, "Q")]:
        df = pd.DataFrame([{"year": year, "round": rnd, "session": sess, "session_key": f"{year}_{rnd:02d}_{sess}",
                            "driver_number": "1", "lap": 1, "lap_time_s": 90.0}])
        write_parquet(df, partition_dir(tmp_path, "laps", year, rnd, sess) / "part.parquet", spec, tmp_path)
    back = pd.read_parquet(tmp_path / "laps")
    assert len(back) == 2
    assert set(PARTITION_COLUMNS) <= set(back.columns)
    assert sorted(back["session"].astype(str)) == ["Q", "R"]
    assert list(back["year"].astype(int)) == [2024, 2024]
    assert "year" not in file_columns(spec) and "session_key" in file_columns(spec)
