"""Offline unit tests for ``_pick_clean_fastest_lap`` (V1 track comparison).

Uses a minimal fake laps object mimicking FastF1's ``Laps`` chaining API
(``pick_wo_box`` / ``pick_accurate`` / ``pick_fastest`` / ``len``) so the
filtering logic is exercised with no FastF1 session or network access.
"""
from __future__ import annotations

import pytest

from src.services.analysis.v1.track_comparison import _pick_clean_fastest_lap


class _FakeLaps:
    def __init__(self, name, wo_box=None, accurate=None, accurate_raises=False, fastest="clean-fastest"):
        self.name = name
        self._wo_box = wo_box
        self._accurate = accurate
        self._accurate_raises = accurate_raises
        self._fastest = fastest

    def __len__(self):
        return 0 if self.name == "empty" else 1

    def pick_wo_box(self):
        return self._wo_box if self._wo_box is not None else self

    def pick_accurate(self):
        if self._accurate_raises:
            raise AttributeError("pick_accurate not supported")
        return self._accurate if self._accurate is not None else self

    def pick_fastest(self):
        return self._fastest


def test_picks_fastest_from_clean_laps_when_available():
    clean = _FakeLaps("clean", fastest="clean-lap")
    raw = _FakeLaps("raw", wo_box=clean, fastest="raw-lap")
    assert _pick_clean_fastest_lap(raw) == "clean-lap"


def test_falls_back_to_unfiltered_when_clean_set_is_empty():
    empty = _FakeLaps("empty")
    raw = _FakeLaps("raw", wo_box=empty, fastest="raw-lap")
    assert _pick_clean_fastest_lap(raw) == "raw-lap"


def test_tolerates_pick_accurate_not_supported():
    wo_box = _FakeLaps("wo_box", accurate_raises=True, fastest="wo-box-lap")
    raw = _FakeLaps("raw", wo_box=wo_box, fastest="raw-lap")
    assert _pick_clean_fastest_lap(raw) == "wo-box-lap"


def test_accurate_filter_applied_after_wo_box():
    accurate = _FakeLaps("accurate", fastest="accurate-lap")
    wo_box = _FakeLaps("wo_box", accurate=accurate, fastest="wo-box-lap")
    raw = _FakeLaps("raw", wo_box=wo_box, fastest="raw-lap")
    assert _pick_clean_fastest_lap(raw) == "accurate-lap"
