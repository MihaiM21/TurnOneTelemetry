"""Unit tests for :mod:`src.services.circuit_derivation`.

Pure-geometry tests run against a synthetic rounded-rectangle "lap" (see
``rounded_rect`` below) that stands in for one lap of ``Position.z`` samples.
The orchestrator tests (``derive_circuit_layout`` / ``derive_and_store`` /
``ensure_circuit_layout``) run offline against a ``_FakeStore`` and
monkeypatched data-access functions -- no network, Redis, or Mongo.
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.core.exceptions import DataNotAvailableError
from src.domain.models.circuits import CircuitLayout, Point, TrackMarker, TrackOutline
from src.services import circuit_derivation as cdmod
from src.services.analysis.v2.telemetry_track_map import _rotate_xy as ttm_rotate_xy

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
SESSION_INFO_SAMPLE = FIXTURES_DIR / "session_info_sample.json"


# ---------------------------------------------------------------------------
# Synthetic lap generators
# ---------------------------------------------------------------------------

def rounded_rect(L=1000, W=500, r=60, spacing_m=12, noise_m=0.05, seed=0):
    """A rounded rectangle 1000 x 500 m (r=60 corners) as raw X/Y/T samples.

    Starts at (0, 0) mid-bottom-straight heading +x, closed (last sample ends
    just before the start). Perimeter is approx 2896 m, 4 corners at
    s = 487/958/1935/2406 m -- this exact generator's outputs are pinned by
    the assertions below.
    """
    rng = np.random.default_rng(seed)
    pts = []

    def line(p0, p1):
        n = max(2, int(math.hypot(p1[0] - p0[0], p1[1] - p0[1]) / spacing_m))
        for i in range(n):
            t = i / n
            pts.append((p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t))

    def arc(c, a0, a1):
        n = max(4, int(abs(a1 - a0) * r / spacing_m))
        for i in range(n):
            a = a0 + (a1 - a0) * i / n
            pts.append((c[0] + r * math.cos(a), c[1] + r * math.sin(a)))

    hx = L / 2
    line((0, 0), (hx - r, 0))
    arc((hx - r, r), -math.pi / 2, 0)
    line((hx, r), (hx, W - r))
    arc((hx - r, W - r), 0, math.pi / 2)
    line((hx - r, W), (-hx + r, W))
    arc((-hx + r, W - r), math.pi / 2, math.pi)
    line((-hx, W - r), (-hx, r))
    arc((-hx + r, r), math.pi, 3 * math.pi / 2)
    line((-hx + r, 0), (0, 0))
    p = np.array(pts) * 10.0 + rng.normal(0, noise_m * 10, (len(pts), 2))
    t = np.arange(len(p)) * 0.2
    return p[:, 0], p[:, 1], t


def _turtle_path(steps, spacing_m=12.0, start=(0.0, 0.0), start_heading_deg=0.0):
    """Build a polyline from ``('line', length)`` / ``('arc', turn_deg, radius)`` steps.

    A tiny "turtle graphics" builder used only to construct the chicane test
    track below: straights and constant-curvature arcs (positive turn = left
    / CCW) chained from a current position and heading.
    """
    pts = [start]
    x, y = start
    heading = math.radians(start_heading_deg)
    for step in steps:
        if step[0] == "line":
            length = step[1]
            n = max(2, int(length / spacing_m))
            for i in range(1, n + 1):
                dist = length * i / n
                pts.append((x + dist * math.cos(heading), y + dist * math.sin(heading)))
            x, y = pts[-1]
        else:
            _, turn_deg, radius = step
            sign = 1.0 if turn_deg >= 0 else -1.0
            cx = x - sign * radius * math.sin(heading)
            cy = y + sign * radius * math.cos(heading)
            a0 = math.atan2(y - cy, x - cx)
            d_a = math.radians(turn_deg)
            n = max(4, int(abs(d_a) * radius / spacing_m))
            for i in range(1, n + 1):
                a = a0 + d_a * i / n
                pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
            heading += d_a
            x, y = pts[-1]
    return pts, heading, (x, y)


def chicane_loop(spacing_m=12.0, seed=0):
    """A closed loop: two big (unflagged) 180-deg turns joined by two straights,
    one of which carries a right-left 45-deg chicane ~50 m apart (apex to apex).

    The two big-radius turns use slightly different radii (R1 vs R2) so the
    loop closes almost exactly despite the chicane's lateral offset on only
    one straight -- an exact closure keeps the wraparound seam from creating
    a spurious extra "corner" where detect_corners_from_geometry treats the
    array as a ring.
    """
    outbound = [
        ("line", 100.0), ("arc", 45.0, 20.0), ("line", 34.3), ("arc", -45.0, 20.0), ("line", 100.0),
    ]
    _, _, (x1, y1) = _turtle_path(outbound, spacing_m=spacing_m)
    r1 = 500.0
    r2 = r1 + y1 / 2.0
    steps = outbound + [("arc", 180.0, r1), ("line", x1), ("arc", 180.0, r2)]
    pts, _, _ = _turtle_path(steps, spacing_m=spacing_m)
    p = np.array(pts) * 10.0
    rng = np.random.default_rng(seed)
    p = p + rng.normal(0, 0.5, p.shape)
    t = np.arange(len(p)) * 0.2
    return p[:, 0], p[:, 1], t


# ---------------------------------------------------------------------------
# 1. build_outline
# ---------------------------------------------------------------------------

def test_build_outline_shape_and_closure():
    x, y, t = rounded_rect()
    o = cdmod.build_outline(x, y, t)

    assert len(o.x) == 750
    assert abs(o.length_raw / 10 - 2896) < 30
    assert o.closure_gap_raw / 10 < 20
    assert abs(o.x[0] - x[0]) < 15
    assert abs(o.y[0] - y[0]) < 15
    assert o.raw_t is not None
    assert o.s[0] == 0
    assert np.all(np.diff(o.s) > 0)


# ---------------------------------------------------------------------------
# 2. detect_corners_from_geometry
# ---------------------------------------------------------------------------

def test_detect_corners_from_geometry_four_corners():
    x, y, t = rounded_rect()
    o = cdmod.build_outline(x, y, t)
    corners = cdmod.detect_corners_from_geometry(o.x, o.y, o.s)

    assert len(corners) == 4
    assert [c.s for c in corners] == sorted(c.s for c in corners)

    expected_s_m = (487, 958, 1935, 2406)
    for c, expected in zip(corners, expected_s_m):
        assert abs(c.s / 10 - expected) < 15
        assert 80 <= c.turn_deg <= 95
        assert c.direction == 1

    assert abs(corners[0].heading_deg - 45) < 10


# ---------------------------------------------------------------------------
# 3. min_turn_deg threshold plumbing
# ---------------------------------------------------------------------------

def test_detect_corners_min_turn_deg_threshold_plumbing():
    """Not a real kink track -- just confirms min_turn_deg is actually wired
    through to the merged-run filter (a lower threshold never loses a corner
    that a higher one keeps)."""
    x, y, t = rounded_rect()
    o = cdmod.build_outline(x, y, t)

    loose = cdmod.detect_corners_from_geometry(o.x, o.y, o.s, min_turn_deg=5)
    strict = cdmod.detect_corners_from_geometry(o.x, o.y, o.s, min_turn_deg=25)

    assert len(loose) >= 4
    assert len(strict) == 4


# ---------------------------------------------------------------------------
# 4. Right-left chicane
# ---------------------------------------------------------------------------

def test_detect_corners_chicane_opposite_direction_not_merged():
    x, y, t = chicane_loop()
    o = cdmod.build_outline(
        x, y, t, min_lap_m=500, max_lap_m=5000, closure_tol_m=100, min_samples=50,
    )
    corners = cdmod.detect_corners_from_geometry(o.x, o.y, o.s, min_separation_m=60)

    assert len(corners) == 2
    assert {c.direction for c in corners} == {1, -1}
    # Apex-to-apex separation is ~50m (well under min_separation_m=60), and
    # they are NOT merged because merging only ever applies within the same
    # direction -- opposite-sign corners are never candidates for merging.
    apex_gap_m = abs(corners[1].s - corners[0].s) / 10
    assert apex_gap_m < 60


# ---------------------------------------------------------------------------
# 5. snap_corners_to_speed_minima
# ---------------------------------------------------------------------------

def test_snap_corners_to_speed_minima_moves_apex_to_dip():
    x, y, t = rounded_rect()
    o = cdmod.build_outline(x, y, t)
    corners = cdmod.detect_corners_from_geometry(o.x, o.y, o.s)

    speed_s = o.raw_s
    length = o.length_raw
    speed = np.full_like(speed_s, 300.0)
    for c in corners:
        d = (speed_s - (c.s + 200)) % length
        d = np.minimum(d, length - d)
        speed = np.minimum(speed, 300 - 150 * np.exp(-(d / 300) ** 2))

    snapped_corners, snapped = cdmod.snap_corners_to_speed_minima(corners, o, speed_s, speed)
    assert snapped == 4
    for old_c, new_c in zip(corners, snapped_corners):
        delta = new_c.s - old_c.s
        assert 0 <= delta <= 250
        assert new_c.min_speed_kmh is not None
        assert new_c.min_speed_kmh < 200


def test_snap_corners_to_speed_minima_flat_speed_no_snap():
    x, y, t = rounded_rect()
    o = cdmod.build_outline(x, y, t)
    corners = cdmod.detect_corners_from_geometry(o.x, o.y, o.s)

    speed_s = o.raw_s
    flat_speed = np.full_like(speed_s, 300.0)

    result, snapped = cdmod.snap_corners_to_speed_minima(corners, o, speed_s, flat_speed)
    assert snapped == 0
    for old_c, new_c in zip(corners, result):
        assert new_c.s == old_c.s


# ---------------------------------------------------------------------------
# 6. auto_rotation
# ---------------------------------------------------------------------------

def test_auto_rotation_plus_x_start_is_zero():
    x, y, t = rounded_rect()
    o = cdmod.build_outline(x, y, t)
    assert cdmod.auto_rotation(o) == 0.0


def test_auto_rotation_rotated_start_is_270():
    x, y, t = rounded_rect()
    o = cdmod.build_outline(-y, x, t)
    assert cdmod.auto_rotation(o) == 270.0


def test_auto_rotation_lays_pit_straight_left_to_right():
    x0, y0, t = rounded_rect()
    rng = np.random.default_rng(42)
    for _ in range(12):
        angle_deg = rng.uniform(0, 360)
        theta = math.radians(angle_deg)
        xr = x0 * math.cos(theta) - y0 * math.sin(theta)
        yr = x0 * math.sin(theta) + y0 * math.cos(theta)
        o = cdmod.build_outline(xr, yr, t)
        rotation = cdmod.auto_rotation(o)

        xf, yf = cdmod._rotate_xy(o.x, o.y, rotation)
        rotated = replace(o, x=xf, y=yf)
        x_a, y_a = xf[0], yf[0]
        x_b, y_b = cdmod.point_at_s(rotated, 1200)

        dx, dy = x_b - x_a, y_b - y_a
        assert dx > 0
        assert abs(math.degrees(math.atan2(dy, dx))) < 3


def test_rotate_xy_matches_telemetry_track_map():
    rng = np.random.default_rng(7)
    x = rng.uniform(-1000, 1000, 200)
    y = rng.uniform(-1000, 1000, 200)
    for angle in (0.0, 37.5, 180.0, 359.9):
        a, b = cdmod._rotate_xy(x, y, angle)
        c, d = ttm_rotate_xy(x, y, angle)
        assert np.allclose(a, c)
        assert np.allclose(b, d)


# ---------------------------------------------------------------------------
# 7. build_outline error cases
# ---------------------------------------------------------------------------

def test_build_outline_raises_too_few_samples():
    x, y, t = rounded_rect()
    with pytest.raises(cdmod.CircuitDerivationError):
        cdmod.build_outline(x[:30], y[:30], t[:30])


def test_build_outline_raises_lap_too_short():
    # Dense enough sampling to clear min_samples while staying well under
    # the 2500m lower bound on lap length.
    x, y, t = rounded_rect(L=300, W=150, r=30, spacing_m=3)
    with pytest.raises(cdmod.CircuitDerivationError):
        cdmod.build_outline(x, y, t)


def test_build_outline_raises_open_arc():
    x, y, t = rounded_rect()
    n_open = int(len(x) * 0.6)
    with pytest.raises(cdmod.CircuitDerivationError):
        cdmod.build_outline(x[:n_open], y[:n_open], t[:n_open])


# ---------------------------------------------------------------------------
# 8. build_preview_geometry
# ---------------------------------------------------------------------------

def _layout_from_synthetic(outline, corners, **overrides):
    markers = [
        TrackMarker(
            number=i + 1, angle=round(c.heading_deg, 3), length=round(c.s, 3),
            position=Point(x=round(c.x, 3), y=round(c.y, 3)),
        )
        for i, c in enumerate(corners)
    ]
    kwargs = dict(
        circuit_id="999", year=2025, name="Test", country="Testland", country_code="TST",
        location="Test City", rotation=0.0,
        track_outline=TrackOutline(x=list(outline.x), y=list(outline.y)),
        corners=markers, marshal_lights=[], marshal_sectors=[], source="telemetry",
    )
    kwargs.update(overrides)
    return CircuitLayout(**kwargs)


def test_build_preview_geometry_shape_and_y_flip():
    x, y, t = rounded_rect()
    o = cdmod.build_outline(x, y, t)
    corners = cdmod.detect_corners_from_geometry(o.x, o.y, o.s)
    layout = _layout_from_synthetic(o, corners)

    geo = cdmod.build_preview_geometry(layout)
    pts = geo["polyline_points"].split(" ")
    assert len(pts) == 750
    assert len(geo["corners"]) == len(corners)
    assert len(geo["viewbox"].split(" ")) == 4

    svg_ys = [float(p.split(",")[1]) for p in pts]
    xr, yr = cdmod._rotate_xy(
        np.asarray(layout.track_outline.x), np.asarray(layout.track_outline.y), layout.rotation,
    )
    idx_max_rawy = int(np.argmax(yr))
    # The point with max raw y (after rotation) must land at the MIN svg y.
    assert svg_ys[idx_max_rawy] == pytest.approx(min(svg_ys))


# ---------------------------------------------------------------------------
# 9. point_at_s periodicity
# ---------------------------------------------------------------------------

def test_point_at_s_is_periodic():
    x, y, t = rounded_rect()
    o = cdmod.build_outline(x, y, t)
    p0 = cdmod.point_at_s(o, 0)
    p_end = cdmod.point_at_s(o, o.length_raw)
    assert p0 == pytest.approx(p_end, abs=1e-6)


# ---------------------------------------------------------------------------
# Orchestrator: derive_circuit_layout / derive_and_store / ensure_circuit_layout
# ---------------------------------------------------------------------------

class _FakeStore:
    def __init__(self, year=2025, identifier="Australian Grand Prix", session_name="Q"):
        self.year = year
        self.identifier = identifier
        self.session_name = session_name
        self.event_name = "Australian Grand Prix"
        self.round_nr = 1
        self.base_url = "https://x/"
        self.client = object()

    def session_info(self):
        with open(SESSION_INFO_SAMPLE, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def driver_list(self):
        return {"63": {"tla": "RUS"}, "1": {"tla": "VER"}}


@pytest.fixture
def fake_store():
    return _FakeStore()


@pytest.fixture
def synthetic_lap():
    return rounded_rect()


@pytest.fixture
def synthetic_speed(synthetic_lap):
    x, y, t = synthetic_lap
    o = cdmod.build_outline(x, y, t)
    corners = cdmod.detect_corners_from_geometry(o.x, o.y, o.s)
    speed_s = o.raw_s
    length = o.length_raw
    speed = np.full_like(speed_s, 300.0)
    for c in corners:
        d = (speed_s - (c.s + 200)) % length
        d = np.minimum(d, length - d)
        speed = np.minimum(speed, 300 - 150 * np.exp(-(d / 300) ** 2))
    return t, speed


@pytest.fixture
def patch_derivation_sources(monkeypatch, synthetic_lap, synthetic_speed):
    """Wire fastest-lap/position/telemetry lookups to the synthetic lap.

    Driver "1"/VER gets a bogus, very fast (20s) lap that only has 10 position
    samples (too short to become an outline); driver "63"/RUS gets the real
    80s lap with the full synthetic trace, so the orchestrator must reject the
    faster candidate and fall back to the slower one that actually closes.
    """
    x, y, t = synthetic_lap
    tel_t, speed = synthetic_speed

    def fake_get_fastest_lap_windows(base_url, client, target_driver_num=None, store=None):
        rows = [
            {"DriverNum": "1", "StartTime": 100.0, "EndTime": 120.0, "LapTime": 20.0},
            {"DriverNum": "63", "StartTime": 200.0, "EndTime": 280.0, "LapTime": 80.0},
        ]
        df = pd.DataFrame(rows)
        if target_driver_num is not None:
            df = df[df["DriverNum"] == str(target_driver_num)]
        return df.reset_index(drop=True)

    def fake_extract_position_for_lap(base_url, client, driver_num, start, end, store=None):
        if str(driver_num) == "63":
            return pd.DataFrame({"Time": t, "X": x, "Y": y, "Z": 0.0})
        return pd.DataFrame({"Time": t[:10], "X": x[:10], "Y": y[:10], "Z": 0.0})

    def fake_extract_telemetry_for_lap(
        base_url, client, driver_num, start, end, channels=None, store=None,
    ):
        return pd.DataFrame({"Time": tel_t, "Speed": speed})

    def fake_get_season_events(year):
        return []

    monkeypatch.setattr(cdmod, "get_fastest_lap_windows", fake_get_fastest_lap_windows)
    monkeypatch.setattr(cdmod, "extract_position_for_lap", fake_extract_position_for_lap)
    monkeypatch.setattr(cdmod, "extract_telemetry_for_lap", fake_extract_telemetry_for_lap)
    monkeypatch.setattr(cdmod, "get_season_events", fake_get_season_events)


def test_derive_circuit_layout_picks_slower_but_valid_candidate(fake_store, patch_derivation_sources):
    derived = cdmod.derive_circuit_layout(fake_store)

    assert derived.stats["candidates_tried"] == 2
    assert len(derived.stats["rejected"]) == 1
    assert "1" in derived.stats["rejected"][0] or "VER" in derived.stats["rejected"][0]

    layout = derived.layout
    assert layout.circuit_id == "10"
    assert layout.name == "Melbourne"
    assert layout.country_code == "AUS"
    assert layout.source == "telemetry"
    assert layout.source_driver == "RUS"
    assert layout.source_lap_time_s == 80.0
    assert layout.source_session == "2025/Australian Grand Prix/Q"
    assert layout.marshal_lights == []
    assert layout.rotation == 0.0

    lengths = [c.length for c in layout.corners]
    numbers = [c.number for c in layout.corners]
    assert lengths == sorted(lengths)
    assert len(set(lengths)) == len(lengths)
    assert numbers == list(range(1, len(numbers) + 1))

    summary = derived.summary
    assert summary.circuit_key == 10
    assert summary.years_available == [2025]


def test_derive_circuit_layout_with_driver_option_targets_only_that_driver(
    fake_store, patch_derivation_sources,
):
    derived = cdmod.derive_circuit_layout(fake_store, cdmod.DeriveOptions(driver="RUS"))
    assert derived.stats["candidates_tried"] == 1
    assert derived.stats["rejected"] == []
    assert derived.layout.source_driver == "RUS"


def test_derive_circuit_layout_unknown_driver_raises(fake_store, patch_derivation_sources):
    with pytest.raises(DataNotAvailableError):
        cdmod.derive_circuit_layout(fake_store, cdmod.DeriveOptions(driver="XXX"))


def test_derive_circuit_layout_manual_rotation_disables_auto(fake_store, patch_derivation_sources):
    derived = cdmod.derive_circuit_layout(fake_store, cdmod.DeriveOptions(rotation=90))
    assert derived.layout.rotation == 90.0
    assert derived.stats["auto_rotation"] is False


def test_derive_circuit_layout_all_candidates_fail_raises(monkeypatch, fake_store, synthetic_lap):
    x, y, t = synthetic_lap

    def fake_get_fastest_lap_windows(base_url, client, target_driver_num=None, store=None):
        rows = [
            {"DriverNum": "1", "StartTime": 100.0, "EndTime": 120.0, "LapTime": 20.0},
            {"DriverNum": "63", "StartTime": 200.0, "EndTime": 280.0, "LapTime": 80.0},
        ]
        df = pd.DataFrame(rows)
        if target_driver_num is not None:
            df = df[df["DriverNum"] == str(target_driver_num)]
        return df.reset_index(drop=True)

    def always_short_position(base_url, client, driver_num, start, end, store=None):
        return pd.DataFrame({"Time": t[:10], "X": x[:10], "Y": y[:10], "Z": 0.0})

    monkeypatch.setattr(cdmod, "get_fastest_lap_windows", fake_get_fastest_lap_windows)
    monkeypatch.setattr(cdmod, "extract_position_for_lap", always_short_position)
    monkeypatch.setattr(cdmod, "get_season_events", lambda year: [])

    with pytest.raises(cdmod.CircuitDerivationError, match="no candidate lap"):
        cdmod.derive_circuit_layout(fake_store)


# ---------------------------------------------------------------------------
# derive_and_store / ensure_circuit_layout
# ---------------------------------------------------------------------------

@pytest.fixture
def patch_store_writer(monkeypatch):
    """Monkeypatch the circuits_store calls derive_and_store makes, and
    return a dict of call trackers the test can inspect."""
    calls = {"save": 0}

    def fake_save_circuit_layout(layout, summary):
        calls["save"] += 1
        return Path("fake/path.json")

    monkeypatch.setattr(cdmod.circuits_store, "save_circuit_layout", fake_save_circuit_layout)
    return calls


def test_derive_and_store_existing_not_overwrite_raises(
    monkeypatch, fake_store, patch_derivation_sources, patch_store_writer,
):
    monkeypatch.setattr(cdmod.circuits_store, "find_circuit_file", lambda year, cid: Path("existing.json"))
    monkeypatch.setattr(cdmod.circuits_store, "read_source", lambda path: "multiviewer")

    with pytest.raises(cdmod.LayoutExistsError):
        cdmod.derive_and_store(2025, "Australian Grand Prix", "Q", store=fake_store)
    assert patch_store_writer["save"] == 0


def test_derive_and_store_existing_dry_run_previews_without_writing(
    monkeypatch, fake_store, patch_derivation_sources, patch_store_writer,
):
    monkeypatch.setattr(cdmod.circuits_store, "find_circuit_file", lambda year, cid: Path("existing.json"))
    monkeypatch.setattr(cdmod.circuits_store, "read_source", lambda path: "multiviewer")

    result = cdmod.derive_and_store(2025, "Australian Grand Prix", "Q", store=fake_store, dry_run=True)
    assert result["written"] is False
    assert result["existing_source"] == "multiviewer"
    assert patch_store_writer["save"] == 0


def test_derive_and_store_existing_overwrite_saves(
    monkeypatch, fake_store, patch_derivation_sources, patch_store_writer,
):
    monkeypatch.setattr(cdmod.circuits_store, "find_circuit_file", lambda year, cid: Path("existing.json"))
    monkeypatch.setattr(cdmod.circuits_store, "read_source", lambda path: "multiviewer")

    result = cdmod.derive_and_store(2025, "Australian Grand Prix", "Q", store=fake_store, overwrite=True)
    assert result["written"] is True
    assert patch_store_writer["save"] == 1


def test_derive_and_store_not_existing_saves(
    monkeypatch, fake_store, patch_derivation_sources, patch_store_writer,
):
    monkeypatch.setattr(cdmod.circuits_store, "find_circuit_file", lambda year, cid: None)

    result = cdmod.derive_and_store(2025, "Australian Grand Prix", "Q", store=fake_store)
    assert patch_store_writer["save"] == 1
    assert result["written"] is True
    assert "layout" in result


def test_ensure_circuit_layout_returns_none_when_exists(
    monkeypatch, fake_store, patch_derivation_sources, patch_store_writer,
):
    monkeypatch.setattr(cdmod.circuits_store, "find_circuit_file", lambda year, cid: Path("existing.json"))
    monkeypatch.setattr(cdmod.circuits_store, "read_source", lambda path: "multiviewer")

    assert cdmod.ensure_circuit_layout(2025, "Australian Grand Prix", "Q", store=fake_store) is None


def test_ensure_circuit_layout_returns_none_on_runtime_error(monkeypatch, fake_store):
    def raise_runtime(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cdmod, "derive_and_store", raise_runtime)
    assert cdmod.ensure_circuit_layout(2025, "Australian Grand Prix", "Q", store=fake_store) is None


def test_ensure_circuit_layout_returns_dict_on_success(
    monkeypatch, fake_store, patch_derivation_sources, patch_store_writer,
):
    monkeypatch.setattr(cdmod.circuits_store, "find_circuit_file", lambda year, cid: None)

    result = cdmod.ensure_circuit_layout(2025, "Australian Grand Prix", "Q", store=fake_store)
    assert isinstance(result, dict)
    assert result["written"] is True
