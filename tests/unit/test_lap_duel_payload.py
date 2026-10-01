"""Offline tests for the Lap Duel payload and renderer.

``lap_duel.build_payload`` is the seam between fetching and drawing, so two
synthetic sides are enough to exercise the whole thing: payload shape, colours,
cache keys, and a real render to PNG for every canvas format.
"""
from __future__ import annotations

import pytest

from src.services.analysis.v2 import _lap_duel_core as core
from src.services.analysis.v2 import _lap_duel_render as render_mod
from src.services.analysis.v2 import lap_duel
from src.services.analysis.v2.lap_duel import LapRef
from src.services.plotting import canvas
from tests.unit._lap_duel_synth import dipped_speed, make_car, make_circle_pos

START = 1000.0


def _side(driver, lap_time, *, team="Red Bull", color="#3671C6", number="1", speed_scale=1.0,
          year=2025, identifier="Bahrain", session="Q", lap=None, segment=None, lap_number=12):
    speed_fn = dipped_speed()
    car = make_car(START, lap_time, lambda f: speed_fn(f) * speed_scale)
    pos = make_circle_pos(START, lap_time, 477.5)
    return {
        "ref": LapRef(year, identifier, session, driver, lap, segment),
        "store": None,
        "trace": core.build_lap_trace(car, pos, START, lap_time),
        "lap_time": lap_time,
        "lap_number": lap_number,
        "team": team,
        "color": color,
        "racing_number": number,
    }


@pytest.fixture()
def sides():
    return (
        _side("VER", 90.512, team="Red Bull", color="#3671C6", number="1"),
        _side("NOR", 90.889, team="McLaren", color="#FF8000", number="4", speed_scale=0.99),
    )


@pytest.fixture()
def payload(sides):
    return lap_duel.build_payload(*sides, circuit_info=None)


SERIES = ("speed", "throttle", "brake", "gear", "rpm", "drs")
TOP_LEVEL = {"a", "b", "distance", "delta", "corners", "apexes", "sections", "track", "highlights", "detail",
             "same_session", "same_team", "session_info", "meta"}


def test_payload_top_level_keys(payload):
    assert set(payload) == TOP_LEVEL             # no accelerations at detail=standard
    assert payload["detail"] == "standard"
    assert payload["same_session"] is True
    assert payload["same_team"] is False
    assert payload["session_info"] == {"year": 2025, "event_name": "Bahrain", "session_name": "Q"}
    assert "positive means b is behind a" in payload["meta"]["delta_convention"]


def test_payload_series_lengths_and_ranges(payload):
    n = len(payload["distance"])
    assert n == core.GRID_POINTS
    assert len(payload["delta"]) == n
    for key in ("a", "b"):
        side = payload[key]
        for name in SERIES:
            assert len(side[name]) == n, name
        assert all(0.0 <= v <= 1.0 for v in side["throttle"])
        assert set(side["brake"]) <= {0, 1}
        assert max(side["speed"]) > min(side["speed"])
    assert payload["distance"][0] == 0.0
    assert payload["distance"] == sorted(payload["distance"])
    # The delta ends on the lap-time difference and B (slower) is behind.
    assert payload["delta"][0] == pytest.approx(0.0, abs=1e-6)
    assert payload["delta"][-1] == pytest.approx(90.889 - 90.512, abs=0.01)


def test_payload_side_metadata(payload):
    a, b = payload["a"], payload["b"]
    assert (a["driverCode"], b["driverCode"]) == ("VER", "NOR")
    assert (a["lapTime"], b["lapTime"]) == ("1:30.512", "1:30.889")
    assert a["lap_time_s"] == 90.512
    assert a["selection"] == "fastest" and a["segment"] is None
    assert a["racingNumber"] == "1" and a["team"] == "Red Bull"
    assert a["year"] == 2025 and a["session"] == "Q"
    assert a["length_m"] > 0


