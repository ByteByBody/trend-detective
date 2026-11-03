import numpy as np

from trend_detective.analysis import decomp
from trend_detective.data import sample


def _seasonal_amplitude(values: np.ndarray) -> float:
    """Std of the per-calendar-month climatology over years (axis 0 = time)."""
    reshaped = values.reshape(-1, 12, *values.shape[1:])
    return float(np.nanstd(np.nanmean(reshaped, axis=0)))


def test_removes_seasonal_cycle():
    da, _ = sample.generate_land_grid(seed=3, ny=4, nx=4, n_months=120, autocorr=0.0)
    out = decomp.deseasonalize(da, method="climatology")
    assert _seasonal_amplitude(out.values) < 0.1 * _seasonal_amplitude(da.values)


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
