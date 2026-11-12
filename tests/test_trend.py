import warnings

import numpy as np
import xarray as xr

from trend_detective.analysis import trend


def _series(values) -> xr.DataArray:
    n = len(values)
    return xr.DataArray(
        np.asarray(values, dtype=float).reshape(n, 1, 1),
        dims=("time", "lat", "lon"),
        coords={"time": np.arange(n), "lat": [0.0], "lon": [0.0]},
    )


def test_theilsen_recovers_known_slope():
    x = np.arange(120)
    slope, _ = trend.trend_test(_series(3.0 + 0.5 * x))
    assert abs(float(slope.values[0, 0]) - 0.5) < 1e-9


def test_white_noise_is_mostly_not_significant():
    rng = np.random.default_rng(0)
    hits = 0
    for _ in range(200):
        _, p = trend.trend_test(_series(rng.normal(size=120)))
        hits += int(float(p.values[0, 0]) < 0.05)
    assert hits < 30


def test_autocorrelated_series_not_over_detected():
    rng = np.random.default_rng(1)
    hits_plain = hits_corrected = 0
    for _ in range(100):
        e = rng.normal(size=200)
        y = np.empty_like(e)
        y[0] = e[0]
        for t in range(1, 200):
            y[t] = 0.8 * y[t - 1] + e[t]  # strong AR(1), no real trend
        _, p_plain = trend.trend_test(_series(y), autocorr_correct=False)
        _, p_corr = trend.trend_test(_series(y), autocorr_correct=True)
        hits_plain += int(float(p_plain.values[0, 0]) < 0.05)
        hits_corrected += int(float(p_corr.values[0, 0]) < 0.05)
    assert hits_corrected < hits_plain


def test_constant_series_is_nan_not_significant():
    _, p = trend.trend_test(_series(np.full(50, 7.0)))
    assert np.isnan(float(p.values[0, 0]))


def test_grid_paths_match_per_pixel_reference():
    rng = np.random.default_rng(2)
    x = rng.normal(size=(60, 7, 5))
    x[rng.random(x.shape) < 0.1] = np.nan  # mix in missing months
    flat = x.reshape(60, -1)

    ref_slope = np.array([trend._sen_slope_1d(flat[:, p]) for p in range(flat.shape[1])])
    ref_p = np.array(
        [trend._mk_pvalue_1d(flat[:, p], True) for p in range(flat.shape[1])]
    )
    assert np.allclose(trend._sen_slopes(flat), ref_slope, equal_nan=True)
    assert np.allclose(trend._mk_pvalues(flat, True), ref_p, equal_nan=True)


def test_a_pixel_with_no_usable_months_is_nan_without_a_warning():
    # Permanently masked cells (soil moisture or snow over open ocean) have zero
    # finite months. The slope must be NaN and the all-NaN median must not warn:
    # a wall of RuntimeWarnings hides the next real error.
    flat = np.full((24, 3), np.nan)
    flat[:, 1] = 0.0  # a sane pixel next to the dead ones

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = trend._sen_slopes(flat)

    assert [c.category.__name__ for c in caught] == []
    assert np.isnan(out[0]) and np.isnan(out[2])
    assert float(out[1]) == 0.0


def test_a_pixel_with_one_usable_month_is_nan_without_a_warning():
    flat = np.full((24, 1), np.nan)
    flat[5, 0] = 3.0  # one month is not a trend

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = trend._sen_slopes(flat)

    assert [c.category.__name__ for c in caught] == []
    assert np.isnan(out[0])


def test_missing_months_keep_their_true_time_spacing():
    # Pairs are formed on original indices, so a gap contributes its real
    # distance. Dropping NaNs would compress the axis and treble this slope.
    flat = np.array([[0.0], [np.nan], [np.nan], [3.0]])
    out = trend._sen_slopes(flat)
    assert float(out[0]) == 1.0


def test_nan_free_fast_path_matches_nan_aware_path():
    # The fast path skips the finite check and uses np.median; it must agree
    # with the general per-pixel routines on data that has no gaps.
    rng = np.random.default_rng(3)
    flat = rng.normal(size=(48, 40))
    ref_slope = np.array([trend._sen_slope_1d(flat[:, p]) for p in range(flat.shape[1])])
    ref_p = np.array(
        [trend._mk_pvalue_1d(flat[:, p], True) for p in range(flat.shape[1])]
    )
    assert np.allclose(trend._sen_slopes(flat), ref_slope)
    assert np.allclose(trend._mk_pvalues(flat, True), ref_p)