def test_payload_highlights(payload):
    h = payload["highlights"]
    assert h["gap_s"] == pytest.approx(0.377, abs=1e-6)
    assert h["faster"] == "VER"
    assert set(h["top_speed_kmh"]) == {"VER", "NOR"}
    assert set(h["full_throttle_pct"]) == {"VER", "NOR"}
    assert h["top_speed_kmh"]["VER"] > h["top_speed_kmh"]["NOR"]
    for swing in h["biggest_swings"]:
        assert swing["gainer"] in ("VER", "NOR") and swing["seconds"] > 0


def test_highlights_gap_sign_flips_with_order(sides):
    a, b = sides
    flipped = lap_duel.build_payload(b, a, circuit_info=None)
    assert flipped["highlights"]["gap_s"] == pytest.approx(-0.377, abs=1e-6)
    assert flipped["highlights"]["faster"] == "VER"
    assert flipped["delta"][-1] == pytest.approx(-(90.889 - 90.512), abs=0.01)


def test_payload_corners_apexes_and_sections_are_consistent(payload):
    assert len(payload["corners"]) == 3
    assert payload["apexes"], "the synthetic dips should register as braking corners"
    length = payload["distance"][-1]
    secs = payload["sections"]
    assert secs[0]["start_m"] == 0.0 and secs[-1]["end_m"] == pytest.approx(length, abs=0.2)
    assert sum(s["delta_change_s"] for s in secs) == pytest.approx(payload["delta"][-1], abs=0.01)
    assert {s["gainer"] for s in secs} <= {"a", "b"}
    track = payload["track"]
    assert track is not None and len(track["x"]) == len(track["y"]) == len(track["faster"])


def test_teammates_get_distinguishable_colours():
    a = _side("VER", 90.5, team="Red Bull", color="#3671C6")
    b = _side("TSU", 90.9, team="Red Bull", color="#3671C6")
    p = lap_duel.build_payload(a, b, circuit_info=None)
    assert p["same_team"] is True
    assert p["a"]["color"] == "#3671C6"
    assert p["b"]["color"].lower() != p["a"]["color"].lower()
    assert p["b"]["color"].startswith("#") and len(p["b"]["color"]) == 7


def test_different_team_colours_are_left_alone(payload):
    assert payload["a"]["color"] == "#3671C6"
    assert payload["b"]["color"] == "#FF8000"


def test_full_detail_adds_accelerations(sides):
    p = lap_duel.build_payload(*sides, circuit_info=None, detail="full")
    assert p["detail"] == "full"
    acc = p["accelerations"]
    n = len(p["distance"])
    for key in ("a", "b"):
        assert len(acc[key]["long_g"]) == len(acc[key]["lat_g"]) == n
        assert all(abs(v) <= core.G_CLAMP for v in acc[key]["long_g"])
    assert "indicative" in acc["note"]


def test_cross_session_payload_flags(sides):
    a = sides[0]
    b = _side("NOR", 91.2, year=2026, identifier="Bahrain", session="R", team="McLaren", color="#FF8000")
    p = lap_duel.build_payload(a, b, circuit_info=None)
    assert p["same_session"] is False
    assert p["b"]["year"] == 2026 and p["b"]["session"] == "R"


# ----------------------------------------------------------------------
# Cache keys
# ----------------------------------------------------------------------
def _ref(driver, **kw):
    base = dict(year=2025, identifier=5, session="Q", driver=driver)
    base.update(kw)
    return LapRef(**base)


def test_data_type_is_ordered_and_upper_case():
    assert lap_duel._data_type("ver", "nor") == "lap_duel_VER_NOR"
    assert lap_duel._data_type("NOR", "VER") == "lap_duel_NOR_VER"


