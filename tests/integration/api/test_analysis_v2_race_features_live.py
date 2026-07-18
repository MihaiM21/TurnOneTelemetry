"""Live integration tests for the 5 new V2 race-analysis features.

These hit the real F1 livetiming network (via ``F1StaticClient`` /
``SessionDataStore``) for a fixed, known-good session (2025 GP1 Race, plus
FP1 for session-weather which supports every session type). They are marked
``slow`` — per this repo's pytest marker convention (see ``pyproject.toml``,
``[tool.pytest.ini_options]`` -> ``markers``) — so they're excluded from the
default fast loop (``make test-fast`` runs ``-m "not slow"``) and only run
when explicitly requested, e.g.:

    pytest tests/integration/api/test_analysis_v2_race_features_live.py -v -o addopts=

MongoDB/Redis are not required to be running: the cache layers degrade
gracefully to "fetch from livetiming every time" when unavailable.
"""
import pytest

YEAR = 2025
GP = 1
SESSION_RACE = "R"
SESSION_WEATHER_SESSION = "FP1"

PNG_MAGIC = b"\x89PNG"


pytestmark = pytest.mark.slow


def _get(client, auth_headers, path, params):
    return client.get(path, params=params, headers=auth_headers("standard"))


# ---------------------------------------------------------------------------
# Position Changes (Race/Sprint only)
# ---------------------------------------------------------------------------

def test_position_changes_plot(client, disable_rate_limit, auth_headers):
    resp = _get(
        client, auth_headers, "/api/v2/position-changes-plot",
        {"year": YEAR, "gp": GP, "session": SESSION_RACE},
    )
    assert resp.status_code == 200, resp.text
    assert resp.content[:4] == PNG_MAGIC


def test_position_changes_data(client, disable_rate_limit, auth_headers):
    resp = _get(
        client, auth_headers, "/api/v2/position-changes-data",
        {"year": YEAR, "gp": GP, "session": SESSION_RACE},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert 18 <= len(body) <= 20
    assert set(body[0].keys()) >= {"driver", "team", "color", "start_pos", "end_pos", "positions"}


# ---------------------------------------------------------------------------
# Race Gaps (Race/Sprint only; reference='leader'|'average')
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reference", ["leader", "average"])
def test_race_gaps_plot(client, disable_rate_limit, auth_headers, reference):
    resp = _get(
        client, auth_headers, "/api/v2/race-gaps-plot",
        {"year": YEAR, "gp": GP, "session": SESSION_RACE, "reference": reference},
    )
    assert resp.status_code == 200, resp.text
    assert resp.content[:4] == PNG_MAGIC


@pytest.mark.parametrize("reference", ["leader", "average"])
def test_race_gaps_data(client, disable_rate_limit, auth_headers, reference):
    resp = _get(
        client, auth_headers, "/api/v2/race-gaps-data",
        {"year": YEAR, "gp": GP, "session": SESSION_RACE, "reference": reference},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert 18 <= len(body) <= 20
    assert set(body[0].keys()) >= {"driver", "team", "color", "laps"}


# ---------------------------------------------------------------------------
# Tyre Degradation (Race/Sprint only)
# ---------------------------------------------------------------------------

def test_tyre_degradation_plot(client, disable_rate_limit, auth_headers):
    resp = _get(
        client, auth_headers, "/api/v2/tyre-degradation-plot",
        {"year": YEAR, "gp": GP, "session": SESSION_RACE},
    )
    assert resp.status_code == 200, resp.text
    assert resp.content[:4] == PNG_MAGIC


def test_tyre_degradation_data(client, disable_rate_limit, auth_headers):
    resp = _get(
        client, auth_headers, "/api/v2/tyre-degradation-data",
        {"year": YEAR, "gp": GP, "session": SESSION_RACE},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) > 0
    assert set(body[0].keys()) >= {"compound", "color", "points", "deg_rate_s_per_lap", "r_squared", "n_points"}


# ---------------------------------------------------------------------------
# Pit Strategy (Race/Sprint only)
# ---------------------------------------------------------------------------

def test_pit_strategy_plot(client, disable_rate_limit, auth_headers):
    resp = _get(
        client, auth_headers, "/api/v2/pit-strategy-plot",
        {"year": YEAR, "gp": GP, "session": SESSION_RACE},
    )
    assert resp.status_code == 200, resp.text
    assert resp.content[:4] == PNG_MAGIC


def test_pit_strategy_data(client, disable_rate_limit, auth_headers):
    resp = _get(
        client, auth_headers, "/api/v2/pit-strategy-data",
        {"year": YEAR, "gp": GP, "session": SESSION_RACE},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict)
    assert set(body.keys()) >= {"stops", "undercuts", "summary", "free_changes"}


# ---------------------------------------------------------------------------
# Session Weather (all session types)
# ---------------------------------------------------------------------------

def test_session_weather_plot(client, disable_rate_limit, auth_headers):
    resp = _get(
        client, auth_headers, "/api/v2/session-weather-plot",
        {"year": YEAR, "gp": GP, "session": SESSION_WEATHER_SESSION},
    )
    assert resp.status_code == 200, resp.text
    assert resp.content[:4] == PNG_MAGIC


def test_session_weather_data(client, disable_rate_limit, auth_headers):
    resp = _get(
        client, auth_headers, "/api/v2/session-weather-data",
        {"year": YEAR, "gp": GP, "session": SESSION_WEATHER_SESSION},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict)
    assert set(body.keys()) >= {"weather", "track_status_periods", "race_control"}
