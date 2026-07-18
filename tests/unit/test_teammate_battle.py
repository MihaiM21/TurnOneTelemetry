"""Offline unit tests for the Teammate Battle Tracker (V2) and its
`_season_helpers` foundations.

All scenarios use synthetic season-result rows (no network, Redis, or
Mongo) so the H2H counting, mid-season-swap pairing, and deepest-common-
segment quali gap math can be verified against hand-computed values.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from src.core.exceptions import DataNotAvailableError
from src.services.analysis.v2 import _season_helpers as sh
from src.services.analysis.v2 import teammate_battle as tb


# ----------------------------------------------------------------------
# Synthetic builders
# ----------------------------------------------------------------------
def _race_row(round_nr, driver, constructor, position, classified=True, status="Finished"):
    return {
        "round": round_nr,
        "driver_code": driver,
        "driver_name": driver.title(),
        "constructor": constructor,
        "position": position,
        "grid": position,
        "status": status,
        "classified": classified,
        "q1_s": None,
        "q2_s": None,
        "q3_s": None,
    }


def _quali_row(round_nr, driver, constructor, position, q1=None, q2=None, q3=None):
    return {
        "round": round_nr,
        "driver_code": driver,
        "driver_name": driver.title(),
        "constructor": constructor,
        "position": position,
        "grid": None,
        "status": None,
        "classified": position is not None,
        "q1_s": q1,
        "q2_s": q2,
        "q3_s": q3,
    }


# ----------------------------------------------------------------------
# pair_teammates — mid-season swap
# ----------------------------------------------------------------------
def test_pair_teammates_mid_season_swap():
    """Three drivers share one constructor across the season (a mid-season
    swap); pairing keeps only the two most-frequent drivers."""
    results = (
        [_race_row(r, "ALO", "Aston Martin", 5) for r in range(1, 11)]
        + [_race_row(r, "STR", "Aston Martin", 8) for r in range(1, 8)]
        + [_race_row(r, "DRV", "Aston Martin", 9) for r in range(8, 11)]
    )
    pairs = sh.pair_teammates(results)
    # ALO (10 rounds) + STR (7 rounds) clearly outnumber DRV (3 rounds).
    assert pairs["Aston Martin"] == sorted(["ALO", "STR"])


def test_pair_teammates_skips_constructor_with_one_driver():
    results = [_race_row(r, "VER", "Red Bull Racing", 1) for r in range(1, 5)]
    pairs = sh.pair_teammates(results)
    assert "Red Bull Racing" not in pairs


# ----------------------------------------------------------------------
# H2H counting — DNF exclusion
# ----------------------------------------------------------------------
def test_race_h2h_excludes_dnf_round():
    race_results = [
        _race_row(1, "AAA", "Team", 1, classified=True),
        _race_row(1, "BBB", "Team", 2, classified=True),
        _race_row(2, "AAA", "Team", None, classified=False, status="DNF"),
        _race_row(2, "BBB", "Team", 3, classified=True),
        _race_row(3, "AAA", "Team", 4, classified=True),
        _race_row(3, "BBB", "Team", 2, classified=True),
    ]
    race_by_round = tb._rows_by_round(race_results)
    wins = tb._race_h2h(race_by_round, "AAA", "BBB")
    # Round 1: AAA ahead. Round 2: excluded (AAA DNF). Round 3: BBB ahead.
    assert wins == [1, 1]


def test_quali_h2h_counts_every_round_both_set_a_time():
    quali_results = [
        _quali_row(1, "AAA", "Team", 1, q1=90.0, q2=89.0, q3=88.0),
        _quali_row(1, "BBB", "Team", 2, q1=91.0, q2=90.0, q3=89.0),
        _quali_row(2, "AAA", "Team", 3, q1=92.0, q2=91.5),
        _quali_row(2, "BBB", "Team", 1, q1=90.5, q2=90.0),
    ]
    quali_by_round = tb._rows_by_round(quali_results)
    wins = tb._quali_h2h(quali_by_round, "AAA", "BBB")
    assert wins == [1, 1]


# ----------------------------------------------------------------------
# Deepest-common-segment quali gap
# ----------------------------------------------------------------------
def test_deepest_common_gap_prefers_q3():
    row_a = {"q1_s": 90.0, "q2_s": 89.0, "q3_s": 88.0}
    row_b = {"q1_s": 91.0, "q2_s": 90.0, "q3_s": 88.5}
    gap = tb._deepest_common_gap(row_a, row_b)
    assert gap == pytest.approx(0.5, abs=1e-9)  # b - a from Q3


def test_deepest_common_gap_falls_back_when_one_missing_q3():
    """Driver A got knocked out in Q2 (no Q3 time) -> fall back to Q2."""
    row_a = {"q1_s": 90.0, "q2_s": 89.5, "q3_s": None}
    row_b = {"q1_s": 91.0, "q2_s": 90.0, "q3_s": 88.5}
    gap = tb._deepest_common_gap(row_a, row_b)
    assert gap == pytest.approx(0.5, abs=1e-9)  # b - a from Q2


def test_deepest_common_gap_falls_back_to_q1_when_both_missing_deeper():
    row_a = {"q1_s": 90.0, "q2_s": None, "q3_s": None}
    row_b = {"q1_s": 91.0, "q2_s": None, "q3_s": None}
    gap = tb._deepest_common_gap(row_a, row_b)
    assert gap == pytest.approx(1.0, abs=1e-9)


def test_deepest_common_gap_none_when_no_common_segment():
    row_a = {"q1_s": 90.0, "q2_s": None, "q3_s": None}
    row_b = {"q1_s": None, "q2_s": 90.0, "q3_s": None}
    assert tb._deepest_common_gap(row_a, row_b) is None


def test_avg_quali_gap_averages_across_rounds():
    quali_results = [
        _quali_row(1, "AAA", "Team", 1, q1=90.0, q2=89.0, q3=88.0),
        _quali_row(1, "BBB", "Team", 2, q1=91.0, q2=90.0, q3=88.5),
        _quali_row(2, "AAA", "Team", 3, q1=92.0, q2=91.5),
        _quali_row(2, "BBB", "Team", 1, q1=90.5, q2=90.0),
    ]
    quali_by_round = tb._rows_by_round(quali_results)
    avg_gap, rounds_counted = tb._avg_quali_gap(quali_by_round, "AAA", "BBB")
    # Round 1: Q3 gap = 88.5 - 88.0 = 0.5. Round 2: Q2 gap = 90.0 - 91.5 = -1.5.
    assert rounds_counted == 2
    assert avg_gap == pytest.approx((0.5 + (-1.5)) / 2, abs=1e-9)


# ----------------------------------------------------------------------
# Full payload assembly
# ----------------------------------------------------------------------
def test_build_payload_from_results_alphabetical_teams():
    race_results = (
        [_race_row(1, "AAA", "Zeta Team", 1), _race_row(1, "BBB", "Zeta Team", 2)]
        + [_race_row(1, "CCC", "Alpha Team", 3), _race_row(1, "DDD", "Alpha Team", 4)]
    )
    quali_results = (
        [_quali_row(1, "AAA", "Zeta Team", 1, q3=88.0), _quali_row(1, "BBB", "Zeta Team", 2, q3=88.5)]
        + [_quali_row(1, "CCC", "Alpha Team", 3, q3=89.0), _quali_row(1, "DDD", "Alpha Team", 4, q3=89.5)]
    )
    payload = tb.build_payload_from_results(race_results, quali_results)
    teams = [t["team"] for t in payload["teams"]]
    assert teams == sorted(teams)
    assert teams == ["Alpha Team", "Zeta Team"]

    zeta = next(t for t in payload["teams"] if t["team"] == "Zeta Team")
    assert zeta["driver_a"] == "AAA"
    assert zeta["driver_b"] == "BBB"
    assert zeta["race_h2h"] == [1, 0]
    assert zeta["quali_h2h"] == [1, 0]
    assert zeta["rounds_counted"] == 1
    assert zeta["avg_quali_gap_s"] == pytest.approx(0.5, abs=1e-9)


def test_data_call_raises_when_no_teams(monkeypatch):
    monkeypatch.setattr(tb, "season_cached_or_generate", lambda year, data_type, generator: {"teams": []})
    with pytest.raises(DataNotAvailableError):
        tb.TeammateBattlePlot()(2025)


# ----------------------------------------------------------------------
# fetch_season_results row normalization (mocked HTTP layer, no network)
# ----------------------------------------------------------------------
def _mrdata_results_page(round_nr=1, total=1):
    return {
        "MRData": {
            "total": str(total),
            "RaceTable": {
                "Races": [
                    {
                        "round": str(round_nr),
                        "Results": [
                            {
                                "position": "1",
                                "positionText": "1",
                                "grid": "1",
                                "status": "Finished",
                                "Driver": {"code": "VER", "givenName": "Max", "familyName": "Verstappen"},
                                "Constructor": {"name": "Red Bull Racing"},
                            },
                            {
                                "position": "20",
                                "positionText": "R",
                                "grid": "5",
                                "status": "Retired",
                                "Driver": {"code": "HAM", "givenName": "Lewis", "familyName": "Hamilton"},
                                "Constructor": {"name": "Ferrari"},
                            },
                        ],
                    }
                ]
            },
        }
    }


def _mrdata_qualifying_page(round_nr=1, total=1):
    return {
        "MRData": {
            "total": str(total),
            "RaceTable": {
                "Races": [
                    {
                        "round": str(round_nr),
                        "QualifyingResults": [
                            {
                                "position": "1",
                                "Driver": {"code": "VER", "givenName": "Max", "familyName": "Verstappen"},
                                "Constructor": {"name": "Red Bull Racing"},
                                "Q1": "1:20.123",
                                "Q2": "1:19.456",
                                "Q3": "1:18.789",
                            },
                            {
                                "position": "15",
                                "Driver": {"code": "HAM", "givenName": "Lewis", "familyName": "Hamilton"},
                                "Constructor": {"name": "Ferrari"},
                                "Q1": "1:21.000",
                            },
                        ],
                    }
                ]
            },
        }
    }


def test_fetch_season_results_normalizes_race_rows():
    mock_client = MagicMock()
    mock_client.BASE_URL = "https://api.jolpi.ca/ergast/f1"
    mock_client._get_json.return_value = _mrdata_results_page()

    rows = sh.fetch_season_results(2025, "race", client=mock_client)
    assert len(rows) == 2
    ver = next(r for r in rows if r["driver_code"] == "VER")
    assert ver["round"] == 1
    assert ver["constructor"] == "Red Bull Racing"
    assert ver["position"] == 1
    assert ver["classified"] is True

    ham = next(r for r in rows if r["driver_code"] == "HAM")
    assert ham["classified"] is False  # positionText "R" is not numeric
    assert ham["position"] == 20


def test_fetch_season_results_normalizes_qualifying_rows_with_parsed_times():
    mock_client = MagicMock()
    mock_client.BASE_URL = "https://api.jolpi.ca/ergast/f1"
    mock_client._get_json.return_value = _mrdata_qualifying_page()

    rows = sh.fetch_season_results(2025, "qualifying", client=mock_client)
    ver = next(r for r in rows if r["driver_code"] == "VER")
    assert ver["q1_s"] == pytest.approx(80.123, abs=1e-3)
    assert ver["q2_s"] == pytest.approx(79.456, abs=1e-3)
    assert ver["q3_s"] == pytest.approx(78.789, abs=1e-3)

    ham = next(r for r in rows if r["driver_code"] == "HAM")
    assert ham["q1_s"] == pytest.approx(81.0, abs=1e-3)
    assert ham["q2_s"] is None
    assert ham["q3_s"] is None


def test_fetch_season_results_paginates_until_total():
    mock_client = MagicMock()
    mock_client.BASE_URL = "https://api.jolpi.ca/ergast/f1"
    # Two pages: total=2 results across 2 rounds, 1 result each (limit=100 means
    # a single page normally suffices, but we still exercise the pagination
    # loop condition by asserting call count against a small `total`).
    page1 = _mrdata_results_page(round_nr=1, total=2)
    mock_client._get_json.return_value = page1

    rows = sh.fetch_season_results(2025, "race", client=mock_client)
    assert mock_client._get_json.call_count == 1
    assert len(rows) == 2  # first (only) page already satisfies total=2 results


def test_fetch_season_results_rejects_unknown_kind():
    with pytest.raises(ValueError):
        sh.fetch_season_results(2025, "practice")  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Plot render
# ----------------------------------------------------------------------
def test_plot_renders_png(tmp_path, monkeypatch):
    payload = {
        "teams": [
            {
                "team": "Alpha Team", "color": "#FF0000",
                "driver_a": "AAA", "driver_b": "BBB",
                "quali_h2h": [3, 2], "race_h2h": [4, 1],
                "avg_quali_gap_s": 0.18, "rounds_counted": 5,
            },
            {
                "team": "Zeta Team", "color": "#00FF00",
                "driver_a": "CCC", "driver_b": "DDD",
                "quali_h2h": [0, 0], "race_h2h": [0, 0],
                "avg_quali_gap_s": None, "rounds_counted": 0,
            },
        ]
    }
    monkeypatch.chdir(tmp_path)
    out = tb.TeammateBattlePlot._render(payload, 2025)
    assert os.path.isfile(out)
    assert out.endswith(".png")
