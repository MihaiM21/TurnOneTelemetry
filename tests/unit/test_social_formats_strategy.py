"""Social-format renders for the pit-strategy and teammate-battle charts (fully offline)."""
import os

import pytest
from PIL import Image

from src.services.analysis.v2 import pit_strategy as ps
from src.services.analysis.v2 import teammate_battle as tb
from src.services.plotting import canvas


def _pit_payload(n_drivers=22, n_undercuts=12):
    compounds = ["SOFT", "MEDIUM", "HARD"]
    stops = []
    for i in range(n_drivers):
        tla = f"D{i:02d}"
        stops.append({"driver": tla, "team": f"Team {i // 2}", "lap": 15 + i % 6, "stop_n": 1,
                      "pit_lane_time_s": 20.0 + (i % 5) * 0.4, "compound_in": compounds[i % 3],
                      "compound_out": compounds[(i + 1) % 3], "under_sc": i % 7 == 0, "drive_through": False})
        if i % 3 == 0:
            stops.append({"driver": tla, "team": f"Team {i // 2}", "lap": 17 + i % 6 + 12, "stop_n": 2,
                          "pit_lane_time_s": 21.5, "compound_in": compounds[(i + 1) % 3],
                          "compound_out": None, "under_sc": False, "drive_through": True})
    undercuts = [
        {"attacker": f"D{i:02d}", "defender": f"D{i + 1:02d}", "lap": 12 + i, "gap_before_s": 1.2,
         "gap_after_s": -0.5 + i * 0.3, "gain_s": 1.7 - i * 0.8, "worked": i % 2 == 0}
        for i in range(n_undercuts)
    ]
    return {
        "stops": stops,
        "undercuts": undercuts,
        "summary": {
            "fastest_stop": {"driver": "D03", "lap": 18, "pit_lane_time_s": 19.8},
            "avg_stop_by_team": [{"team": f"Team {i}", "avg_pit_lane_time_s": 20.0 + i * 0.3, "n_stops": 2}
                                 for i in range(11)],
        },
        "free_changes": [],
    }


def _teammate_payload(n_teams=11):
    teams = []
    for i in range(n_teams):
        teams.append({
            "team": f"Team Number {i}", "color": "#%02X%02X%02X" % (30 + i * 18, 200 - i * 12, 90 + i * 9),
            "driver_a": f"A{i:02d}", "driver_b": f"B{i:02d}",
            "quali_h2h": [i % 12, 12 - i % 12], "race_h2h": [(i * 3) % 11, 5],
            "avg_quali_gap_s": None if i == 4 else (0.2 - i * 0.05), "rounds_counted": 12,
        })
    return {"teams": teams}


def _size(path):
    with Image.open(path) as img:
        return img.size


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_pit_strategy_formatted_size(tmp_path, monkeypatch, fmt_name):
    monkeypatch.chdir(tmp_path)
    order = [f"D{i:02d}" for i in range(22)][::-1]
    periods = [{"status": "SC", "start_lap": 20, "end_lap": 24}, {"status": "YELLOW", "start_lap": 3, "end_lap": 4}]
    out = ps._render_formatted(_pit_payload(), periods, 2026, "Test Grand Prix", "R", fmt_name, order,
                               {"D00": 40}, {"D01": "MEDIUM"})
    fmt = canvas.get_format(fmt_name)
    assert _size(out) == (fmt.width_px, fmt.height_px)
    assert out.endswith(f"R{canvas.format_suffix(fmt_name)}.png")
    assert os.path.isfile(out)


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_pit_strategy_formatted_sparse_payload(tmp_path, monkeypatch, fmt_name):
    """No undercuts, no summary, no order or lap counts: still renders at the exact size."""
    monkeypatch.chdir(tmp_path)
    payload = {"stops": _pit_payload(n_drivers=3)["stops"], "undercuts": [], "summary": {}, "free_changes": []}
    out = ps._render_formatted(payload, [], 2025, "Test Grand Prix", "Sprint", fmt_name)
    fmt = canvas.get_format(fmt_name)
    assert _size(out) == (fmt.width_px, fmt.height_px)


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_teammate_battle_formatted_size(tmp_path, monkeypatch, fmt_name):
    monkeypatch.chdir(tmp_path)
    out = tb._render_formatted(_teammate_payload(), 2026, fmt_name)
    fmt = canvas.get_format(fmt_name)
    assert _size(out) == (fmt.width_px, fmt.height_px)
    assert out.endswith(f"2026{canvas.format_suffix(fmt_name)}.png")
    assert os.path.dirname(out).replace("\\", "/").endswith("2026/Season/TeammateBattle")


