"""Mongo plot rows record the livetiming session they were computed from, and a row filed
for a different session is never served back."""
from __future__ import annotations

from src.repositories.mongo import MongoDBManager


class _Collection:
    """Just enough of a pymongo collection for ``add_session_data`` / ``get_session_data``."""

    def __init__(self):
        self.doc = {"gp_id": "2026_JPN", "sessions": []}

    def find_one(self, query, projection=None):
        if projection and "sessions" in projection:
            wanted = query.get("sessions.session_type")
            sessions = [s for s in self.doc["sessions"] if s["session_type"] == wanted]
            return {"sessions": sessions[:1]} if sessions else None
        return self.doc

    def update_one(self, flt, update):
        if "$push" in update:
            for path, value in update["$push"].items():
                if path == "sessions":
                    self.doc["sessions"].append(value)
                else:
                    idx = int(path.split(".")[1])
                    self.doc["sessions"][idx]["data"].append(value)
        if "$set" in update:
            for path, value in update["$set"].items():
                _, s_idx, _, d_idx = path.split(".")
                self.doc["sessions"][int(s_idx)]["data"][int(d_idx)] = value


def _manager(monkeypatch):
    manager = MongoDBManager.__new__(MongoDBManager)
    manager.version = "v2"
    coll = _Collection()
    monkeypatch.setattr(manager, "_get_collection", lambda *a, **k: coll, raising=False)
    return manager, coll


JAPAN = "2026/2026-03-29_Japanese_Grand_Prix/2026-03-28_Qualifying"
AUSTRALIA = "2026/2026-03-08_Australian_Grand_Prix/2026-03-07_Qualifying"


def test_row_written_for_one_session_is_a_miss_for_another(monkeypatch):
    manager, _ = _manager(monkeypatch)
    assert manager.add_session_data("2026_JPN", "Q", "qualifying_results", [{"d": 1}], year=2026, source=AUSTRALIA)

    assert manager.get_session_data("2026_JPN", "Q", "qualifying_results", year=2026, expect_source=JAPAN) is None
    assert manager.get_session_data("2026_JPN", "Q", "qualifying_results", year=2026, expect_source=AUSTRALIA) == [{"d": 1}]


def test_legacy_row_without_a_source_is_still_served(monkeypatch):
    """Rows written before provenance existed cannot be verified; the audit finds the bad ones."""
    manager, _ = _manager(monkeypatch)
    manager.add_session_data("2026_JPN", "Q", "qualifying_results", [{"d": 1}], year=2026)
    assert manager.get_session_data("2026_JPN", "Q", "qualifying_results", year=2026, expect_source=JAPAN) == [{"d": 1}]


def test_rewrite_records_the_new_source(monkeypatch):
    manager, coll = _manager(monkeypatch)
    manager.add_session_data("2026_JPN", "Q", "qualifying_results", [{"d": 1}], year=2026, source=AUSTRALIA)
    manager.add_session_data("2026_JPN", "Q", "qualifying_results", [{"d": 2}], year=2026, source=JAPAN)
    entry = coll.doc["sessions"][0]["data"][0]
    assert entry["source"] == JAPAN and entry["data"] == [{"d": 2}]
