"""Energy clipping: detection maths, payload assembly, gating and rendering (all offline)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.analysis.v2 import _clipping_core as core
from src.services.analysis.v2 import _clipping_render as render
from src.services.analysis.v2 import energy_clipping as ec
from src.services.plotting import canvas


# ---------------------------------------------------------------------------
# Synthetic laps
# ---------------------------------------------------------------------------
def _grid(points, length=1500.0, brake_from=None, throttle_off_from=None):
    """Distance grid from ``[(distance, speed), ...]`` knots at 100 % throttle."""
    d = np.arange(0.0, length + 1e-9, core.GRID_STEP_M)
    kd, kv = zip(*points)
    speed = np.interp(d, kd, kv)
    throttle = np.full_like(d, 100.0)
    brake = np.zeros_like(d)
    if brake_from is not None:
        brake[d >= brake_from] = 1.0
    if throttle_off_from is not None:
        throttle[d >= throttle_off_from] = 0.0
    return {"d": d, "t": d / 80.0, "speed": speed, "throttle": throttle, "brake": brake,
            "x": d.copy(), "y": np.zeros_like(d)}


def _assert_disjoint_sorted(zones):
    for a, b in zip(zones, zones[1:]):
        assert a["end_m"] < b["start_m"]
    for z in zones:
        assert z["start_m"] < z["end_m"]


def test_straight_that_bleeds_speed_is_one_zone():
    grid = _grid([(0, 200), (400, 300), (600, 280), (1500, 280)])
    zones = core.detect_clipping(grid)
    assert len(zones) == 1
    z = zones[0]
    assert z["kmh_lost"] == pytest.approx(20, abs=3)
    assert z["time_lost_s"] > 0
    assert 350 <= z["start_m"] <= 450 and z["end_m"] >= 550


def test_rising_and_plateau_straight_has_no_zone():
    grid = _grid([(0, 200), (600, 320), (1500, 320)])
    assert core.detect_clipping(grid) == []


def test_speed_drop_under_braking_is_not_clipping():
    grid = _grid([(0, 300), (800, 300), (1000, 150), (1500, 150)], brake_from=800, throttle_off_from=800)
    assert core.detect_clipping(grid) == []


def test_drop_just_before_a_brake_zone_is_ignored():
    # 300 -> 294 over the last 50 m of flat-out running, then the brakes.
    grid = _grid([(0, 300), (950, 300), (1000, 294), (1030, 200), (1500, 150)],
                 brake_from=1000, throttle_off_from=1000)
    assert core.detect_clipping(grid) == []


def test_dips_backing_to_the_same_peak_merge_into_one_zone():
    grid = _grid([(0, 200), (400, 300), (500, 290), (580, 290), (680, 278), (1500, 278)])
    zones = core.detect_clipping(grid)
    assert len(zones) == 1
    assert zones[0]["kmh_lost"] == pytest.approx(22, abs=4)
    _assert_disjoint_sorted(zones)


def test_zones_are_disjoint_and_sorted_across_separate_straights():
    grid = _grid([(0, 200), (300, 300), (450, 285), (500, 285), (560, 150), (700, 150),
                  (1000, 310), (1150, 290), (1500, 290)])
    grid["throttle"][(grid["d"] >= 500) & (grid["d"] <= 800)] = 0.0  # lift + slow corner between them
    zones = core.detect_clipping(grid)
    assert len(zones) >= 2
    _assert_disjoint_sorted(zones)


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------
def _lap(tla, color, lap_time, loss, length=1500.0):
    """A ``load_field_laps``-shaped lap whose straight bleeds ``loss`` km/h."""
    n = int(length / 5) + 1
    d = np.linspace(0.0, length, n)
    knots = [(0, 200), (400, 300), (600, 300 - loss), (length, 300 - loss)]
    speed = np.interp(d, *zip(*knots))
    ang = 2 * np.pi * d / length
    trace = pd.DataFrame({
        "d": d, "t": d / 80.0, "speed": speed, "throttle": 100.0, "brake": 0.0,
        "x": 5000 * np.cos(ang), "y": 3000 * np.sin(ang),
    })
    return {"tla": tla, "team": f"Team {tla}", "color": color, "lap_time": lap_time, "trace": trace}


_SPECS = [("AAA", "#ff0000", 84.2, 8), ("BBB", "#00ff00", 83.9, 30), ("CCC", "#0000ff", 84.5, 18),
          ("DDD", "#ffff00", 84.8, 4), ("EEE", "#ff00ff", 85.0, 12), ("FFF", "#00ffff", 85.1, 25),
          ("GGG", "#888888", 85.3, 6), ("HHH", "#ff8800", 85.4, 22), ("III", "#8800ff", 85.6, 10),
          ("JJJ", "#88ff00", 85.9, 16), ("KKK", "#0088ff", 86.0, 3), ("LLL", "#ff0088", 86.2, 14),
          ("MMM", "#aaaaaa", 86.4, 9), ("NNN", "#446688", 86.6, 20)]


def _payload(count=3):
    entries = [ec._driver_entry(_lap(*spec)) for spec in _SPECS[:count]]
    return ec.build_payload(entries, rotation=35, year=2026, event_name="Test Grand Prix", session="Q")


def test_build_payload_orders_by_time_lost_and_strips_grids():
    payload = _payload()
    lost = [d["time_lost_s"] for d in payload["drivers"]]
    assert lost == sorted(lost, reverse=True)
    assert payload["drivers"][0]["driver"] == "BBB"
    assert all("_grid" not in d for d in payload["drivers"])
    assert payload["reference_driver"] == "BBB"  # quickest lap
    for d in payload["drivers"]:
        assert d["zones"]
        for z in d["zones"]:
            assert 0.0 <= z["start_fraction"] < z["end_fraction"] <= 1.0
    track = payload["track"]
    assert track["rotation"] == 35 and len(track["x"]) == len(track["fraction"])
    hi = payload["highlights"]
    assert hi["most_time_lost"]["driver"] == "BBB"
    assert hi["least_time_lost"]["driver"] == "AAA"
    assert hi["biggest_single_drop"]["driver"] == "BBB"
    assert hi["pole_lap"]["driver"] == "BBB"
    assert hi["field_median_time_lost_s"] > 0


def test_pre_2026_is_refused_without_touching_the_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network / cache must not be touched")

    monkeypatch.setattr(ec, "cached_or_generate", boom)
    with pytest.raises(DataNotAvailableError):
        ec.EnergyClippingData()(2025, "Azerbaijan", "Q")


def test_plot_validates_format_before_fetching(monkeypatch):
    class Boom:
        def __call__(self, *a, **k):
            raise AssertionError("must not fetch for a bad format")

    monkeypatch.setattr(ec, "EnergyClippingData", Boom)
    with pytest.raises(ValueError):
        ec.EnergyClippingPlot()(2026, "Azerbaijan", "Q", fmt="widescreen")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_render_every_format_at_exact_pixel_size(tmp_path, monkeypatch, fmt_name):
    from PIL import Image

    monkeypatch.chdir(tmp_path)
    path = render.render(_payload(14), fmt_name)
    fmt = canvas.get_format(fmt_name)
    with Image.open(path) as img:
        assert img.size == (fmt.width_px, fmt.height_px)
    assert path.endswith(f"Energy clipping BBB_{fmt_name}.png")


def test_render_highlighted_driver_outside_the_cut_stays_visible(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = _payload(14)
    last = payload["drivers"][-1]["driver"]
    rows = render._rows(payload, last, 10)
    assert len(rows) == 10 and rows[-1]["driver"] == last
    assert render.render(payload, "story", driver=last.lower()).endswith(f"Energy clipping {last}_story.png")


def test_render_unknown_driver_lists_valid_codes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="AAA"):
        render.render(_payload(), driver="ZZZ")


def test_render_handles_a_payload_without_a_track_outline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = _payload()
    payload["track"] = None
    assert render.render(payload, "square").endswith("_square.png")


def test_plot_class_renders_from_the_data_callable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = _payload()
    monkeypatch.setattr(ec, "EnergyClippingData", lambda: (lambda y, i, e: payload))
    path = ec.EnergyClippingPlot()(2026, "Test", "Q", driver="AAA", fmt="portrait")
    assert path.endswith("Energy clipping AAA_portrait.png")
