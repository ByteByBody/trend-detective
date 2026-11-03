import sys
import types
import warnings

import numpy as np

from trend_detective.analysis import decomp
from trend_detective.data import sample


class _FakeSTL:
    """Stand-in for ``statsmodels.tsa.seasonal.STL``.

    Its "seasonal" component is the per-calendar-month mean, so subtracting it
    removes the cycle exactly. Raises like the real STL on series shorter than
    two periods, and refuses NaN input (the real STL does too).
    """

    def __init__(self, series, period=12, robust=True):
        self.series = np.asarray(series, dtype=float)
        self.period = period

    def fit(self):
        if np.isnan(self.series).any():
            raise ValueError("STL cannot handle NaN")
        if self.series.size < 2 * self.period:
            raise ValueError("series too short for STL")
        m = np.arange(self.series.size) % self.period
        seasonal = np.zeros_like(self.series)
        for k in range(self.period):
            seasonal[m == k] = self.series[m == k].mean()
        seasonal -= seasonal.mean()
        return types.SimpleNamespace(seasonal=seasonal)


def _install_fake_statsmodels(monkeypatch):
    pkg = types.ModuleType("statsmodels")
    tsa = types.ModuleType("statsmodels.tsa")
    seasonal = types.ModuleType("statsmodels.tsa.seasonal")
    seasonal.STL = _FakeSTL
    tsa.seasonal = seasonal
    pkg.tsa = tsa
    monkeypatch.setitem(sys.modules, "statsmodels", pkg)
    monkeypatch.setitem(sys.modules, "statsmodels.tsa", tsa)
    monkeypatch.setitem(sys.modules, "statsmodels.tsa.seasonal", seasonal)


def _month_mean_std(values: np.ndarray) -> float:
    """Std of the per-calendar-month means over years (seasonal amplitude)."""
    reshaped = values.reshape(-1, 12)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return float(np.nanstd(np.nanmean(reshaped, axis=0)))


def _seasonal_amplitude(values: np.ndarray) -> float:
    """Seasonal amplitude across the whole grid (axis 0 = time)."""
    reshaped = values.reshape(-1, 12, *values.shape[1:])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return float(np.nanstd(np.nanmean(reshaped, axis=0)))


def test_removes_seasonal_cycle():
    da, _ = sample.generate_land_grid(seed=3, ny=4, nx=4, n_months=120, autocorr=0.0)
    out = decomp.deseasonalize(da, method="climatology")
    assert _seasonal_amplitude(out.values) < 0.1 * _seasonal_amplitude(da.values)


def test_stl_deseasonalizes_a_clean_pixel(monkeypatch):
    da, _ = sample.generate_land_grid(seed=11, ny=3, nx=3, n_months=120, autocorr=0.0)
    _install_fake_statsmodels(monkeypatch)
    out = decomp.deseasonalize(da, method="stl")
    assert _month_mean_std(out.values[:, 0, 0]) < 0.2 * _month_mean_std(da.values[:, 0, 0])


def test_stl_falls_back_to_climatology_for_gappy_pixel(monkeypatch):
    # One pixel loses every January. The STL path must not leave its seasonal
    # cycle in place (the old bug), because the pixel is still trend-tested.
    da, _ = sample.generate_land_grid(seed=9, ny=3, nx=3, n_months=120, autocorr=0.0)
    values = da.values.copy()
    values[0::12, 1, 1] = np.nan
    da = da.copy(data=values)
    _install_fake_statsmodels(monkeypatch)
    out = decomp.deseasonalize(da, method="stl")
    assert _month_mean_std(out.values[:, 1, 1]) < 0.2 * _month_mean_std(values[:, 1, 1])
    assert np.isnan(out.values[0::12, 1, 1]).all()


def test_stl_short_series_falls_back_without_raising(monkeypatch):
    da, _ = sample.generate_land_grid(seed=10, ny=3, nx=3, n_months=12, autocorr=0.0)
    _install_fake_statsmodels(monkeypatch)
    out = decomp.deseasonalize(da, method="stl")
    assert out.shape == da.shape
    assert np.isfinite(out.values).all()


def test_unknown_method_raises():
    da, _ = sample.generate_land_grid(seed=12, ny=2, nx=2, n_months=24)
    try:
        decomp.deseasonalize(da, method="nope")
    except ValueError:
        return
    raise AssertionError("expected ValueError for an unknown method")


def test_shape_and_nans_preserved():
    da, _ = sample.generate_land_grid(seed=4, ny=4, nx=4, n_months=60, gap_fraction=0.2)
    out = decomp.deseasonalize(da, method="climatology")
    assert out.shape == da.shape
    assert np.array_equal(np.isnan(out.values), np.isnan(da.values))


def test_all_nan_calendar_month_at_a_pixel_does_not_raise():
    # Wipe every January (t mod 12 == 0) at one pixel: that calendar month has
    # no data there, so its climatology is NaN. Must not raise or impute.
    da, _ = sample.generate_land_grid(seed=8, ny=3, nx=3, n_months=60)
    values = da.values.copy()
    values[0::12, 1, 1] = np.nan
    da = da.copy(data=values)
    out = decomp.deseasonalize(da, method="climatology")
    assert np.isnan(out.values[0::12, 1, 1]).all()
    assert np.isfinite(out.values[1::12, 1, 1]).all()
