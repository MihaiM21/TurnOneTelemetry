"""Offline tests for the social-format renders of the speed-flavoured charts.

Covers ``track_comparison`` (two-driver minisector dominance map) and
``top_speed`` (Telemetry + SpeedTrap ranking). Data is synthetic and every
network / Mongo access is monkeypatched; renders are checked by the pixel size
of the PNG they write.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from src.services.analysis.v2 import top_speed as ts
from src.services.analysis.v2 import track_comparison as tc
from src.services.plotting import canvas

EVENT = "Test Grand Prix"


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
def _track_payload(n: int = 200) -> dict:
    """Two drivers tracing the same oval, minisectors 1..25 alternating winners."""
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    rows = []
    for driver in ("RUS", "LEC"):
        jitter = 0.0 if driver == "RUS" else 20.0
        for i, a in enumerate(angles):
            ms = int(i * 25 / n) + 1
            winner = 1 if ms % 3 else 2
            rows.append({
                "x": float(3000 * np.cos(a) + jitter), "y": float(1500 * np.sin(a) + jitter),
                "distance": float(i * 25.0), "speed": 250.0, "driver": driver,
                "minisector": ms, "fastest_driver": "RUS" if winner == 1 else "LEC",
                "fastest_driver_int": winner,
            })
    return {
        "driver1": "RUS", "driver2": "LEC",
        "driver1_color": "#27F4D2", "driver2_color": "#E80020",
        "telemetry": rows,
        "session_info": {"year": 2026, "event_name": EVENT, "session_name": "Q"},
    }


def _speed_rows():
    teams = ["Alpine", "Mercedes", "Ferrari", "McLaren", "Williams", "Audi", "Cadillac",
             "Haas F1 Team", "Racing Bulls", "Red Bull Racing", "Aston Martin", "Kick Sauber", "Extra Team"]
    speeds = [332.0 - i * 1.7 for i in range(len(teams))]
    colors = ["#0093CC", "#27F4D2", "#E80020", "#FF8000", "#64C4FF", "#FF5225", "#444444",
              "#FFFFFF", "#6692FF", "#3671C6", "#229971", "#52E252", "#FFFFFF"]
    return teams, speeds, colors


class _Client:
    """Stand-in for F1StaticClient that must never reach the network."""

    def get_event_info(self, year, identifier):
        return {"name": EVENT, "round_nr": 1, "circuit_key": None}

    def get_event_session_url(self, *a, **k):  # pragma: no cover - would mean a cache miss
        raise AssertionError("network access")


def _size(path: str):
    with Image.open(path) as img:
        return img.size


# ----------------------------------------------------------------------
# track_comparison
# ----------------------------------------------------------------------
@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_track_comparison_renders_exact_pixel_size(tmp_path, monkeypatch, fmt_name):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "loc").mkdir()
    path = tc._render_formatted(_track_payload(), 2026, EVENT, "Q", str(tmp_path / "loc"),
                                "Test Q 2026 RUS vs LEC.png", fmt_name, rotation=35.0)
    fmt = canvas.FORMATS[fmt_name]
    assert path.endswith(f"RUS vs LEC_{fmt_name}.png")
    assert _size(path) == (fmt.width_px, fmt.height_px)


def test_track_comparison_counts_minisectors_won():
    geo = tc._track_geometry(_track_payload(), 0.0)
    assert sum(geo["wins"]) == 25
    assert geo["wins"] == (17, 8)  # minisectors 3, 6, ... 24 go to driver 2


def _patch_plot(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    payload = _track_payload()
    monkeypatch.setattr(tc, "get_plot_data_from_mongo", lambda *a, **k: {"data": payload})
    monkeypatch.setattr(tc, "F1StaticClient", _Client)
    return payload


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_track_comparison_plot_with_fmt(tmp_path, monkeypatch, fmt_name):
    _patch_plot(monkeypatch, tmp_path)
    path = tc.TrackComparisonPlot(2026, 1, "Q", "rus", "lec", fmt=fmt_name)
    fmt = canvas.FORMATS[fmt_name]
    assert path == f"outputs/plots/2026/TestGrandPrix/Q/{EVENT} Q 2026 RUS vs LEC_{fmt_name}.png"
    assert _size(path) == (fmt.width_px, fmt.height_px)


def test_track_comparison_bad_fmt_fails_before_any_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def boom(*a, **k):
        raise AssertionError("data was fetched")

    monkeypatch.setattr(tc, "get_plot_data_from_mongo", boom)
    monkeypatch.setattr(tc, "F1StaticClient", boom)
    with pytest.raises(ValueError):
        tc.TrackComparisonPlot(2026, 1, "Q", "RUS", "LEC", fmt="widescreen")


def test_track_comparison_legacy_path_unchanged(tmp_path, monkeypatch):
    """fmt=None keeps the legacy renderer and file name."""
    _patch_plot(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(tc, "_generate_plot", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(tc, "_render_formatted", lambda *a, **k: pytest.fail("formatted path taken"))
    path = tc.TrackComparisonPlot(2026, 1, "Q", "RUS", "LEC")
    assert calls and path == f"outputs/plots/2026/TestGrandPrix/Q/{EVENT} Q 2026 RUS vs LEC.png"


# ----------------------------------------------------------------------
# top_speed
# ----------------------------------------------------------------------
@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_top_speed_renders_exact_pixel_size(tmp_path, monkeypatch, fmt_name):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "loc").mkdir()
    teams, speeds, colors = _speed_rows()
    path = ts._render_formatted(teams, speeds, colors, 2026, EVENT, "Q", str(tmp_path / "loc"),
                                "Top speed.png", "Telemetry", fmt_name)
    fmt = canvas.FORMATS[fmt_name]
    assert path.endswith(f"Top speed_{fmt_name}.png")
    assert _size(path) == (fmt.width_px, fmt.height_px)


def test_top_speed_story_shows_twelve_rows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "loc").mkdir()
    teams, speeds, colors = _speed_rows()
    seen = {}
    real = ts.cv.save_png

    def spy(fig, path, fmt, scale=1.0):
        chart = max(fig.axes, key=lambda a: len(a.patches))
        seen["labels"] = [t.get_text() for t in chart.get_yticklabels()]
        return real(fig, path, fmt, scale)

    monkeypatch.setattr(ts.cv, "save_png", spy)
    ts._render_formatted(teams, speeds, colors, 2026, EVENT, "Q", str(tmp_path / "loc"),
                         "Top speed.png", "Telemetry", "story")
    assert len(seen["labels"]) == 12 and "Extra Team" not in seen["labels"]


def _patch_speed_cache(monkeypatch, tmp_path, key: str):
    monkeypatch.chdir(tmp_path)
    teams, speeds, colors = _speed_rows()
    data = [{"Team": t, "Top Speed (km/h)": s, "Color": c} for t, s, c in zip(teams, speeds, colors)]
    monkeypatch.setattr(
        ts, "get_plot_data_from_mongo",
        lambda y, ident, e, data_type, version="v2": {"data": data, "metadata": {"event_name": EVENT}}
        if data_type == key else None,
    )
    monkeypatch.setattr(ts, "F1StaticClient", lambda: pytest.fail("network access"))


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_top_speed_telemetry_plot_with_fmt(tmp_path, monkeypatch, fmt_name):
    _patch_speed_cache(monkeypatch, tmp_path, "top_speed_telemetry")
    path = ts.TopSpeedPlot_Telemetry(2026, 1, "Q", fmt=fmt_name)
    fmt = canvas.FORMATS[fmt_name]
    assert path == f"outputs/plots/2026/TestGrandPrix/Q/Top speed comparison Telemetry 2026 {EVENT} Q_{fmt_name}.png"
    assert _size(path) == (fmt.width_px, fmt.height_px)


@pytest.mark.parametrize("fmt_name", ("landscape", "story"))
def test_top_speed_speedtrap_plot_with_fmt(tmp_path, monkeypatch, fmt_name):
    _patch_speed_cache(monkeypatch, tmp_path, "top_speed_speedtrap")
    path = ts.TopSpeedPlot_SpeedTrap(2026, 1, "Q", fmt=fmt_name)
    fmt = canvas.FORMATS[fmt_name]
    assert path.endswith(f"SpeedTrap 2026 {EVENT} Q_{fmt_name}.png")
    assert _size(path) == (fmt.width_px, fmt.height_px)


@pytest.mark.parametrize("func", ("TopSpeedPlot_Telemetry", "TopSpeedPlot_SpeedTrap"))
def test_top_speed_bad_fmt_fails_before_any_data(tmp_path, monkeypatch, func):
    monkeypatch.chdir(tmp_path)

    def boom(*a, **k):
        raise AssertionError("data was fetched")

    monkeypatch.setattr(ts, "get_plot_data_from_mongo", boom)
    monkeypatch.setattr(ts, "F1StaticClient", boom)
    with pytest.raises(ValueError):
        getattr(ts, func)(2026, 1, "Q", fmt="widescreen")


class _LegacyFigure(Exception):
    """Raised by the patched ``plt.subplots`` to stop the legacy render once it is reached."""


def test_top_speed_legacy_path_unchanged(tmp_path, monkeypatch):
    _patch_speed_cache(monkeypatch, tmp_path, "top_speed_telemetry")
    calls = []

    def subplots(*a, **k):
        # A plain exception: a StopIteration thrown out of a generator expression
        # becomes a RuntimeError on Python < 3.12 (PEP 479).
        calls.append(1)
        raise _LegacyFigure

    monkeypatch.setattr(ts.plt, "subplots", subplots)
    monkeypatch.setattr(ts, "_render_formatted", lambda *a, **k: pytest.fail("formatted path taken"))
    with pytest.raises(_LegacyFigure):
        ts.TopSpeedPlot_Telemetry(2026, 1, "Q")
    assert calls


def test_top_speed_cached_speed_key_either_spelling():
    import pandas as pd
    assert ts._speed_column(pd.DataFrame({"Speed": [1.0]})).tolist() == [1.0]
    assert ts._speed_column(pd.DataFrame({"Top Speed (km/h)": [2.0]})).tolist() == [2.0]