def test_teammate_battle_formatted_all_zero_counts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = {"teams": [{"team": "Alpha", "color": "#FF0000", "driver_a": "AAA", "driver_b": "BBB",
                          "quali_h2h": [0, 0], "race_h2h": [0, 0], "avg_quali_gap_s": None,
                          "rounds_counted": 0}]}
    out = tb._render_formatted(payload, 2025, "story")
    assert _size(out) == (1080, 1920)


def test_pit_strategy_bad_format_raises_before_fetch(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("data path reached")

    monkeypatch.setattr(ps, "assert_session_type", boom)
    monkeypatch.setattr(ps, "PitStrategyData", boom)
    monkeypatch.setattr(ps, "SessionDataStore", boom)
    with pytest.raises(ValueError):
        ps.PitStrategyPlot()(2026, "Azerbaijan Grand Prix", "R", fmt="cinema")


def test_teammate_battle_bad_format_raises_before_fetch(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("data path reached")

    monkeypatch.setattr(tb, "TeammateBattleData", boom)
    with pytest.raises(ValueError):
        tb.TeammateBattlePlot()(2026, fmt="cinema")


def test_pit_strategy_plot_fmt_reuses_payload(tmp_path, monkeypatch):
    """fmt= renders from the Data payload and the store's derived streams; nothing else is fetched."""
    payload = _pit_payload(n_drivers=4, n_undercuts=2)

    class FakeData:
        def __call__(self, y, identifier, e):
            return payload

    class FakeStore:
        event_name = "Test Grand Prix"

        def __init__(self, *a, **k):
            pass

        def driver_list(self):
            return {str(i): {"tla": f"D{i:02d}"} for i in range(4)}

        def positions_by_lap(self):
            return {str(i): [{"lap": 50, "position": 4 - i}] for i in range(4)}

        def lap_times(self):
            return {str(i): [{"lap": 50 - i, "time_s": 90.0}] for i in range(4)}

        def stints(self):
            return {str(i): [{"compound": "MEDIUM"}] for i in range(4)}

    monkeypatch.setattr(ps, "assert_session_type", lambda *a, **k: None)
    monkeypatch.setattr(ps, "PitStrategyData", FakeData)
    monkeypatch.setattr(ps, "SessionDataStore", FakeStore)
    monkeypatch.setattr(ps, "get_track_status_periods", lambda store: [])
    monkeypatch.chdir(tmp_path)

    out = ps.PitStrategyPlot()(2026, "Test Grand Prix", "R", fmt="story")
    assert _size(out) == (1080, 1920)
    assert out.endswith("Pit strategy 2026 Test Grand Prix R_story.png")

    order, laps, first = ps._finish_context(FakeStore())
    assert order == ["D03", "D02", "D01", "D00"]
    assert laps["D00"] == 50 and first["D02"] == "MEDIUM"


def test_pit_strategy_stint_segments_use_first_compound():
    stops = [{"lap": 20, "compound_in": None, "compound_out": "HARD"},
             {"lap": 40, "compound_in": None, "compound_out": None}]
    assert ps._stint_segments(stops, 55, "MEDIUM") == [(0, 20, "MEDIUM"), (20, 40, "HARD"), (40, 55, "HARD")]


def test_legacy_render_paths_unchanged(tmp_path, monkeypatch):
    """fmt=None keeps the original figure and un-suffixed file names."""
    monkeypatch.chdir(tmp_path)
    out = ps.PitStrategyPlot._render(_pit_payload(n_drivers=3), [], 2025, "Legacy GP", "R")
    assert out.endswith("Pit strategy 2025 Legacy GP R.png")
    out = tb.TeammateBattlePlot._render(_teammate_payload(2), 2025)
    assert out.endswith("Teammate battle 2025.png")
