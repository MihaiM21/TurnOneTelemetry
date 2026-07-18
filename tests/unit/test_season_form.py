"""Offline unit tests for the Season Form Guide (V2).

`_rolling_mean` edge cases, default top-10 selection, and payload shape are
verified against synthetic season-result rows — no network, Redis, or Mongo.
"""
from __future__ import annotations

import os

import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.analysis.v2 import _season_helpers as sh
from src.services.analysis.v2 import season_form as sf


def _race_row(round_nr, driver, constructor, position, classified=True):
    return {
        "round": round_nr, "driver_code": driver, "driver_name": driver.title(),
        "constructor": constructor, "position": position, "grid": position,
        "status": "Finished" if classified else "DNF", "classified": classified,
        "q1_s": None, "q2_s": None, "q3_s": None,
    }


def _quali_row(round_nr, driver, constructor, position):
    return {
        "round": round_nr, "driver_code": driver, "driver_name": driver.title(),
        "constructor": constructor, "position": position, "grid": None,
        "status": None, "classified": position is not None,
        "q1_s": None, "q2_s": None, "q3_s": None,
    }


# ----------------------------------------------------------------------
# _rolling_mean — Nones and window edges
# ----------------------------------------------------------------------
def test_rolling_mean_requires_two_points():
    """A single non-None value in the trailing window -> None (not a lone value)."""
    out = sf._rolling_mean([5], 3)
    assert out == [None]


def test_rolling_mean_skips_none_within_window():
    values = [1, None, 3, None, 5]
    out = sf._rolling_mean(values, 3)
    # i=0: [1] -> None (only 1 point)
    # i=1: [1,None] -> [1] -> None
    # i=2: [1,None,3] -> [1,3] -> mean 2.0
    # i=3: [None,3,None] -> [3] -> None
    # i=4: [3,None,5] -> [3,5] -> mean 4.0
    assert out == [None, None, 2.0, None, 4.0]


def test_rolling_mean_window_of_two():
    values = [10, 20, 30, 40]
    out = sf._rolling_mean(values, 2)
    assert out == [None, 15.0, 25.0, 35.0]


def test_rolling_mean_all_none():
    out = sf._rolling_mean([None, None, None], 3)
    assert out == [None, None, None]


def test_rolling_mean_empty():
    assert sf._rolling_mean([], 3) == []


# ----------------------------------------------------------------------
# Default top-10 selection
# ----------------------------------------------------------------------
def test_default_top_drivers_ranks_by_mean_finish():
    race_results = []
    # 12 drivers, mean finish = their own driver number for simplicity.
    for n in range(1, 13):
        driver = f"D{n:02d}"
        race_results.append(_race_row(1, driver, "Team", n))
        race_results.append(_race_row(2, driver, "Team", n))

    top = sf._default_top_drivers(race_results, 10)
    assert len(top) == 10
    assert top == [f"D{n:02d}" for n in range(1, 11)]


def test_default_top_drivers_excludes_unclassified():
    race_results = [
        _race_row(1, "AAA", "Team", 1, classified=True),
        _race_row(1, "BBB", "Team", None, classified=False),
    ]
    top = sf._default_top_drivers(race_results, 10)
    assert top == ["AAA"]


def test_default_top_drivers_ties_broken_alphabetically():
    race_results = [
        _race_row(1, "ZZZ", "Team", 3),
        _race_row(1, "AAA", "Team", 3),
    ]
    top = sf._default_top_drivers(race_results, 10)
    assert top == ["AAA", "ZZZ"]


def test_data_call_defaults_to_top_10(monkeypatch):
    race_results = []
    quali_results = []
    for n in range(1, 13):
        driver = f"D{n:02d}"
        race_results.append(_race_row(1, driver, "Team", n))
        quali_results.append(_quali_row(1, driver, "Team", n))

    monkeypatch.setattr(sf, "fetch_season_results", lambda year, kind: (
        race_results if kind == "race" else quali_results
    ))
    monkeypatch.setattr(sh, "season_cached_or_generate", lambda year, data_type, generator: generator())
    monkeypatch.setattr(sf, "season_cached_or_generate", sh.season_cached_or_generate)

    payload = sf.SeasonFormData()(2025)
    assert len(payload["drivers"]) == 10
    tlas = {d["tla"] for d in payload["drivers"]}
    assert tlas == {f"D{n:02d}" for n in range(1, 11)}


