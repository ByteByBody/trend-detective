import numpy as np

from trend_detective.analysis import significance


def test_bh_threshold_matches_definition():
    p = np.array([0.001, 0.008, 0.02, 0.04, 0.2, 0.5, 0.9])
    mask, thr = significance.benjamini_hochberg(p, q=0.05)
    m = p.size
    order = np.argsort(p)
    ok = np.nonzero(p[order] <= (np.arange(1, m + 1) / m) * 0.05)[0]
    expected = p[order][ok.max()] if ok.size else 0.0
    assert abs(thr - expected) < 1e-12
    assert mask.sum() == (p <= thr).sum()


def test_false_discovery_rate_controlled_under_null():
    rng = np.random.default_rng(0)
    m, reps, flagged = 2000, 50, 0
    for _ in range(reps):
        _, thr = significance.benjamini_hochberg(rng.uniform(size=m), q=0.05)
        flagged += int((rng.uniform(size=m) <= thr).sum())
    assert flagged / (reps * m) < 0.05


def test_nans_are_never_flagged():
    p = np.array([0.001, np.nan, 0.9])
    mask, _ = significance.benjamini_hochberg(p, q=0.05)
    assert mask[1] == False  # noqa: E712


def test_pvalue_ties_are_stable():
    p = np.array([0.01, 0.01, 0.01, 0.9, 0.9])
    mask1, thr1 = significance.benjamini_hochberg(p, q=0.05)
    mask2, thr2 = significance.benjamini_hochberg(p, q=0.05)
    assert thr1 == thr2 and np.array_equal(mask1, mask2)
