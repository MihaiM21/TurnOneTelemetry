"""Season-scope MongoDB round-trip (offline, mongomock-backed).

Season-wide V2 payloads (teammate battle, season form, season/career radar) are
addressed by the sentinel identifier ``"season"``. Before ``store_season_data_to_mongo``
and the matching read branch existed, the Mongo tier for these features was
unreachable: the reader fell through to ``resolve_event(year, "season")``, which
can never resolve an event, so every season payload was regenerated from jolpica
on each Redis miss. These tests pin the round-trip shut.
"""

import pytest

from src.repositories.plots import (
    SEASON_SESSION_TYPE,
    get_plot_data_from_mongo,
    is_season_identifier,
    season_gp_id,
    store_season_data_to_mongo,
)


@pytest.fixture(autouse=True)
def _fresh_manager():
    """Season docs go in ``{year}_processed_data_v2``; drop the shared manager so
    each test starts from a clean mongomock collection.
    """
    from src.repositories import plots as plots_mod
    from src.repositories.mongo import MongoDBManager

    plots_mod.close_global_db_manager()
    manager = MongoDBManager(year=2023, version="v2")
    manager.db.drop_collection("2023_processed_data_v2")
    yield
    plots_mod.close_global_db_manager()


def test_is_season_identifier_is_case_insensitive():
    assert is_season_identifier("season")
    assert is_season_identifier("Season")
    assert not is_season_identifier("Australian Grand Prix")
    assert not is_season_identifier(5)


def test_season_gp_id_shape():
    assert season_gp_id(2023) == "2023_SEASON"


def test_season_round_trip():
    payload = [{"constructor": "McLaren", "drivers": ["NOR", "PIA"], "score": [12, 10]}]
    assert store_season_data_to_mongo(2023, "teammate_battle", payload) is True

    got = get_plot_data_from_mongo(2023, "season", SEASON_SESSION_TYPE,
                                   "teammate_battle", version="v2")
    assert got is not None
    assert got["data"] == payload
    assert got["metadata"]["gp_id"] == "2023_SEASON"


def test_season_read_does_not_resolve_an_event(monkeypatch):
    """The season branch must short-circuit before event resolution.

    ``resolve_event(year, "season")`` cannot succeed, so if the reader ever
    reaches it again this test fails loudly rather than silently degrading to a
    permanent cache miss.
    """
    import src.ingestion.event_resolver as resolver

    def _boom(*_a, **_kw):
        raise AssertionError("season read must not call resolve_event")

    monkeypatch.setattr(resolver, "resolve_event", _boom)

    store_season_data_to_mongo(2023, "driver_radar_season", {"drivers": []})
    # Empty payload stored above is falsy-but-present; use a real one to assert.
    store_season_data_to_mongo(2023, "season_form_w3", [{"driver": "VER", "form": 1.0}])
    got = get_plot_data_from_mongo(2023, "season", SEASON_SESSION_TYPE,
                                   "season_form_w3", version="v2")
    assert got["data"] == [{"driver": "VER", "form": 1.0}]


def test_multiple_season_data_types_share_one_document():
    store_season_data_to_mongo(2023, "teammate_battle", [{"a": 1}])
    store_season_data_to_mongo(2023, "season_form_w3", [{"b": 2}])
    store_season_data_to_mongo(2023, "driver_radar_season", [{"c": 3}])

    from src.repositories.mongo import MongoDBManager

    manager = MongoDBManager(year=2023, version="v2")
    docs = manager.list_all_gps(year=2023)
    season_docs = [d for d in docs if d["gp_id"] == "2023_SEASON"]
    assert len(season_docs) == 1

    sessions = season_docs[0]["sessions"]
    assert [s["session_type"] for s in sessions] == [SEASON_SESSION_TYPE]
    stored = {d["data_type"] for d in sessions[0]["data"]}
    assert stored == {"teammate_battle", "season_form_w3", "driver_radar_season"}


def test_season_miss_returns_none_not_an_error():
    assert get_plot_data_from_mongo(2023, "season", SEASON_SESSION_TYPE,
                                    "never_generated", version="v2") is None
