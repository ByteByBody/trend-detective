import numpy as np

from trend_detective.data import sample


def _ols_slope(values: np.ndarray) -> np.ndarray:
    """Per-pixel OLS slope over the time axis (axis 0)."""
    t = np.arange(values.shape[0], dtype=float)
    tbar = t.mean()
    denom = ((t - tbar) ** 2).sum()
    return ((t - tbar)[:, None, None] * values).sum(axis=0) / denom


def test_shape_dims_and_determinism():
    da, gt = sample.generate_land_grid(seed=1, ny=8, nx=10, n_months=60)
    assert da.dims == ("time", "lat", "lon")
    assert da.shape == (60, 8, 10)
    da2, _ = sample.generate_land_grid(seed=1, ny=8, nx=10, n_months=60)
    assert np.allclose(da.values, da2.values, equal_nan=True)


def test_true_patch_slopes_recovered():
    da, gt = sample.generate_land_grid(seed=0, ny=6, nx=6, n_months=240)
    patch = gt.true_patch_mask
    assert patch.any() and (~patch).any()
    slope = _ols_slope(da.values)
    # injected trend recovered on true patches, ~0 elsewhere
    assert np.allclose(slope[patch], gt.true_slope[patch], atol=0.05)
    assert np.allclose(slope[~patch], 0.0, atol=0.05)


def test_gaps_are_nan_when_requested():
    da, _ = sample.generate_land_grid(seed=2, ny=5, nx=5, n_months=60, gap_fraction=0.3)
    assert np.isnan(da.values).mean() > 0.0


def test_bundled_round_trip(tmp_path):
    da, _ = sample.generate_land_grid(seed=13, ny=5, nx=6, n_months=36)
    path = str(tmp_path / "sample.nc")
    sample.save_bundled(da, path)
    loaded = sample.load_bundled(path)
    assert loaded.dims == da.dims
    assert np.allclose(loaded.values, da.values, equal_nan=True)


def test_load_bundled_missing_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        sample.load_bundled(str(tmp_path / "absent.nc"))