def test_variant_key_equals_data_type_only_for_the_default_variant():
    a, b = _ref("VER"), _ref("NOR")
    default = lap_duel._data_type("VER", "NOR")
    assert lap_duel._variant_key(a, b, "standard") == default
    # any deviation gets its own key so it cannot be served as the stored default
    assert lap_duel._variant_key(a, b, "full") != default
    assert lap_duel._variant_key(_ref("VER", lap=3), b, "standard") != default
    assert lap_duel._variant_key(a, _ref("NOR", segment="Q3"), "standard") != default
    assert lap_duel._variant_key(a, _ref("NOR", year=2024), "standard") != default
    assert lap_duel._variant_key(a, _ref("NOR", session="R"), "standard") != default
    keys = {
        lap_duel._variant_key(_ref("VER", lap=3), b, "standard"),
        lap_duel._variant_key(_ref("VER", lap=4), b, "standard"),
        lap_duel._variant_key(_ref("VER", segment="Q1"), b, "standard"),
        lap_duel._variant_key(a, b, "full"),
    }
    assert len(keys) == 4
    # every variant still starts with the stored key, so storage-cleanup prefix matching finds it
    assert all(k.startswith(default) for k in keys)


def test_variant_key_treats_identifier_types_as_the_same_session():
    assert lap_duel._variant_key(_ref("VER", identifier=5), _ref("NOR", identifier="5"), "standard") \
        == lap_duel._data_type("VER", "NOR")


def test_segment_normalisation_and_validation():
    assert lap_duel._normalise_segment(" q2 ") == "Q2"
    assert lap_duel._normalise_segment(None) is None
    assert lap_duel._normalise_segment("") is None
    with pytest.raises(ValueError):
        lap_duel._normalise_segment("Q4")


def test_identical_laps_and_bad_detail_are_rejected_before_any_fetch():
    with pytest.raises(ValueError, match="same lap"):
        lap_duel.LapDuelData()(2025, 5, "Q", "VER", "VER")
    with pytest.raises(ValueError, match="detail"):
        lap_duel.LapDuelData()(2025, 5, "Q", "VER", "NOR", detail="huge")
    with pytest.raises(ValueError, match="segment"):
        lap_duel.LapDuelData()(2025, 5, "Q", "VER", "NOR", segment1="Q4")


def test_plot_rejects_a_bad_format_before_any_fetch():
    with pytest.raises(ValueError, match="format"):
        lap_duel.LapDuelPlot()(2025, 5, "Q", "VER", "NOR", fmt="widescreen")


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------
@pytest.fixture()
def in_tmp(tmp_path, monkeypatch):
    """Outputs are written under the CWD-relative ``outputs/plots``; keep them out of the repo."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_render_writes_a_png_of_the_formats_exact_size(payload, in_tmp, fmt_name):
    from PIL import Image

    path = render_mod.render(payload, fmt_name)
    out = in_tmp / path
    assert out.is_file()
    assert str(out.resolve()).startswith(str(in_tmp.resolve()))
    fmt = canvas.get_format(fmt_name)
    with Image.open(out) as img:
        assert img.format == "PNG"
        assert img.size == (fmt.width_px, fmt.height_px)


def test_render_default_is_landscape(payload, in_tmp):
    from PIL import Image

    path = render_mod.render(payload, None)
    with Image.open(in_tmp / path) as img:
        assert img.size == (1920, 1080)


def test_render_full_detail_landscape(sides, in_tmp):
    from PIL import Image

    p = lap_duel.build_payload(*sides, circuit_info=None, detail="full")
    path = render_mod.render(p, "landscape", variant="full")
    with Image.open(in_tmp / path) as img:
        assert img.size == (1920, 1080)


def test_render_rejects_unknown_format(payload, in_tmp):
    with pytest.raises(ValueError):
        render_mod.render(payload, "cinema")


# ----------------------------------------------------------------------
# Registry wiring
# ----------------------------------------------------------------------
def test_registry_pair_spec_matches_the_request_path_key():
    from src.services.analysis.v2 import registry

    spec = next(s for s in registry.V2_PAIR_PLOTS if s.name == "lap_duel")
    assert spec.ordered is True                        # (A,B) and (B,A) are different documents
    assert spec.key_for("ver", "nor") == lap_duel._data_type("VER", "NOR")
    assert spec.key_for("NOR", "VER") == "lap_duel_NOR_VER"
    assert spec.persist_key is not None                # class-based: the backfill must write Mongo itself
    assert spec.applies("Q") and spec.applies("R") and spec.applies("FP1")
    assert any(e.spec is spec for e in registry.FEATURE_CATALOG)
