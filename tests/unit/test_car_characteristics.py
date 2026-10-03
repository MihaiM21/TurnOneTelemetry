"""Offline tests for the car-characteristics charts (corner speed profile, efficiency scatter).

Synthetic teams run the same circular circuit with three speed dips of different
depths (slow, medium, fast), a flat-out kink and a two-piece chicane. The pure
computation (:func:`compute_profile` / :func:`compute_efficiency`) is exercised
without a store; the renderers write real PNGs for every canvas format.
"""
from __future__ import annotations

import types

import numpy as np
import pytest

from src.services.analysis.v2 import _car_characteristics_render as render_mod
from src.services.analysis.v2 import _lap_duel_core as core
from src.services.analysis.v2 import car_characteristics as cc
from src.services.plotting import canvas
from tests.unit._lap_duel_synth import dipped_speed, make_car, make_circle_pos

START = 1000.0
RADIUS_M = 900.0

# (centre fraction, minimum km/h) on the base team; corners 1/2/3 are slow/medium/fast,
# 4 is a flat-out kink, 5+6 are the two pieces of one chicane.
DIPS = ((0.15, 90.0), (0.40, 150.0), (0.55, 240.0), (0.70, 290.0), (0.85, 100.0))


def _circuit_corners():
    def at(number, frac):
        theta = 2 * np.pi * frac
        return {"number": number, "x": RADIUS_M * 10 * np.cos(theta), "y": RADIUS_M * 10 * np.sin(theta),
                "distance_m": None}
    return [at(1, 0.15), at(2, 0.40), at(3, 0.55), at(4, 0.70), at(5, 0.850), at(6, 0.857)]


def _lap(tla, team, color, lap_time, *, base=310.0, dip_shift=(0, 0, 0, 0, 0), number=None):
    dips = tuple((c, v + s) for (c, v), s in zip(DIPS, dip_shift))
    speed_fn = dipped_speed(base=base, dips=dips, width=0.02)
    car = make_car(START, lap_time, speed_fn)
    pos = make_circle_pos(START, lap_time, RADIUS_M)
    return {"tla": tla, "team": team, "color": color, "lap_time": lap_time,
            "trace": core.build_lap_trace(car, pos, START, lap_time)}


@pytest.fixture()
def field():
    """Four teams; ALO is the slower Aston driver (higher apex speeds, slower lap)."""
    return {
        "1": _lap("VER", "Red Bull Racing", "#3671C6", 90.0, base=318.0, dip_shift=(-6, -4, 0, 0, -6)),
        "16": _lap("LEC", "Ferrari", "#E80020", 90.4, base=312.0, dip_shift=(4, 2, 4, 0, 4)),
        "14": _lap("ALO", "Aston Martin", "#229971", 91.9, base=300.0, dip_shift=(30, 30, 30, 0, 30)),
        "18": _lap("STR", "Aston Martin", "#229971", 91.2, base=304.0, dip_shift=(12, 10, 8, 0, 12)),
        "4": _lap("NOR", "McLaren", "#FF8000", 90.2, base=308.0, dip_shift=(10, 6, 12, 0, 10)),
    }


@pytest.fixture()
def profile(field):
    return cc.compute_profile(field, _circuit_corners(), 2026, "Test Grand Prix", "Q")


@pytest.fixture()
def efficiency(field):
    return cc.compute_efficiency(field, _circuit_corners(), 2026, "Test Grand Prix", "Q")


@pytest.fixture()
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ----------------------------------------------------------------------
# Classification
# ----------------------------------------------------------------------
@pytest.mark.parametrize("kmh, expected", [
    (60.0, "slow"), (119.9, "slow"), (120.0, "medium"), (200.0, "medium"), (200.1, "fast"),
    (280.0, "fast"), (280.1, None), (320.0, None),
])
def test_class_boundaries(kmh, expected):
    assert cc.classify(kmh) == expected


