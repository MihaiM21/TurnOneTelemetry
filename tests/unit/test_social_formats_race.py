"""Offline tests for the social-format renders of Position Changes and Race Story.

Synthetic payloads in the exact shape each Data callable produces; no network,
no Redis/Mongo.
"""
from __future__ import annotations

import os
import random

import pytest
from PIL import Image

from src.services.analysis.v2 import position_changes as pc
from src.services.analysis.v2 import race_story as rs
from src.services.plotting import canvas

_TEAMS = [
    ("Red Bull Racing", "#3671C6"), ("McLaren", "#FF8000"), ("Ferrari", "#E8002D"),
    ("Mercedes", "#27F4D2"), ("Aston Martin", "#229971"), ("Alpine", "#FF87BC"),
    ("Williams", "#64C4FF"), ("Racing Bulls", "#6692FF"), ("Kick Sauber", "#52E252"),
    ("Haas F1 Team", "#B6BABD"),
]
_CODES = ["VER", "TSU", "NOR", "PIA", "LEC", "HAM", "RUS", "ANT", "ALO", "STR",
          "GAS", "COL", "ALB", "SAI", "LAW", "HAD", "HUL", "BOR", "OCO", "BEA"]


def _position_payload(n_drivers: int = 20, laps: int = 57):
    rng = random.Random(7)
    grid = list(range(1, n_drivers + 1))
    rng.shuffle(grid)
    payload = []
    for i, code in enumerate(_CODES[:n_drivers]):
        team, color = _TEAMS[i // 2]
        pos = grid[i]
        series = [{"lap": 0, "position": pos}]
        for lap in range(1, laps + 1):
            target = i + 1
            if pos < target and rng.random() < 0.3:
                pos += 1
            elif pos > target and rng.random() < 0.3:
                pos -= 1
            series.append({"lap": lap, "position": pos})
        payload.append({"driver": code, "team": team, "color": color, "start_pos": series[0]["position"],
                        "end_pos": series[-1]["position"], "positions": series})
    payload.sort(key=lambda d: d["end_pos"])
    return payload


_PERIODS = [
    {"status": "GREEN", "start_lap": 1, "end_lap": 10},
    {"status": "SC", "start_lap": 11, "end_lap": 14},
    {"status": "VSC", "start_lap": 30, "end_lap": 31},
    {"status": "RED", "start_lap": 40, "end_lap": None},
]


def _story_payload(laps: int = 57):
    drivers = []
    for i, code in enumerate(_CODES[:10]):
        team, color = _TEAMS[i // 2]
        series = []
        for lap in range(1, laps + 1):
            gap = None if 40 <= lap <= 42 else round(i * 2.5 + lap * 0.15 * i, 3)
            series.append({"lap": lap, "gap_s": gap})
        drivers.append({
            "driver": code, "team": team, "color": color, "finish_rank": i + 1, "laps": series,
            "pit_stops": [{"lap": 18 + i, "compound": "HARD"}, {"lap": 39, "compound": "MEDIUM"}],
            "last_lap": laps,
        })
    moments = [{"lap": lap, "kind": kind, "caption": f"L{lap} something happens", "n": n}
               for n, (lap, kind) in enumerate(
                   [(3, "lead_change"), (4, "retirement"), (4, "penalty"), (12, "lead_change"),
                    (13, "retirement"), (25, "penalty"), (33, "lead_change")], start=1)]
    return {"drivers": drivers, "key_moments": moments, "track_status_periods": _PERIODS}


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_position_changes_formatted_size(tmp_path, monkeypatch, fmt_name):
    monkeypatch.chdir(tmp_path)
    fmt = canvas.get_format(fmt_name)
    out = pc.PositionChangesPlot._render_formatted(
        _position_payload(), _PERIODS, 2026, "Azerbaijan Grand Prix", "R", fmt_name)
    assert out.endswith(f"_{fmt_name}.png")
    assert "Position changes 2026 Azerbaijan Grand Prix R_" in out
    with Image.open(out) as img:
        assert img.size == (fmt.width_px, fmt.height_px)


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_race_story_formatted_size(tmp_path, monkeypatch, fmt_name):
    monkeypatch.chdir(tmp_path)
    fmt = canvas.get_format(fmt_name)
    out = rs.RaceStoryPlot._render_formatted(_story_payload(), 2026, "Azerbaijan Grand Prix", "R", fmt_name)
    assert out.endswith(f"_{fmt_name}.png")
    with Image.open(out) as img:
        assert img.size == (fmt.width_px, fmt.height_px)


def test_race_story_formatted_without_optional_parts(tmp_path, monkeypatch):
    """No pit stops, moments or shading: no legend, no marker strip, still renders."""
    monkeypatch.chdir(tmp_path)
    payload = _story_payload()
    for d in payload["drivers"]:
        d["pit_stops"] = []
    payload["key_moments"] = []
    payload["track_status_periods"] = []
    out = rs.RaceStoryPlot._render_formatted(payload, 2026, "Azerbaijan Grand Prix", "S", "story")
    assert os.path.isfile(out)


def test_position_changes_formatted_without_periods(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = pc.PositionChangesPlot._render_formatted(_position_payload(8, 10), [], 2026, "Test GP", "S", "square")
    assert os.path.isfile(out)


def test_spread_labels_separates_close_ends():
    ys = rs._spread_labels([(57.0, 1.0), (57.0, 1.2), (57.0, 1.3), (30.0, 1.1)], min_gap=1.0, min_dx=5.0)
    close = sorted(ys[:3])
    assert all(b - a >= 1.0 - 1e-9 for a, b in zip(close, close[1:]))
    assert ys[3] == 1.1  # far away in x: untouched


def test_assign_rows_staggers_close_markers():
    rows = rs._assign_rows([4, 4, 5, 20], min_dx=3.0)
    assert rows[0] == 0 and rows[1] == 1 and rows[2] == 2 and rows[3] == 0


class _Boom:
    def __init__(self, *a, **k):
        raise AssertionError("data must not be fetched for an invalid format")

    def __call__(self, *a, **k):  # pragma: no cover
        raise AssertionError("data must not be fetched for an invalid format")


@pytest.mark.parametrize("module,plot_cls,data_cls", [
    (pc, "PositionChangesPlot", "PositionChangesData"),
    (rs, "RaceStoryPlot", "RaceStoryData"),
])
def test_bad_format_raises_before_data(monkeypatch, module, plot_cls, data_cls):
    monkeypatch.setattr(module, data_cls, _Boom)
    monkeypatch.setattr(module, "SessionDataStore", _Boom)
    with pytest.raises(ValueError):
        getattr(module, plot_cls)()(2026, "Azerbaijan Grand Prix", "R", fmt="widescreen")


@pytest.mark.parametrize("module,plot_cls,data_cls,payload,legacy_name", [
    (pc, "PositionChangesPlot", "PositionChangesData", _position_payload(6, 8),
     "Position changes 2026 Test Grand Prix R.png"),
    (rs, "RaceStoryPlot", "RaceStoryData", _story_payload(), "Race story 2026 Test Grand Prix R.png"),
])
def test_fmt_none_keeps_legacy_path(tmp_path, monkeypatch, module, plot_cls, data_cls, payload, legacy_name):
    """fmt=None -> legacy file name; fmt set -> same folder, suffixed name, same payload."""
    monkeypatch.chdir(tmp_path)
    calls = []

    class _Data:
        def __call__(self, *a, **k):
            calls.append(a)
            return payload

    class _Store:
        def __init__(self, *a, **k):
            self.event_name = "Test Grand Prix"

    monkeypatch.setattr(module, data_cls, _Data)
    monkeypatch.setattr(module, "SessionDataStore", _Store)
    if module is pc:
        monkeypatch.setattr(module, "get_track_status_periods", lambda store: _PERIODS)

    legacy = getattr(module, plot_cls)()(2026, "Test", "R")
    assert legacy.endswith(legacy_name)
    formatted = getattr(module, plot_cls)()(2026, "Test", "R", fmt="portrait")
    assert formatted == legacy[:-4] + "_portrait.png"
    with Image.open(formatted) as img:
        assert img.size == (1080, 1350)
    assert len(calls) == 2
