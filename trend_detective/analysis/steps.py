"""Step-change detection: flag abrupt jumps a smooth trend could not make.

A record that jumps from one level to another can pass every trend test for a
reason that has nothing to do with climate — usually an upstream ingest defect.
This marks those pixels from a field-relative cut on the largest 60-vs-60-month
median step, so the map can show them *without* removing them from either
verdict. See docs/algorithms.md section 8.

Two adjacent 60-month windows are what makes the statistic persistent: a rain
spike moves a single month, not a 5-year median, so a flat desert series that
spikes when it rains is not a step. A second gate compares the jump with the
level left at the end of the record, so a smooth ramp and an excursion that
returns are rejected too.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import xarray as xr

from trend_detective import config


def max_step(
    values: np.ndarray,
    window: int = config.STEP_WINDOW,
    stride: int = config.STEP_STRIDE,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Largest |median(right) - median(left)| over every split, with its sign.

    ``values`` is ``(time, lat, lon)``; two adjacent ``window``-month windows are
    slid ``stride`` months at a time. Returns ``(abs_step, signed_step, split)``
    grids. ``split`` is the first month index whose right window achieves the
    maximum, or ``-1`` where the statistic is undefined (too short a record, or
    an all-NaN pixel).
    """
    values = np.asarray(values, dtype=float)
    n = values.shape[0]
    ny, nx = values.shape[1], values.shape[2]
    abs_step = np.full((ny, nx), np.nan)
    signed = np.full((ny, nx), np.nan)
    split = np.full((ny, nx), -1, dtype=int)
    if n < 2 * window:
        return abs_step, signed, split

    for k in range(window, n - window + 1, stride):
        # An all-NaN window (ocean soil moisture, a fully gapped pixel) warns
        # "Mean of empty slice"; the result is NaN and that is the correct
        # answer, so silence the warning rather than impute.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            left = np.nanmedian(values[k - window : k], axis=0)
            right = np.nanmedian(values[k : k + window], axis=0)
        step = right - left
        magnitude = np.abs(step)
        update = np.isfinite(magnitude) & (np.isnan(abs_step) | (magnitude > abs_step))
        abs_step = np.where(update, magnitude, abs_step)
        signed = np.where(update, step, signed)
        split = np.where(update, k, split)
    return abs_step, signed, split


def _threshold_mask(abs_step: np.ndarray, threshold_sigma: float) -> np.ndarray:
    """Apply the field-relative cut to a step-magnitude grid.

    The reference is ``median + threshold_sigma * 1.4826 * MAD`` over pixels that
    show *any* step (``abs_step > 0``). Restricting the reference to those pixels
    is what keeps a mostly-zero field (snow sitting on bare land) from collapsing
    the scale to zero and flagging every nonzero cell. A field with no step at
    all, or one whose steps are all the same size, has no scale to cut against
    and flags nothing.
    """
    finite = np.isfinite(abs_step)
    active = finite & (abs_step > 0.0)
    if not active.any():
        return np.zeros(abs_step.shape, dtype=bool)
    reference = abs_step[active]
    center = float(np.median(reference))
    mad = float(np.median(np.abs(reference - center)))
    sigma = 1.4826 * mad
    if sigma <= 0.0:
        return np.zeros(abs_step.shape, dtype=bool)
    threshold = center + threshold_sigma * sigma
    return finite & (abs_step > threshold)


def endpoints_step(
    values: np.ndarray, window: int = config.STEP_WINDOW
) -> np.ndarray:
    """``median(last window) - median(first window)`` per pixel.

    The one level change that must survive to the end of the record. A localized
    step of size ``H`` gives ``H``; a smooth ramp gives the whole ramp's worth
    (about ``8 x`` one 60-month step over 540 months); an excursion that returns
    gives about ``0``.
    """
    values = np.asarray(values, dtype=float)
    if values.shape[0] < 2 * window:
        return np.full(values.shape[1:], np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(values[-window:], axis=0) - np.nanmedian(
            values[:window], axis=0
        )


def _persistent_gate(
    abs_step: np.ndarray,
    endpoints: np.ndarray,
    threshold_sigma: float,
    persistence_min: float,
    persistence_max: float,
) -> np.ndarray:
    """Both gates: field-relative magnitude, and a shift that survives to the end.

    The second gate is a *band*. A durable step of size ``H`` leaves
    ``|endpoints| ~ H ~ abs_step`` (ratio near 1). A smooth ramp leaves an
    endpoint difference of many window-steps (ratio well above the top of the
    band), and an excursion that returns to its old level leaves almost nothing
    (ratio near 0). Both are excluded; only the level shift survives.
    """
    magnitude = _threshold_mask(abs_step, threshold_sigma)
    with np.errstate(invalid="ignore"):
        survives = (
            np.isfinite(endpoints)
            & (np.abs(endpoints) >= persistence_min * abs_step)
            & (np.abs(endpoints) <= persistence_max * abs_step)
        )
    return magnitude & survives


def step_change_mask(
    values: np.ndarray,
    window: int = config.STEP_WINDOW,
    stride: int = config.STEP_STRIDE,
    threshold_sigma: float = config.STEP_THRESHOLD_SIGMA,
    persistence_min: float = config.STEP_PERSISTENCE_MIN,
    persistence_max: float = config.STEP_PERSISTENCE_MAX,
) -> np.ndarray:
    """Boolean ``(lat, lon)`` grid: True where the record holds a *persistent* step.

    Both gates must pass: the step has to clear the field-relative magnitude cut,
    *and* its end-of-record shift has to sit in the persistence band. That second
    gate is what rejects a smooth trend and an excursion that returns.
    """
    abs_step, _signed, _split = max_step(values, window=window, stride=stride)
    endpoints = endpoints_step(values, window=window)
    return _persistent_gate(
        abs_step, endpoints, threshold_sigma, persistence_min, persistence_max
    )


@dataclass(frozen=True)
class StepChange:
    """The step-change verdict: where it is, and how big the jump was."""

    mask: xr.DataArray  # bool, (lat, lon)
    magnitude: xr.DataArray  # signed step at the chosen split, NaN elsewhere


def _grid(values: np.ndarray, like: xr.DataArray, name: str) -> xr.DataArray:
    return xr.DataArray(
        values,
        dims=("lat", "lon"),
        coords={"lat": like["lat"], "lon": like["lon"]},
        name=name,
    )


def step_change_test(
    da: xr.DataArray,
    window: int = config.STEP_WINDOW,
    stride: int = config.STEP_STRIDE,
    threshold_sigma: float = config.STEP_THRESHOLD_SIGMA,
    persistence_min: float = config.STEP_PERSISTENCE_MIN,
    persistence_max: float = config.STEP_PERSISTENCE_MAX,
) -> StepChange:
    """Run the detector on a ``(time, lat, lon)`` grid."""
    values = np.asarray(da.values, dtype=float)
    abs_step, signed, _split = max_step(values, window=window, stride=stride)
    endpoints = endpoints_step(values, window=window)
    mask = _persistent_gate(
        abs_step, endpoints, threshold_sigma, persistence_min, persistence_max
    )
    magnitude = np.where(mask, signed, np.nan)
    return StepChange(
        mask=_grid(mask, da, "step_mask"),
        magnitude=_grid(magnitude, da, "step_magnitude"),
    )
