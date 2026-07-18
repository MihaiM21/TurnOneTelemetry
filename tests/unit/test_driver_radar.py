"""Offline unit tests for the driver-radar payload builder (V2).

Covers the pure ``build_radar_payload`` seam, driver filtering, and the small
argument parsers, with no network / store dependency.
"""
from __future__ import annotations

import os

import pytest

from src.services.analysis.v2 import driver_radar as dr
from src.services.analysis.v2._radar_axes import AxisSpec

# A tiny two-axis spec: one field-relative (higher better), one absolute ratio.
_AXES = [
    AxisSpec("A", "a", higher_is_better=True, method="percentile"),
    AxisSpec("B", "b", higher_is_better=True, method="absolute", lo=0.0, hi=1.0),
]


def _rows():
    return [
        {"tla": "VER", "team": "Red Bull Racing", "color": "#3671C6",
         "raw": {"a": 10.0, "b": 0.5}},
        {"tla": "NOR", "team": "McLaren", "color": "#FF8000",
         "raw": {"a": 30.0, "b": 0.25}},
    ]


def test_build_radar_payload_shape_and_scaling():
    population = {"a": [10.0, 20.0, 30.0]}
    payload = dr.build_radar_payload("session", _AXES, _rows(), population)

    assert payload["scope"] == "session"
    assert payload["axes"] == ["A", "B"]
    assert payload["hero"] is False
    assert len(payload["drivers"]) == 2

    for d in payload["drivers"]:
        assert len(d["values"]) == len(_AXES)
        for v in d["values"]:
            assert v is None or 0.0 <= v <= 100.0

    ver, nor = payload["drivers"]
    # NOR has the higher raw "a" -> higher percentile score on axis A.
    assert nor["values"][0] > ver["values"][0]
    # Absolute axis B: 0.5 -> 50, 0.25 -> 25.
    assert ver["values"][1] == pytest.approx(50.0)
    assert nor["values"][1] == pytest.approx(25.0)


def test_build_radar_payload_missing_metric_is_none():
    rows = [{"tla": "SAI", "team": "Ferrari", "color": "#E80020", "raw": {"a": 10.0}}]
    payload = dr.build_radar_payload("session", _AXES, rows, {"a": [10.0]})
    d = payload["drivers"][0]
    # "b" missing -> None spoke; single driver -> hero.
    assert d["values"][1] is None
    assert payload["hero"] is True


def test_population_from_field_only_percentile_axes():
    field = {
        "1": {"a": 10.0, "b": 0.5},
        "2": {"a": 20.0, "b": None},
    }
    pop = dr._population_from_field(field, _AXES)
    assert set(pop.keys()) == {"a"}          # absolute axis excluded
    assert sorted(pop["a"]) == [10.0, 20.0]


def test_filter_radar_drivers_recomputes_hero():
    payload = dr.build_radar_payload("season", _AXES, _rows(), {"a": [10.0, 30.0]})
    filtered = dr._filter_radar_drivers(payload, ["VER"])
    assert [d["tla"] for d in filtered["drivers"]] == ["VER"]
    assert filtered["hero"] is True


# ----------------------------------------------------------------------
# Argument parsers
# ----------------------------------------------------------------------
def test_parse_drivers_arg_variants():
    assert dr._parse_drivers_arg("ver, nor ,pia") == ["VER", "NOR", "PIA"]
    assert dr._parse_drivers_arg(["ham", "lec"]) == ["HAM", "LEC"]
    assert dr._parse_drivers_arg(None) == []
    assert dr._parse_drivers_arg("") == []


def test_career_years_span_and_list():
    assert dr._career_years("2021-2024", 0) == [2021, 2022, 2023, 2024]
    assert dr._career_years("2021,2023", 0) == [2021, 2023]
    assert dr._career_years([2020, 2021], 0) == [2020, 2021]
    assert dr._career_years(None, 2026) == [2026]


def test_default_session_nums_ranks_by_best_lap():
    field = {
        "1": {"quali_pace": 91.0},
        "2": {"quali_pace": 90.0},
        "3": {"quali_pace": None},
    }
    assert dr._default_session_nums(field, 2) == ["2", "1"]


# ----------------------------------------------------------------------
# Render smoke test
# ----------------------------------------------------------------------
def test_render_session_radar_png(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = dr.build_radar_payload("session", _AXES, _rows(), {"a": [10.0, 30.0]})
    from src.services.analysis.v2 import _radar_render as render
    out = render.render_radar(
        payload, title="Driver Radar", subtitle="2025 Test GP R",
        scope="session", year=2025, event="Test GP", session="R",
    )
    assert os.path.isfile(out)
    assert out.endswith(".png")
