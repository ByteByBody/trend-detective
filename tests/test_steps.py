"""Step-change detection: a field-relative cut on the 60-vs-60-month median step."""

import numpy as np

from trend_detective.analysis import steps
from trend_detective.data import sample


def _noise_grid(seed, n_months=240, ny=12, nx=12, sigma=1.0):
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, sigma, size=(n_months, ny, nx))


def test_flags_an_injected_step_and_ignores_noise():
    values = _noise_grid(1)
    values[120:, 4, 7] += 10.0
    mask = steps.step_change_mask(values)
    assert mask[4, 7]
    assert int(mask.sum()) == 1


def test_flat_with_rain_spikes_is_not_flagged():
    values = _noise_grid(2, sigma=0.01)
    rng = np.random.default_rng(3)
    for i in range(0, 12, 2):
        for j in range(0, 12, 2):
            months = rng.choice(values.shape[0], size=10, replace=False)
            values[months, i, j] += 5.0
    assert not steps.step_change_mask(values).any()


def test_constant_field_is_not_flagged():
    values = np.zeros((240, 8, 8))
    assert not steps.step_change_mask(values).any()


def test_transient_excursion_is_not_flagged():
    # A 5-year bump that returns to its old level is not a step, however big.
    values = _noise_grid(11, sigma=0.05, ny=8, nx=8)
    values[120:180, 3, 4] += 5.0
    assert not steps.step_change_mask(values).any()


def test_smooth_trend_is_not_flagged():
    # A steady ramp is a trend, not a step: the first and last windows differ by
    # far more than one 60-month window's worth, so it is not a level shift.
    values = _noise_grid(12, sigma=0.05, ny=8, nx=8)
    values[:, 2, 2] += 0.05 * np.arange(values.shape[0])
    assert not steps.step_change_mask(values)[2, 2]


def test_threshold_is_field_relative():
    # The identical 5-unit step is a step in a calm field and background noise
    # in a rough one; the cut has to be relative to each field's own step scale.
    calm = _noise_grid(4, sigma=0.02, ny=8, nx=8)
    calm[120:, 2, 2] += 5.0
    assert steps.step_change_mask(calm)[2, 2]

    rough = _noise_grid(5, sigma=6.0, ny=8, nx=8)
    rough[120:, 2, 2] += 5.0
    assert not steps.step_change_mask(rough)[2, 2]


def test_nan_pixels_are_not_flagged():
    values = _noise_grid(7, ny=8, nx=8)
    values[:, 0, 0] = np.nan
    values[120:, 2, 2] += 10.0
    mask = steps.step_change_mask(values)
    assert not mask[0, 0]
    assert mask[2, 2]


def test_max_step_reports_the_split_around_the_jump():
    values = _noise_grid(6, sigma=0.05, ny=6, nx=6)
    values[150:, 1, 3] += 8.0
    abs_step, signed, split = steps.max_step(values)
    assert abs_step[1, 3] > 7.0
    assert signed[1, 3] > 7.0
    assert 120 <= int(split[1, 3]) <= 180


def test_step_change_test_wraps_an_xarray_grid():
    da, _ = sample.generate_land_grid(seed=8, ny=10, nx=10, n_months=240, autocorr=0.0)
    values = da.values.copy()
    values[120:, 3, 3] += 8.0
    da = da.copy(data=values)
    change = steps.step_change_test(da)
    assert change.mask.dims == ("lat", "lon")
    assert bool(change.mask.values[3, 3])
    assert np.isfinite(change.magnitude.values[3, 3])
