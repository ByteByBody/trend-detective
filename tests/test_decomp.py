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