def test_data_call_respects_explicit_drivers(monkeypatch):
    race_results = [_race_row(1, "AAA", "Team", 1), _race_row(1, "BBB", "Team", 2)]
    quali_results = [_quali_row(1, "AAA", "Team", 1), _quali_row(1, "BBB", "Team", 2)]

    monkeypatch.setattr(sf, "fetch_season_results", lambda year, kind: (
        race_results if kind == "race" else quali_results
    ))
    monkeypatch.setattr(sf, "season_cached_or_generate", lambda year, data_type, generator: generator())

    payload = sf.SeasonFormData()(2025, drivers=["BBB"])
    assert [d["tla"] for d in payload["drivers"]] == ["BBB"]


# ----------------------------------------------------------------------
# Payload shape
# ----------------------------------------------------------------------
def test_build_payload_shape():
    race_results = [
        _race_row(1, "AAA", "Team1", 1), _race_row(1, "BBB", "Team2", 2),
        _race_row(2, "AAA", "Team1", 3), _race_row(2, "BBB", "Team2", 1),
    ]
    quali_results = [
        _quali_row(1, "AAA", "Team1", 1), _quali_row(1, "BBB", "Team2", 2),
        _quali_row(2, "AAA", "Team1", 2), _quali_row(2, "BBB", "Team2", 1),
    ]
    payload = sf.build_payload_from_results(race_results, quali_results, window=3)
    assert payload["window"] == 3
    assert payload["rounds"] == [1, 2]
    assert len(payload["drivers"]) == 2

    aaa = next(d for d in payload["drivers"] if d["tla"] == "AAA")
    assert aaa["team"] == "Team1"
    assert aaa["rounds"] == [1, 2]
    assert aaa["finish"] == [1, 3]
    assert aaa["quali"] == [1, 2]
    assert "finish_rolling" in aaa and "quali_rolling" in aaa


def test_build_payload_handles_missing_round_for_driver():
    """Driver B skips round 2 entirely (e.g. reserve driver stand-in) -> None."""
    race_results = [
        _race_row(1, "AAA", "Team1", 1), _race_row(1, "BBB", "Team2", 2),
        _race_row(2, "AAA", "Team1", 1),
    ]
    payload = sf.build_payload_from_results(race_results, [], window=3)
    bbb = next(d for d in payload["drivers"] if d["tla"] == "BBB")
    assert bbb["finish"] == [2, None]


# ----------------------------------------------------------------------
# Plot / error paths
# ----------------------------------------------------------------------
def test_plot_raises_when_no_drivers(monkeypatch):
    monkeypatch.setattr(sf, "SeasonFormData", lambda: (lambda *a, **k: {"drivers": [], "rounds": [], "window": 3}))
    with pytest.raises(DataNotAvailableError):
        sf.SeasonFormPlot()(2025)


def test_plot_renders_png(tmp_path, monkeypatch):
    payload = {
        "window": 3,
        "rounds": [1, 2, 3],
        "drivers": [
            {
                "tla": "AAA", "team": "Team1", "color": "#FF0000",
                "rounds": [1, 2, 3], "finish": [1, 2, 1], "quali": [2, 1, 1],
                "finish_rolling": [None, 1.5, 1.33], "quali_rolling": [None, 1.5, 1.33],
            },
            {
                "tla": "BBB", "team": "Team1", "color": "#FF0000",
                "rounds": [1, 2, 3], "finish": [2, 1, 2], "quali": [1, 2, 2],
                "finish_rolling": [None, 1.5, 1.67], "quali_rolling": [None, 1.5, 1.67],
            },
        ],
    }
    monkeypatch.chdir(tmp_path)
    out = sf.SeasonFormPlot._render(payload, 2025)
    assert os.path.isfile(out)
    assert out.endswith(".png")
