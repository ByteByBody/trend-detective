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
