"""Offline tests for the social-format renders of the qualifying charts.

Each format must produce a PNG of exactly the format's pixel size, and a bad
format name must fail before any data is fetched.
"""
from __future__ import annotations

import pytest
from PIL import Image

from src.services.analysis.v2 import qualifying_results as qr
from src.services.analysis.v2 import theoretical_best as tb
from src.services.plotting import canvas


def _quali_data(n=22):
    teams = ["Mercedes", "Ferrari", "McLaren", "Red Bull Racing", "Cadillac", "Williams"]
    rows = []
    for i in range(n):
        rows.append({
            "Driver": f"D{i:02d}"[:3].upper(),
            "Team": teams[i % len(teams)],
            "LapTime": "1:42.526" if i == 0 else f"1:4{3 + i // 12}.{i * 37 % 1000:03d}",
            "LapTimeDelta": 0.0 if i == 0 else round(0.2 * i + 0.013 * (i % 3), 3),
            "Color": "#42423e" if i % 6 == 4 else "#3671C6",
        })
    return rows


def _theo_payload(n=22):
    teams = ["Mercedes", "Ferrari", "McLaren", "Red Bull Racing", "Cadillac", "Williams"]
    rows = []
    for i in range(n):
        theo = 102.5 + 0.21 * i
        delta = 0.0 if i % 5 == 0 else round(0.07 * (i % 7) + 0.03, 3)
        rows.append({
            "driver": f"D{i:02d}"[:3].upper(), "team": teams[i % len(teams)], "color": "#3671C6",
            "theoretical_s": round(theo, 3), "actual_s": round(theo + delta, 3), "delta_s": delta,
        })
    return rows


@pytest.mark.parametrize("fmt", canvas.FORMAT_NAMES)
def test_quali_results_formatted_size(tmp_path, monkeypatch, fmt):
    monkeypatch.chdir(tmp_path)
    path = qr._render_formatted(_quali_data(), fmt, 2026, "Test Grand Prix", "Q")
    assert path.endswith(f"results{canvas.format_suffix(fmt)}.png")
    f = canvas.get_format(fmt)
    with Image.open(tmp_path / path) as img:
        assert img.size == (f.width_px, f.height_px)


@pytest.mark.parametrize("fmt", canvas.FORMAT_NAMES)
def test_theoretical_best_formatted_size(tmp_path, monkeypatch, fmt):
    monkeypatch.chdir(tmp_path)
    path = tb._render_formatted(_theo_payload(), fmt, 2026, "Test Grand Prix", "Q")
    assert path.endswith(f"Q{canvas.format_suffix(fmt)}.png")
    f = canvas.get_format(fmt)
    with Image.open(tmp_path / path) as img:
        assert img.size == (f.width_px, f.height_px)


def test_short_grid_renders(tmp_path, monkeypatch):
    """A one-car payload (all gaps zero) must not divide by zero."""
    monkeypatch.chdir(tmp_path)
    assert qr._render_formatted(_quali_data(1), "square", 2026, "Test Grand Prix", "Q")
    assert tb._render_formatted(_theo_payload(1)[:1], "square", 2026, "Test Grand Prix", "Q")


def test_quali_plot_rejects_bad_format_before_fetching(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("data was fetched before the format was validated")

    monkeypatch.setattr(qr, "get_plot_data_from_mongo", _boom)
    monkeypatch.setattr(qr, "F1StaticClient", _boom)
    with pytest.raises(ValueError):
        qr.QualiResultsPlot(2026, "Azerbaijan Grand Prix", "Q", fmt="bogus")


def test_theoretical_best_plot_rejects_bad_format_before_fetching(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("data was fetched before the format was validated")

    monkeypatch.setattr(tb, "assert_session_type", _boom)
    monkeypatch.setattr(tb, "TheoreticalBestData", _boom)
    monkeypatch.setattr(tb, "SessionDataStore", _boom)
    with pytest.raises(ValueError):
        tb.TheoreticalBestPlot()(2026, "Azerbaijan Grand Prix", "Q", fmt="bogus")


def test_legible_color_lifts_dark_colours_only():
    assert qr.legible_color("#3671C6") == "#3671C6"
    lifted = qr.legible_color("#42423e")
    assert lifted != "#42423e" and lifted.startswith("#")