def test_corners_are_classified_by_field_median(profile):
    by_class = {c: profile["classes"][c]["corners"] for c in cc.CLASSES}
    assert by_class["slow"] == [1] or 1 in by_class["slow"]
    assert 2 in by_class["medium"]
    assert 3 in by_class["fast"]
    listed = {c["number"]: c for c in profile["corners"]}
    assert listed[1]["class"] == "slow" and listed[2]["class"] == "medium" and listed[3]["class"] == "fast"
    assert listed[1]["median_apex_kmh"] < 120 < listed[2]["median_apex_kmh"] < 200 < listed[3]["median_apex_kmh"]


def test_flat_out_kink_is_skipped(profile):
    assert 4 not in {c["number"] for c in profile["corners"]}
    assert 4 in profile["rules"]["skipped_corners"]


def test_chicane_pieces_merge_into_one_corner(profile):
    numbers = {c["number"] for c in profile["corners"]}
    assert len(numbers & {5, 6}) == 1
    kept = next(c for c in profile["corners"] if c["number"] in (5, 6))
    assert set(kept["merged"]) == ({5, 6} - {kept["number"]})
    assert kept["class"] == "slow"


def test_merge_keeps_the_slower_piece():
    pieces = [{"number": 7, "distance_m": 1000.0, "median_apex_kmh": 110.0, "class": "slow"},
              {"number": 8, "distance_m": 1040.0, "median_apex_kmh": 85.0, "class": "slow"},
              {"number": 9, "distance_m": 1500.0, "median_apex_kmh": 90.0, "class": "slow"}]
    out = cc._merge_chicanes(pieces)
    assert [c["number"] for c in out] == [8, 9]
    assert out[0]["merged"] == [7]


# ----------------------------------------------------------------------
# Profile payload
# ----------------------------------------------------------------------
def test_profile_top_level_shape(profile):
    assert set(profile) == {"classes", "corners", "highlights", "rules", "reference", "session_info"}
    assert set(profile["classes"]) == set(cc.CLASSES)
    assert profile["session_info"] == {"year": 2026, "event_name": "Test Grand Prix", "session_name": "Q"}
    row = profile["classes"]["slow"]["teams"][0]
    assert set(row) == {"team", "short", "driver", "color", "avg_kmh", "delta_kmh"}


def test_one_row_per_team_and_the_faster_driver_is_used(profile):
    rows = profile["classes"]["slow"]["teams"]
    assert len(rows) == 4                                        # 5 drivers, 4 teams
    aston = next(r for r in rows if r["team"] == "Aston Martin")
    assert aston["driver"] == "STR"                              # 91.2 beats ALO's 91.9


def test_teams_sorted_fastest_first_with_deltas_to_the_class_best(profile):
    for cls in cc.CLASSES:
        rows = profile["classes"][cls]["teams"]
        avgs = [r["avg_kmh"] for r in rows]
        assert avgs == sorted(avgs, reverse=True)
        assert rows[0]["delta_kmh"] == 0.0
        for r in rows:
            assert r["delta_kmh"] == pytest.approx(r["avg_kmh"] - avgs[0], abs=0.051)
            assert r["delta_kmh"] <= 0.0


def test_slow_corner_ordering_follows_the_dip_depths(profile):
    order = [r["driver"] for r in profile["classes"]["slow"]["teams"]]
    assert order[0] == "STR"                                     # +12 on the slow corners
    assert order[-1] == "VER"                                    # -6


def test_highlights(profile):
    hi = profile["highlights"]
    assert set(hi["best_per_class"]) == {"slow", "medium", "fast"}
    spread = hi["biggest_spread"]
    rows = profile["classes"][spread["class"]]["teams"]
    assert spread["spread_kmh"] == pytest.approx(rows[0]["avg_kmh"] - rows[-1]["avg_kmh"], abs=0.051)
    assert spread["best_team"] == rows[0]["team"] and spread["worst_team"] == rows[-1]["team"]


def test_class_average_is_the_mean_of_its_corner_apexes(field):
    a = cc.analyse(field, _circuit_corners())
    payload = cc.compute_profile(field, _circuit_corners())
    slow_idx = [i for i, c in enumerate(a["corners"]) if c["class"] == "slow"]
    team = next(t for t in a["teams"] if t["driver"] == "STR")
    expected = np.mean([team["apex"][i] for i in slow_idx])
    got = next(r for r in payload["classes"]["slow"]["teams"] if r["driver"] == "STR")
    assert got["avg_kmh"] == pytest.approx(expected, abs=0.051)


