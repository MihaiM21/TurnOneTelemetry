"""Offline unit tests for radar-axis normalization (V2).

Exercise the percentile / absolute scaling, direction inversion, None
passthrough and degenerate-field rules with no network dependency.
"""
from __future__ import annotations

import pytest

from src.services.analysis.v2 import _radar_scale as scale


# ----------------------------------------------------------------------
# percentile_rank
# ----------------------------------------------------------------------
def test_percentile_rank_monotonic():
    pop = [1.0, 2.0, 3.0, 4.0]
    assert scale.percentile_rank(1.0, pop) < scale.percentile_rank(4.0, pop)
    assert scale.percentile_rank(4.0, pop) == pytest.approx(87.5)  # below 3 + half of 1


def test_percentile_rank_degenerate_field_is_neutral():
    assert scale.percentile_rank(5.0, [5.0]) == 50.0          # single value
    assert scale.percentile_rank(5.0, [5.0, 5.0, 5.0]) == 50.0  # all equal


def test_percentile_rank_ties_share_midpoint():
    pop = [10.0, 10.0, 20.0, 20.0]
    # Two below, two equal -> (2 + 0.5*2)/4 = 75
    assert scale.percentile_rank(20.0, pop) == pytest.approx(75.0)


# ----------------------------------------------------------------------
# scale_value
# ----------------------------------------------------------------------
def test_scale_value_none_passthrough():
    assert scale.scale_value(None, [1.0, 2.0]) is None


def test_scale_value_bounds():
    pop = [1.0, 2.0, 3.0, 4.0, 5.0]
    for v in pop:
        s = scale.scale_value(v, pop)
        assert 0.0 <= s <= 100.0


def test_scale_value_direction_inversion():
    pop = [60.0, 70.0, 80.0]  # e.g. lap times: lower is better
    higher = scale.scale_value(60.0, pop, higher_is_better=True)
    lower = scale.scale_value(60.0, pop, higher_is_better=False)
    # The fastest lap (60) should score high when lower-is-better, low otherwise.
    assert lower > higher
    assert lower == pytest.approx(100.0 - higher)


def test_scale_value_absolute_band_clamps():
    # ratio band lo=0.3 hi=0.75
    assert scale.scale_value(0.30, [], method="absolute", lo=0.3, hi=0.75) == 0.0
    assert scale.scale_value(0.75, [], method="absolute", lo=0.3, hi=0.75) == 100.0
    assert scale.scale_value(0.10, [], method="absolute", lo=0.3, hi=0.75) == 0.0   # clamp low
    assert scale.scale_value(0.90, [], method="absolute", lo=0.3, hi=0.75) == 100.0  # clamp high
    mid = scale.scale_value(0.525, [], method="absolute", lo=0.3, hi=0.75)
    assert mid == pytest.approx(50.0)


def test_scale_value_absolute_missing_band_is_neutral():
    assert scale.scale_value(0.5, [], method="absolute") == 50.0


# ----------------------------------------------------------------------
# scale_population
# ----------------------------------------------------------------------
def test_scale_population_preserves_none_and_length():
    values = [1.0, None, 3.0]
    out = scale.scale_population(values, higher_is_better=True)
    assert len(out) == 3
    assert out[1] is None
    assert out[2] > out[0]
