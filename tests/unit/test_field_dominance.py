"""Offline tests for the field dominance map and the sector-gap-to-pole chart.

Field dominance is exercised with synthetic laps on a circle whose per-minisector
speeds are chosen so the owner and the margin of every minisector are known
exactly. Sector gap is exercised through its pure seams (timing-stream parser,
payload builder) and through ``_generate`` with a fake store.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.analysis.v2 import _field_render as render
from src.services.analysis.v2 import field_dominance as fd
from src.services.analysis.v2 import sector_gap as sg
from src.services.plotting import canvas

LENGTH_M = 5600.0
N = fd.NUM_MINISECTORS
PER = 40                                   # samples per minisector in the synthetic trace


# ----------------------------------------------------------------------
# Synthetic laps
# ----------------------------------------------------------------------
def make_trace(speeds_ms, with_xy: bool = True) -> pd.DataFrame:
    """One lap on a circle; ``speeds_ms[k]`` (m/s) is constant through minisector ``k``."""
    speeds = np.repeat(np.asarray(speeds_ms, dtype=float), PER)
    step = LENGTH_M / len(speeds)
    t = np.concatenate([[0.0], np.cumsum(step / speeds)])
    d = np.linspace(0.0, LENGTH_M, len(t))
    kmh = np.concatenate([speeds, speeds[-1:]]) * 3.6
    theta = 2 * np.pi * d / LENGTH_M
    radius_tenths = LENGTH_M / (2 * np.pi) * 10.0
    return pd.DataFrame({
        "t": t, "d": d, "speed": kmh, "throttle": 100.0, "brake": 0.0, "gear": 6.0, "rpm": 10000.0, "drs": 0.0,
        "x": radius_tenths * np.cos(theta) if with_xy else np.nan,
        "y": radius_tenths * np.sin(theta) if with_xy else np.nan,
    })


def speeds(fast: range, fast_v: float, slow_v: float):
    return [fast_v if k in fast else slow_v for k in range(N)]


A_SPEEDS = speeds(range(0, 10), 75.0, 68.0)
B_SPEEDS = speeds(range(10, 18), 72.0, 67.0)
D_SPEEDS = speeds(range(18, 25), 71.0, 60.0)
C_SPEEDS = [0.97 * v for v in A_SPEEDS]       # A's slower teammate, slower everywhere


def _lap(tla, team, colour, sp, with_xy=True):
    trace = make_trace(sp, with_xy)
    return {"tla": tla, "team": team, "color": colour, "trace": trace, "lap_time": float(trace["t"].iloc[-1])}


def make_field(with_xy: bool = True):
    return {
        "1": _lap("AAA", "Alpha", "#FF0000", A_SPEEDS, with_xy),
        "2": _lap("CCC", "Alpha", "#FF0000", C_SPEEDS, with_xy),
        "3": _lap("BBB", "Beta", "#00FF00", B_SPEEDS, with_xy),
        "4": _lap("DDD", "Delta", "#0000FF", D_SPEEDS, with_xy),
    }


def _margin(first, second) -> float:
    """Seconds the second-best speed loses to the best over one minisector."""
    return LENGTH_M / N * (1.0 / second - 1.0 / first)


def payload_for(mode="team", top_n=None, with_xy=True, rotation=25.0):
    cands = fd.select_candidates(make_field(with_xy), mode, top_n)
    return fd.build_payload(cands, rotation, mode, top_n, 2026, "Test Grand Prix", "Q")


# ----------------------------------------------------------------------
# Field dominance: ownership maths
# ----------------------------------------------------------------------
def test_team_mode_owners_counts_and_margins():
    p = payload_for("team")
    owners = [m["owner"] for m in p["minisectors"]]
    assert owners == ["Alpha"] * 10 + ["Beta"] * 8 + ["Delta"] * 7
    assert [(o["name"], o["count"]) for o in p["owners"]] == [("Alpha", 10), ("Beta", 8), ("Delta", 7)]
    # Team mode: the runner-up is another team, not the slower teammate.
    assert p["minisectors"][0]["margin_s"] == pytest.approx(_margin(75.0, 67.0), abs=0.02)
    assert p["minisectors"][12]["margin_s"] == pytest.approx(_margin(72.0, 68.0), abs=0.02)
    assert p["minisectors"][20]["margin_s"] == pytest.approx(_margin(71.0, 68.0), abs=0.02)
    assert all(m["margin_s"] >= 0 for m in p["minisectors"])


def test_team_mode_uses_the_faster_teammate():
    p = payload_for("team")
    alpha = next(o for o in p["owners"] if o["name"] == "Alpha")
    assert alpha["driver"] == "AAA"
    assert alpha["code"] == "ALP"                        # first three letters (unknown team)
    assert [c["driver"] for c in p["candidates"]] == ["AAA", "BBB", "DDD"]
    assert p["mode"] == "team" and p["top_n"] is None


def test_driver_mode_lists_every_driver_and_teammate_is_runner_up():
    p = payload_for("driver")
    assert {c["name"] for c in p["candidates"]} == {"AAA", "BBB", "CCC", "DDD"}
    assert [(o["name"], o["count"]) for o in p["owners"]] == [("AAA", 10), ("BBB", 8), ("DDD", 7)]
    # CCC (0.97 x AAA) is the runner-up wherever AAA wins.
    assert p["minisectors"][3]["margin_s"] == pytest.approx(_margin(75.0, 0.97 * 75.0), abs=0.02)
    zero = next(c for c in p["candidates"] if c["name"] == "CCC")
    assert zero["count"] == 0


def test_driver_mode_separates_teammate_colours():
    p = payload_for("driver")
    colours = {c["name"]: c["color"] for c in p["candidates"]}
    assert colours["AAA"] == "#FF0000"
    assert colours["CCC"] != colours["AAA"]              # lightened
    assert len(set(colours.values())) == 4


def _dist(a: str, b: str) -> float:
    return fd._colour_distance(a, b)


def test_team_mode_lightens_the_slower_of_two_near_identical_team_colours():
    """Ferrari/Audi are both red and Haas/Cadillac both grey; the slower team of each pair is lightened."""
    field = {
        "1": _lap("AAA", "Ferrari", "#E8002D", A_SPEEDS),
        "2": _lap("BBB", "Mercedes", "#27F4D2", B_SPEEDS),
        "3": _lap("CCC", "Audi", "#F50537", [0.96 * v for v in A_SPEEDS]),
        "4": _lap("DDD", "Haas F1 Team", "#B6BABD", [0.94 * v for v in A_SPEEDS]),
        "5": _lap("EEE", "Cadillac", "#AAAAAD", [0.93 * v for v in A_SPEEDS]),
    }
    cands = fd.select_candidates(field, "team", None)
    colours = {c["name"]: c["color"] for c in cands}
    assert [c["name"] for c in cands] == ["Ferrari", "Mercedes", "Audi", "Haas F1 Team", "Cadillac"]
    # The faster team of each clashing pair keeps its livery colour...
    assert colours["Ferrari"] == "#E8002D"
    assert colours["Haas F1 Team"] == "#B6BABD"
    assert colours["Mercedes"] == "#27F4D2"
    # ...and the slower one now reads as a different colour.
    assert colours["Audi"] != "#F50537"
    assert colours["Cadillac"] != "#AAAAAD"
    ordered = [c["color"] for c in cands]
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            assert _dist(a, b) >= fd.COLOUR_MIN_DISTANCE


def test_team_mode_leaves_distinct_colours_untouched():
    cands = fd.select_candidates(make_field(), "team", None)
    assert {c["name"]: c["color"] for c in cands} == {"Alpha": "#FF0000", "Beta": "#00FF00", "Delta": "#0000FF"}


def test_lightened_team_colour_reaches_the_payload_and_survives_top_n():
    field = {
        "1": _lap("AAA", "Ferrari", "#E8002D", A_SPEEDS),
        "2": _lap("CCC", "Audi", "#F50537", B_SPEEDS),
        "3": _lap("DDD", "Delta", "#0000FF", D_SPEEDS),
    }
    audi_full = next(c["color"] for c in fd.select_candidates(field, "team", None) if c["name"] == "Audi")
    cands = fd.select_candidates(field, "team", 2)               # separation happens before the top_n cut
    assert next(c["color"] for c in cands if c["name"] == "Audi") == audi_full
    p = fd.build_payload(cands, 0.0, "team", 2, 2026, "Test Grand Prix", "Q")
    assert next(o["color"] for o in p["owners"] if o["name"] == "Audi") == audi_full != "#F50537"


def test_colour_distance_is_perceptual_and_tolerates_bad_input():
    assert fd._colour_distance("#000000", "#000000") == 0.0
    assert fd._colour_distance("#E80020", "#FF5226") < fd.COLOUR_MIN_DISTANCE   # Ferrari vs Audi read alike
    assert fd._colour_distance("#FFF", "#000000") == float("inf")
    assert fd._colour_distance("nope", "#000000") == float("inf")


def test_top_n_keeps_only_the_fastest_candidates():
    p = payload_for("team", top_n=2)
    assert [c["name"] for c in p["candidates"]] == ["Alpha", "Beta"]
    assert [(o["name"], o["count"]) for o in p["owners"]] == [("Alpha", 17), ("Beta", 8)]
    assert p["top_n"] == 2
    p3 = payload_for("driver", top_n=3)
    assert [c["name"] for c in p3["candidates"]] == ["AAA", "BBB", "CCC"]
    assert sum(o["count"] for o in p3["owners"]) == N


@pytest.mark.parametrize("bad", [0, 1, 11, 99, -3])
def test_top_n_out_of_range_is_rejected(bad):
    with pytest.raises(ValueError, match="top_n"):
        fd.validate_args("driver", bad)
    with pytest.raises(ValueError, match="top_n"):
        fd.FieldDominanceData()(2026, "Nowhere", "Q", mode="driver", top_n=bad)      # before any fetch


def test_validate_args_accepts_the_edges_and_rejects_bad_mode():
    assert fd.validate_args("Team", None) == ("team", None)
    assert fd.validate_args("driver", 2) == ("driver", 2)
    assert fd.validate_args("driver", "10") == ("driver", 10)
    with pytest.raises(ValueError, match="mode"):
        fd.validate_args("constructor", None)
    with pytest.raises(ValueError, match="top_n"):
        fd.validate_args("driver", "many")


def test_data_type_keys():
    assert fd.data_type("team") == "field_dominance_team"
    assert fd.data_type("driver") == "field_dominance_driver"
    assert fd.data_type("driver", 3) == "field_dominance_driver_top3"


def test_minisector_times_sum_to_the_lap_time():
    lap = make_field()["1"]
    ref = fd.reference_line(lap["trace"])
    for r in (ref, None):
        times = fd.minisector_times(lap["trace"], N, r)
        assert len(times) == N
        assert times.sum() == pytest.approx(lap["lap_time"], abs=0.02)
        assert times[0] == pytest.approx(LENGTH_M / N / 75.0, abs=0.02)


def test_fallback_without_position_data_gives_the_same_owners():
    with_xy = payload_for("team")
    without = payload_for("team", with_xy=False)
    assert [m["owner"] for m in without["minisectors"]] == [m["owner"] for m in with_xy["minisectors"]]
    assert without["track"] is None
    assert with_xy["track"] is not None


def test_ties_go_to_the_faster_lap():
    a = _lap("AAA", "Alpha", "#FF0000", [70.0] * N)
    b = _lap("BBB", "Beta", "#00FF00", [70.0] * N)
    b["lap_time"] += 0.001                                # identical minisector times, marginally slower lap
    cands = fd.select_candidates({"1": b, "2": a}, "driver", None)
    p = fd.build_payload(cands, 0.0, "driver", None, 2026, "X", "Q")
    assert {m["owner"] for m in p["minisectors"]} == {"AAA"}


def test_needs_two_candidates():
    lone = fd.select_candidates({"1": make_field()["1"]}, "team", None)
    with pytest.raises(ValueError):
        fd.compute_ownership(lone)


# ----------------------------------------------------------------------
# Field dominance: payload shape
# ----------------------------------------------------------------------
def test_payload_shape():
    p = payload_for("team")
    assert set(p) == {"mode", "top_n", "minisector_count", "minisectors", "track", "owners", "candidates",
                      "highlights", "method", "session_info"}
    assert p["minisector_count"] == N == len(p["minisectors"])
    first, last = p["minisectors"][0], p["minisectors"][-1]
    assert set(first) == {"index", "start_fraction", "end_fraction", "owner", "owner_color", "margin_s"}
    assert (first["start_fraction"], last["end_fraction"]) == (0.0, 1.0)
    for a, b in zip(p["minisectors"], p["minisectors"][1:]):
        assert a["end_fraction"] == b["start_fraction"]
    track = p["track"]
    assert len(track["x"]) == len(track["y"]) == len(track["fraction"]) == fd.OUTLINE_POINTS
    assert track["rotation"] == 25.0
    assert track["fraction"][0] == 0.0 and track["fraction"][-1] == pytest.approx(1.0, abs=1e-3)
    assert [o["count"] for o in p["owners"]] == sorted((o["count"] for o in p["owners"]), reverse=True)
    assert set(p["owners"][0]) == {"name", "code", "color", "count", "driver", "team"}
    hi = p["highlights"]
    assert hi["pole"]["driver"] == "AAA"
    assert hi["most_owned"]["name"] == "Alpha"
    assert hi["biggest_margin"]["margin_s"] >= hi["closest_margin"]["margin_s"]
    assert p["session_info"] == {"year": 2026, "event_name": "Test Grand Prix", "session_name": "Q"}


def test_team_codes():
    assert fd.team_code("McLaren") == "MCL"
    assert fd.team_code("Red Bull Racing") == "RBR"
    assert fd.team_code("Haas F1 Team") == "HAA"
    assert fd.team_code("Some New Team") == "SOM"
    assert fd.team_code(None) == "?"


def test_generate_with_a_fake_store(monkeypatch):
    class Store:
        event_name = "Test Grand Prix"

    monkeypatch.setattr(fd, "build_session_store", lambda *a, **k: Store())
    monkeypatch.setattr(fd, "load_field_laps", lambda store: make_field())
    monkeypatch.setattr(fd, "get_circuit_info_for_session", lambda store: {"rotation": 30})
    p = fd._generate(2026, "Test", "Q", "team", None)
    assert p["track"]["rotation"] == 30
    assert p["session_info"]["event_name"] == "Test Grand Prix"

    monkeypatch.setattr(fd, "load_field_laps", lambda store: {"1": make_field()["1"]})
    with pytest.raises(DataNotAvailableError):
        fd._generate(2026, "Test", "Q", "team", None)

    monkeypatch.setattr(fd, "build_session_store", lambda *a, **k: None)
    with pytest.raises(DataNotAvailableError):
        fd._generate(2026, "Test", "Q", "team", None)


def test_data_callable_uses_the_mode_specific_cache_key(monkeypatch):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {"ok": True}

    monkeypatch.setattr(fd, "cached_or_generate", fake)
    assert fd.FieldDominanceData()(2026, "X", "Q", mode="driver", top_n=3) == {"ok": True}
    assert seen["data_type"] == "field_dominance_driver_top3" and seen["version"] == "v2"
    fd.FieldDominanceData()(2026, "X", "Q")
    assert seen["data_type"] == "field_dominance_team"


def test_plot_rejects_a_bad_format_before_any_fetch(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("fetched before validating fmt")

    monkeypatch.setattr(fd.FieldDominanceData, "__call__", boom)
    with pytest.raises(ValueError, match="format"):
        fd.FieldDominancePlot()(2026, "X", "Q", fmt="cinema")
    with pytest.raises(ValueError, match="top_n"):
        fd.FieldDominancePlot()(2026, "X", "Q", mode="driver", top_n=1)


# ----------------------------------------------------------------------
# Sector gap: timing-stream parser
# ----------------------------------------------------------------------
def _entry(num, sectors):
    return {"Lines": {num: {"Sectors": sectors}}}


def _val(v):
    return {"Value": v} if v is not None else {}


def test_collect_sector_candidates_handles_resets_and_out_of_order_updates():
    entries = [
        _entry("1", [{"Value": ""}, {"Value": ""}, {"Value": ""}]),           # initial snapshot
        _entry("1", {"0": _val("30.100"), "1": {"Segments": {}}}),
        _entry("1", {"1": _val("40.200")}),
        _entry("1", {"2": _val("20.300")}),                                     # lap 1 complete: 90.6
        _entry("1", {"0": _val("30.000"), "1": _val(""), "2": _val("")}),      # lap 2 starts: reset
        _entry("1", {"2": _val("20.100")}),                                     # S3 before S2 (odd, but legal)
        _entry("1", {"1": _val("39.900")}),                                     # lap 2 complete: 90.0
        _entry("2", {"0": {"PreviousValue": "1.0"}}),                           # ignored
        {"Lines": None},
    ]
    cands = sg.collect_sector_candidates(entries)
    assert cands["1"] == [(30.1, 40.2, 20.3), (30.0, 39.9, 20.1)]
    assert "2" not in cands


def test_sector_triples_never_mix_two_laps():
    entries = [
        _entry("1", {"0": _val("30.0"), "1": _val("40.0"), "2": _val("20.0")}),
        _entry("1", {"0": _val("29.0"), "1": _val(""), "2": _val("")}),         # lap 2: only S1 so far
    ]
    assert sg.collect_sector_candidates(entries)["1"] == [(30.0, 40.0, 20.0)]


def test_match_lap_sectors_by_sum():
    triples = [(30.1, 40.2, 20.3), (30.0, 39.9, 20.1)]
    assert sg.match_lap_sectors(triples, 90.0) == (30.0, 39.9, 20.1)
    assert sg.match_lap_sectors(triples, 90.6) == (30.1, 40.2, 20.3)
    assert sg.match_lap_sectors(triples, 90.601) == (30.1, 40.2, 20.3)          # feed rounding
    assert sg.match_lap_sectors(triples, 95.0) is None
    assert sg.match_lap_sectors([], 90.0) is None


# ----------------------------------------------------------------------
# Sector gap: maths + payload
# ----------------------------------------------------------------------
def _rows():
    return [
        {"position": 1, "driver": "POL", "color": "#00D2BE", "lap_time_s": 90.000, "sectors": [30.000, 40.000, 20.000]},
        {"position": 2, "driver": "AAA", "color": "#FF0000", "lap_time_s": 90.500, "sectors": [30.200, 39.900, 20.400]},
        {"position": 3, "driver": "BBB", "color": "#00FF00", "lap_time_s": 91.100, "sectors": [30.500, 40.300, 20.300]},
    ]


def test_sector_gap_payload_maths_with_a_negative_sector():
    p = sg.build_payload(_rows(), 2026, "Test Grand Prix", "Q")
    assert p["pole"] == {"driver": "POL", "color": "#00D2BE", "lap_time_s": 90.0, "sectors": [30.0, 40.0, 20.0]}
    assert [d["driver"] for d in p["drivers"]] == ["AAA", "BBB"]
    a, b = p["drivers"]
    assert a["sector_gaps_s"] == [0.2, -0.1, 0.4]
    assert a["gap_s"] == 0.5 and a["position"] == 2
    assert sum(a["sector_gaps_s"]) == pytest.approx(a["gap_s"], abs=1e-9)
    assert b["sector_gaps_s"] == [0.5, 0.3, 0.3]
    assert b["gap_s"] == pytest.approx(1.1, abs=1e-9)
    assert p["highlights"]["faster_than_pole"] == [{"driver": "AAA", "sector": 2, "gap_s": -0.1}]
    assert [s["driver"] for s in p["highlights"]["sector_leaders"]] == ["POL", "AAA", "POL"]
    assert p["highlights"]["largest_sector_gap"] == {"driver": "BBB", "sector": 1, "gap_s": 0.5}
    assert p["session_info"]["session_name"] == "Q"
    assert p["unmatched"] == []


def test_sector_gap_payload_needs_two_drivers():
    with pytest.raises(ValueError):
        sg.build_payload(_rows()[:1], 2026, "X", "Q")


class FakeStore:
    event_name = "Test Grand Prix"
    base_url = "https://example.invalid/"
    client = object()

    def __init__(self, entries):
        self._entries = entries

    def timing_data(self):
        return self._entries

    def driver_list(self):
        return {"1": {"tla": "POL", "color": "#00D2BE"}, "2": {"tla": "AAA", "color": "#FF0000"},
                "3": {"tla": "BBB", "color": "#00FF00"}}


def _timing(num, s1, s2, s3):
    return [_entry(num, {"0": _val("%.3f" % s1), "1": _val("%.3f" % s2), "2": _val("%.3f" % s3)})]


def _classification(times):
    return pd.DataFrame({"DriverNum": list(times), "StartTime": 0.0, "EndTime": 1.0,
                         "LapTime": list(times.values()), "Position": range(1, len(times) + 1)})


def test_generate_matches_sectors_to_the_fastest_lap(monkeypatch):
    entries = (
        _timing("1", 30.5, 40.5, 20.5)            # a slower lap first
        + _timing("1", 30.0, 40.0, 20.0)
        + _timing("2", 30.2, 39.9, 20.4)
        + _timing("3", 30.5, 40.3, 20.3)
    )
    monkeypatch.setattr(sg, "build_session_store", lambda *a, **k: FakeStore(entries))
    monkeypatch.setattr(sg, "get_qualifying_classification",
                        lambda *a, **k: _classification({"1": 90.0, "2": 90.5, "3": 91.1}))
    p = sg._generate(2026, "Test", "Q")
    assert p["pole"]["sectors"] == [30.0, 40.0, 20.0]
    assert p["drivers"][0]["sector_gaps_s"] == [0.2, -0.1, 0.4]
    assert [d["color"] for d in p["drivers"]] == ["#FF0000", "#00FF00"]


def test_generate_skips_unmatched_drivers_and_fails_without_a_pole_lap(monkeypatch):
    entries = _timing("1", 30.0, 40.0, 20.0) + _timing("2", 30.2, 39.9, 20.4)
    monkeypatch.setattr(sg, "build_session_store", lambda *a, **k: FakeStore(entries))
    monkeypatch.setattr(sg, "get_qualifying_classification",
                        lambda *a, **k: _classification({"1": 90.0, "2": 90.5, "3": 91.1}))
    p = sg._generate(2026, "Test", "Q")
    assert p["unmatched"] == ["BBB"] and len(p["drivers"]) == 1

    monkeypatch.setattr(sg, "get_qualifying_classification",
                        lambda *a, **k: _classification({"1": 89.0, "2": 90.5, "3": 91.1}))
    with pytest.raises(DataNotAvailableError):
        sg._generate(2026, "Test", "Q")


@pytest.mark.parametrize("session", ["R", "S", "FP1", "Race"])
def test_sector_gap_is_qualifying_only(monkeypatch, session):
    def boom(*a, **k):
        raise AssertionError("touched the network / cache")

    monkeypatch.setattr(sg, "build_session_store", boom)
    monkeypatch.setattr(sg, "cached_or_generate", boom)
    with pytest.raises(DataNotAvailableError) as info:
        sg.SectorGapData()(2026, "Azerbaijan Grand Prix", session)
    assert "sector_gap" in str(info.value)


def test_sector_gap_caches_under_sector_gap(monkeypatch):
    seen = {}
    monkeypatch.setattr(sg, "cached_or_generate", lambda **kw: seen.update(kw) or {"ok": 1})
    for session in ("Q", "SQ"):
        assert sg.SectorGapData()(2026, "X", session) == {"ok": 1}
        assert seen["data_type"] == "sector_gap" and seen["session"] == session


def test_sector_gap_plot_rejects_a_bad_format_before_any_fetch(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("fetched before validating fmt")

    monkeypatch.setattr(sg.SectorGapData, "__call__", boom)
    with pytest.raises(ValueError, match="format"):
        sg.SectorGapPlot()(2026, "X", "Q", fmt="widescreen")


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------
@pytest.fixture()
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _size(path, root):
    from PIL import Image

    with Image.open(root / path) as img:
        return img.size


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
@pytest.mark.parametrize("mode,top_n", [("team", None), ("driver", 3)])
def test_render_dominance_every_format(in_tmp, fmt_name, mode, top_n):
    path = render.render_dominance(payload_for(mode, top_n), fmt_name)
    fmt = canvas.get_format(fmt_name)
    assert _size(path, in_tmp) == (fmt.width_px, fmt.height_px)
    assert "2026/TestGrandPrix/Q/" in path and path.endswith(f"{canvas.format_suffix(fmt_name)}.png")


def test_render_dominance_default_is_landscape_and_names_differ_by_mode(in_tmp):
    a = render.render_dominance(payload_for("team"), None)
    b = render.render_dominance(payload_for("driver", 3), None)
    assert _size(a, in_tmp) == (1920, 1080)
    assert a != b and "top3" in b


def test_render_dominance_without_position_data(in_tmp):
    path = render.render_dominance(payload_for("team", with_xy=False), "square")
    assert _size(path, in_tmp) == (1080, 1080)


def test_render_dominance_with_many_owners(in_tmp):
    field = {}
    for i in range(11):
        sp = [65.0 + (5.0 if k % 11 == i else 0.0) for k in range(N)]
        field[str(i)] = _lap(f"D{i:02d}", f"Team {i}", f"#{(i * 22) % 256:02X}{(i * 50) % 256:02X}90", sp)
    cands = fd.select_candidates(field, "team", None)
    payload = fd.build_payload(cands, 0.0, "team", None, 2026, "Big Field", "Q")
    assert len(payload["owners"]) >= 8
    for fmt_name in canvas.FORMAT_NAMES:
        path = render.render_dominance(payload, fmt_name)
        fmt = canvas.get_format(fmt_name)
        assert _size(path, in_tmp) == (fmt.width_px, fmt.height_px)


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_render_sector_gap_every_format(in_tmp, fmt_name):
    rows = _rows() + [
        {"position": 4 + i, "driver": f"D{i:02d}", "color": "#8888AA", "lap_time_s": 91.5 + 0.2 * i,
         "sectors": [30.6 + 0.05 * i, 40.6 + 0.05 * i, 20.3 + 0.1 * i]}
        for i in range(7)
    ]
    payload = sg.build_payload(rows, 2026, "Test Grand Prix", "Q")
    path = render.render_sector_gap(payload, fmt_name)
    fmt = canvas.get_format(fmt_name)
    assert _size(path, in_tmp) == (fmt.width_px, fmt.height_px)
    assert path.endswith(f"Sector gap{canvas.format_suffix(fmt_name)}.png")


def test_render_sector_gap_handles_all_negative_and_tiny_gaps(in_tmp):
    rows = [
        {"position": 1, "driver": "POL", "color": "#00D2BE", "lap_time_s": 90.0, "sectors": [30.0, 40.0, 20.0]},
        {"position": 2, "driver": "AAA", "color": "#FF0000", "lap_time_s": 90.001, "sectors": [29.9, 39.9, 20.201]},
        {"position": 3, "driver": "BBB", "color": "#00FF00", "lap_time_s": 90.0, "sectors": [29.9, 40.1, 20.0]},
    ]
    path = render.render_sector_gap(sg.build_payload(rows, 2026, "Test Grand Prix", "Q"), "landscape")
    assert _size(path, in_tmp) == (1920, 1080)


def test_render_rejects_an_unknown_format(in_tmp):
    with pytest.raises(ValueError, match="format"):
        render.render_dominance(payload_for("team"), "cinema")
    with pytest.raises(ValueError, match="format"):
        render.render_sector_gap(sg.build_payload(_rows(), 2026, "X", "Q"), "cinema")


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_no_text_below_the_minimum_size(in_tmp, monkeypatch, fmt_name):
    """Every visible label is at least 0.7x the format's base font size."""
    from matplotlib.text import Text

    fmt = canvas.get_format(fmt_name)
    real_save = canvas.save_png
    smallest = []

    def spy(fig, path, f, scale=1.0):
        fig.canvas.draw()
        for t in fig.findobj(Text):
            if t.get_text().strip() and t.get_visible():
                smallest.append((t.get_fontsize(), t.get_text()))
        return real_save(fig, path, f, scale)

    monkeypatch.setattr(canvas, "save_png", spy)
    render.render_dominance(payload_for("team"), fmt_name)
    render.render_sector_gap(sg.build_payload(_rows(), 2026, "Test Grand Prix", "Q"), fmt_name)
    assert smallest
    floor = 0.7 * fmt.base_fontsize - 1e-6
    too_small = [(size, text) for size, text in smallest if size < floor]
    assert not too_small, too_small