# ----------------------------------------------------------------------
# Efficiency payload
# ----------------------------------------------------------------------
def test_efficiency_shape(efficiency):
    assert set(efficiency) == {"teams", "field_median", "highlights", "corners", "reference", "session_info"}
    assert len(efficiency["teams"]) == 4
    assert set(efficiency["teams"][0]) == {"team", "short", "driver", "color", "top_speed_kmh", "avg_apex_kmh",
                                           "lap_time_s"}
    assert set(efficiency["field_median"]) == {"x", "y"}


def test_top_speed_is_the_traces_maximum(field, efficiency):
    ver = next(t for t in efficiency["teams"] if t["driver"] == "VER")
    assert ver["top_speed_kmh"] == pytest.approx(float(field["1"]["trace"]["speed"].max()), abs=0.051)
    assert ver["top_speed_kmh"] > next(t for t in efficiency["teams"] if t["driver"] == "STR")["top_speed_kmh"]


def test_both_charts_share_one_corner_set(profile, efficiency):
    assert efficiency["corners"] == [c["number"] for c in profile["corners"]]


def test_avg_apex_matches_the_profile_class_averages(profile, efficiency):
    counts = {cls: len(profile["classes"][cls]["corners"]) for cls in cc.CLASSES}
    for t in efficiency["teams"]:
        weighted = sum(
            counts[cls] * next(r for r in profile["classes"][cls]["teams"] if r["driver"] == t["driver"])["avg_kmh"]
            for cls in cc.CLASSES if counts[cls]
        ) / sum(counts.values())
        assert t["avg_apex_kmh"] == pytest.approx(weighted, abs=0.15)


def test_field_median_and_highlights(efficiency):
    xs = [t["top_speed_kmh"] for t in efficiency["teams"]]
    ys = [t["avg_apex_kmh"] for t in efficiency["teams"]]
    assert efficiency["field_median"]["x"] == pytest.approx(float(np.median(xs)), abs=0.051)
    assert efficiency["field_median"]["y"] == pytest.approx(float(np.median(ys)), abs=0.051)
    hi = efficiency["highlights"]
    assert hi["top_speed"]["team"] == "Red Bull Racing"
    assert hi["apex"]["team"] == "Aston Martin"
    assert hi["most_efficient"]["team"] in {t["team"] for t in efficiency["teams"]}


def test_short_team_names():
    assert cc.short_team_name("Red Bull Racing") == "Red Bull"
    assert cc.short_team_name("Haas F1 Team") == "Haas"
    assert cc.short_team_name("Aston Martin") == "Aston Martin"
    assert cc.short_team_name("Racing Bulls") == "RB"
    assert cc.short_team_name(None) == "?"


def test_no_circuit_corners_falls_back_to_detection(field):
    payload = cc.compute_profile(field, None)
    assert payload["corners"]                                   # speed-minimum detection found the dips


def test_empty_field_raises_value_error():
    with pytest.raises(ValueError):
        cc.compute_profile({}, None)


# ----------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------
@pytest.fixture()
def patched_fetch(monkeypatch, field):
    seen = {}

    def fake_cached(**kw):
        seen["data_type"] = kw["data_type"]
        return kw["generator"]()

    monkeypatch.setattr(cc, "cached_or_generate", fake_cached)
    monkeypatch.setattr(cc, "build_session_store", lambda *a, **k: types.SimpleNamespace(event_name="Test Grand Prix"))
    monkeypatch.setattr(cc, "load_field_laps", lambda store: field)
    monkeypatch.setattr(cc, "get_circuit_info_for_session", lambda store: {"corners": _circuit_corners()})
    return seen


