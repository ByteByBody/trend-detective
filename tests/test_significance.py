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


def test_false_discovery_rate_controlled_under_mixed_signals():
    # 200 true effects with very small p-values amid 1800 nulls. A working BH
    # must (a) recover nearly all true effects and (b) keep the share of false
    # discoveries among everything it flags at or below q. The old test used
    # only nulls, where the threshold is ~0 and any implementation passes.
    rng = np.random.default_rng(1)
    m_signal, m_null = 200, 1800
    p = np.concatenate(
        [rng.uniform(0.0, 1e-4, m_signal), rng.uniform(0.0, 1.0, m_null)]
    )
    mask, thr = significance.benjamini_hochberg(p, q=0.05)

    assert mask[:m_signal].sum() >= 0.9 * m_signal
    assert thr > 0
    false_discoveries = int(mask[m_signal:].sum())
    assert false_discoveries / max(1, int(mask.sum())) <= 0.05


def test_nans_are_never_flagged():
    p = np.array([0.001, np.nan, 0.9])
    mask, _ = significance.benjamini_hochberg(p, q=0.05)
    assert mask[1] == False  # noqa: E712


def test_all_nan_or_empty_input_returns_empty_mask():
    for p in (np.array([np.nan, np.nan]), np.array([])):
        mask, thr = significance.benjamini_hochberg(p)
        assert not mask.any()
        assert thr == 0.0


def test_pvalue_ties_keep_the_largest_qualifying_rank():
    # Three tied small p-values; BH includes every one of them. The assertion
    # is on the outcome (which pixels survive), not a repeated-call identity.
    p = np.array([0.01, 0.01, 0.01, 0.9, 0.9])
    mask, thr = significance.benjamini_hochberg(p, q=0.05)
    assert thr == 0.01
    assert mask[:3].all()
    assert not mask[3:].any()