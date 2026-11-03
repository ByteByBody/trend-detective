from trend_detective.analysis import pipeline
from trend_detective.data import sample


def test_correction_reduces_false_positives_and_keeps_true_patch():
    da, gt = sample.generate_land_grid(seed=5, ny=30, nx=60, n_months=240, autocorr=0.5)
    res = pipeline.cry_wolf(da, q=0.05, min_patch=5)
    assert res.n_naive >= res.n_corrected
    naive_fp = int((res.naive_mask.values & ~gt.true_patch_mask).sum())
    corr_fp = int((res.corrected_mask.values & ~gt.true_patch_mask).sum())
    assert corr_fp < naive_fp
    kept = int((res.corrected_mask.values & gt.true_patch_mask).sum())
    assert kept > 0.5 * int(gt.true_patch_mask.sum())


def test_low_valid_fraction_pixel_excluded():
    da, _ = sample.generate_land_grid(seed=6, ny=6, nx=6, n_months=120, gap_fraction=0.5)
    res = pipeline.cry_wolf(da, min_valid=0.8)
    assert res.valid_fraction < 1.0
    assert not res.naive_mask.values.all()


def test_result_shapes_match_input():
    da, _ = sample.generate_land_grid(seed=7, ny=8, nx=8, n_months=120)
    res = pipeline.cry_wolf(da)
    assert res.naive_mask.shape == res.corrected_mask.shape == (8, 8)


def test_insufficient_record_pixels_are_counted_and_never_flagged():
    da, _ = sample.generate_land_grid(seed=6, ny=10, nx=10, n_months=60, gap_fraction=0.45)
    res = pipeline.cry_wolf(da, min_valid=0.8)
    assert res.n_insufficient > 0
    assert not (res.insufficient_mask.values & res.naive_mask.values).any()
    assert not (res.insufficient_mask.values & res.corrected_mask.values).any()


def test_morans_i_reported_and_positive_for_injected_patches():
    da, gt = sample.generate_land_grid(seed=5, ny=30, nx=60, n_months=240, autocorr=0.5)
    res = pipeline.cry_wolf(da)
    # neighbouring pixels inside the true patches share a slope, so the
    # corrected field should be spatially clustered.
    assert res.morans_i > 0.5
    assert res.corrected_mask.values.sum() == gt.true_patch_mask.sum()