def test_data_classes_use_their_data_type_keys(patched_fetch):
    p = cc.CornerSpeedProfileData()(2026, "Test Grand Prix", "Q")
    assert patched_fetch["data_type"] == "corner_speed_profile" and "classes" in p
    e = cc.EfficiencyScatterData()(2026, "Test Grand Prix", "Q")
    assert patched_fetch["data_type"] == "efficiency_scatter" and "field_median" in e
    assert p["session_info"]["event_name"] == "Test Grand Prix"


def test_data_class_raises_not_available_without_telemetry(monkeypatch, patched_fetch):
    from src.core.exceptions import DataNotAvailableError
    monkeypatch.setattr(cc, "load_field_laps", lambda store: {})
    with pytest.raises(DataNotAvailableError):
        cc.CornerSpeedProfileData()(2026, "Test Grand Prix", "Q")


@pytest.mark.parametrize("plot_cls", [cc.CornerSpeedProfilePlot, cc.EfficiencyScatterPlot])
def test_bad_format_fails_before_any_fetch(monkeypatch, plot_cls):
    def boom(*a, **k):
        raise AssertionError("fetched before validating fmt")

    monkeypatch.setattr(cc, "cached_or_generate", boom)
    with pytest.raises(ValueError):
        plot_cls()(2026, "Test Grand Prix", "Q", fmt="cinema")


@pytest.mark.parametrize("plot_cls, name", [(cc.CornerSpeedProfilePlot, "Corner speed profile"),
                                            (cc.EfficiencyScatterPlot, "Efficiency scatter")])
def test_plot_class_end_to_end_default_is_landscape(patched_fetch, in_tmp, plot_cls, name):
    from PIL import Image

    path = plot_cls()(2026, "Test Grand Prix", "Q")
    assert path.endswith(f"/2026/TestGrandPrix/Q/{name}_landscape.png")
    with Image.open(in_tmp / path) as img:
        assert img.size == (1920, 1080)


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------
@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_profile_renders_at_the_formats_exact_size(profile, in_tmp, fmt_name):
    from PIL import Image

    path = render_mod.render_profile(profile, fmt_name)
    assert path.endswith(f"Corner speed profile{canvas.format_suffix(fmt_name)}.png")
    fmt = canvas.get_format(fmt_name)
    with Image.open(in_tmp / path) as img:
        assert img.size == (fmt.width_px, fmt.height_px)


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_efficiency_renders_at_the_formats_exact_size(efficiency, in_tmp, fmt_name):
    from PIL import Image

    path = render_mod.render_efficiency(efficiency, fmt_name)
    assert path.endswith(f"Efficiency scatter{canvas.format_suffix(fmt_name)}.png")
    fmt = canvas.get_format(fmt_name)
    with Image.open(in_tmp / path) as img:
        assert img.size == (fmt.width_px, fmt.height_px)


def test_profile_renders_when_a_class_has_no_corners(profile, in_tmp):
    from PIL import Image

    trimmed = {**profile, "classes": {**profile["classes"], "fast": {"corners": [], "teams": []}}}
    for fmt_name in ("landscape", "story"):
        path = render_mod.render_profile(trimmed, fmt_name)
        with Image.open(in_tmp / path) as img:
            assert img.size == (canvas.get_format(fmt_name).width_px, canvas.get_format(fmt_name).height_px)


def test_render_rejects_unknown_format(profile, efficiency, in_tmp):
    with pytest.raises(ValueError):
        render_mod.render_profile(profile, "cinema")
    with pytest.raises(ValueError):
        render_mod.render_efficiency(efficiency, "cinema")


def test_label_placement_avoids_overlap():
    pts = np.array([[100.0, 100.0], [110.0, 104.0], [104.0, 96.0], [400.0, 300.0]])
    sizes = [(60.0, 20.0)] * 4
    picks = render_mod._place_labels(None, pts, sizes, 10.0, (0.0, 0.0, 600.0, 400.0))
    boxes = []
    for (px, py), (w, h), (dx, dy, ha, _) in zip(pts, sizes, picks):
        x0 = px + dx - (w if ha == "right" else (w / 2 if ha == "center" else 0.0))
        boxes.append((x0, py + dy, x0 + w, py + dy + h))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            assert render_mod._overlap(boxes[i], boxes[j]) == 0.0
