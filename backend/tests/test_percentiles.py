"""p50 / p95 latency claims — structural possibility, not value matching.

The dashboard computes percentiles in SQL:

    percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50_ms
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms
                                                  -- backend/dashboard/db.py:88-89

These tests do NOT check that p95 equals some specific number. They check that the
*shape* of the claim is sound — i.e. is it even possible for the dashboard to show
p95 < p50? (No.) We mirror PostgreSQL's `percentile_cont` (continuous percentile,
linear interpolation between the two nearest ranks) and assert the invariants that
must hold for ANY dataset.
"""
import random

import pytest


def percentile_cont(values, q):
    """Mirror of PostgreSQL percentile_cont(q) WITHIN GROUP (ORDER BY values).

    Sort, then linearly interpolate at rank = q*(n-1). Identical algorithm to PG,
    so any invariant proven here is an invariant of the dashboard's numbers.
    """
    if not values:
        return None
    xs = sorted(values)
    n = len(xs)
    if n == 1:
        return float(xs[0])
    rank = q * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def test_known_case_matches_continuous_definition():
    # [10,20,30,40]: p50 rank = 0.5*3 = 1.5 -> between 20 and 30 -> 25.0
    assert percentile_cont([10, 20, 30, 40], 0.5) == 25.0
    # endpoints are exact min/max
    assert percentile_cont([10, 20, 30, 40], 0.0) == 10.0
    assert percentile_cont([10, 20, 30, 40], 1.0) == 40.0


def test_single_sample_p50_equals_p95():
    # With one data point the dashboard must show p50 == p95 == that value.
    assert percentile_cont([123.0], 0.5) == percentile_cont([123.0], 0.95) == 123.0


@pytest.mark.parametrize("seed", range(50))
def test_p95_never_below_p50(seed):
    # The core claim: across arbitrary latency samples, p95 >= p50 ALWAYS.
    rng = random.Random(seed)
    values = [rng.uniform(1, 5000) for _ in range(rng.randint(2, 500))]
    p50 = percentile_cont(values, 0.5)
    p95 = percentile_cont(values, 0.95)
    assert p95 >= p50


@pytest.mark.parametrize("seed", range(50))
def test_percentiles_stay_within_min_max(seed):
    rng = random.Random(seed)
    values = [rng.uniform(1, 5000) for _ in range(rng.randint(2, 500))]
    lo, hi = min(values), max(values)
    for q in (0.5, 0.95):
        p = percentile_cont(values, q)
        assert lo <= p <= hi


def test_percentile_is_monotonic_in_q():
    # Higher quantile -> higher (or equal) value, for any dataset.
    values = [random.Random(7).uniform(1, 1000) for _ in range(100)]
    qs = [0.0, 0.25, 0.5, 0.75, 0.95, 1.0]
    results = [percentile_cont(values, q) for q in qs]
    assert results == sorted(results)


def test_empty_dataset_returns_none():
    # No successful requests in the window -> SQL returns NULL; mirror returns None.
    assert percentile_cont([], 0.5) is None
