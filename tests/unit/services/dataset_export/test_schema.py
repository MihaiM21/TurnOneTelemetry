import pandas as pd
import pytest

from src.services.dataset_export.schema import (
    KEY_COLUMNS,
    TABLES,
    coerce,
    empty_frame,
    tables_for_tier,
)


def test_every_table_starts_with_key_columns_and_has_unique_names():
    key_names = [c.name for c in KEY_COLUMNS]
    for name, spec in TABLES.items():
        assert spec.column_names[: len(key_names)] == key_names, name
        assert len(spec.column_names) == len(set(spec.column_names)), name


def test_tables_for_tier_returns_only_that_tier():
    tables = tables_for_tier("telemetry")
    assert tables
    assert all(t.tier == "telemetry" for t in tables)
    assert {t.name for t in tables} == {"car_telemetry", "position_telemetry"}


@pytest.mark.parametrize("name,spec", list(TABLES.items()))
def test_empty_frame_has_declared_columns_and_dtypes(name, spec):
    df = empty_frame(spec)
    assert list(df.columns) == spec.column_names
    assert len(df) == 0
    for col in spec.columns:
        assert str(df[col.name].dtype) == col.dtype


def test_coerce_handles_messy_numeric_strings():
    spec = TABLES["laps"]
    df = pd.DataFrame(
        {
            "year": [2026],
            "round": [1],
            "session": ["R"],
            "session_key": ["2026_01_R"],
            "lap_time_s": ["1:23.456"],
            "lap": [1.0],
        }
    )
    result = coerce(df, spec)
    assert result["lap_time_s"].isna().all()
    assert result["lap"].iloc[0] == 1
    assert str(result["lap"].dtype) == "Int64"


def test_coerce_fills_missing_columns_and_drops_extra():
    spec = TABLES["drivers"]
    df = pd.DataFrame({"year": [2026], "round": [1], "session": ["R"], "session_key": ["k"], "junk": [1]})
    result = coerce(df, spec)
    assert list(result.columns) == spec.column_names
    assert "junk" not in result.columns
    assert result["driver_code"].isna().all()


def test_coerce_int_from_float_one():
    spec = TABLES["laps"]
    df = pd.DataFrame(
        {
            "year": [2026],
            "round": [1],
            "session": ["R"],
            "session_key": ["k"],
            "stint_number": [1.0],
        }
    )
    result = coerce(df, spec)
    assert result["stint_number"].iloc[0] == 1


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (True, True),
        (False, False),
        ("true", True),
        ("False", False),
        ("yes", True),
        (1, True),
        (0, False),
        (float("nan"), None),
    ],
)
def test_coerce_boolean_from_various_inputs(value, expected):
    spec = TABLES["laps"]
    df = pd.DataFrame(
        {
            "year": [2026],
            "round": [1],
            "session": ["R"],
            "session_key": ["k"],
            "is_deleted": [value],
        }
    )
    result = coerce(df, spec)
    got = result["is_deleted"].iloc[0]
    if expected is None:
        assert pd.isna(got)
    else:
        assert got == expected


def test_coerce_is_idempotent():
    spec = TABLES["results"]
    df = empty_frame(spec)
    once = coerce(df, spec)
    twice = coerce(once, spec)
    pd.testing.assert_frame_equal(once, twice)


def test_coerce_never_raises_on_odd_input():
    spec = TABLES["car_telemetry"]
    df = pd.DataFrame({"rpm": [{"nested": "dict"}], "speed": [[1, 2, 3]]})
    result = coerce(df, spec)
    assert list(result.columns) == spec.column_names
