"""Trend estimation: Theil-Sen slope and Mann-Kendall significance.

The Mann-Kendall p-value optionally applies the Hamed-Rao effective-sample-size
correction for autocorrelation — the "memory fix" that stops smooth wandering
from being read as a trend. See docs/algorithms.md sections 3-4.
"""

from __future__ import annotations

import math

import numpy as np
import xarray as xr


def _sen_slope_1d(x: np.ndarray) -> float:
    """Median pairwise slope of a 1-D series (units per step)."""
    n = x.size
    if n < 2:
        return math.nan
    i, j = np.triu_indices(n, 1)
    slopes = (x[j] - x[i]) / (j - i)
    with np.errstate(invalid="ignore"):
        return float(np.nanmedian(slopes))


def _detrended(x: np.ndarray) -> np.ndarray:
    """Remove a least-squares linear trend, returning the residuals."""
    n = x.size
    t = np.arange(n, dtype=float)
    t -= t.mean()
    denom = float(np.dot(t, t))
    if denom <= 0:
        return x - x.mean()
    slope = float(np.dot(t, x - x.mean()) / denom)
    return x - x.mean() - slope * t


def _effective_sample_size_factor(x: np.ndarray) -> float:
    """Hamed-Rao / AR(1) variance inflation ``(1 + rho) / (1 - rho)``.

    Returns the multiplier for ``Var(S)``. The lag-1 autocorrelation is
    estimated on the **detrended residual** series, *not* the raw series:
    a real linear trend is itself highly autocorrelated (measured raw
    ``rho_1 = 0.987`` for a genuine 0.5/month trend), so estimating ``rho``
    on the raw data penalises the very trend we are trying to detect —
    inflating the variance ~150x and hiding it. The residual autocorrelation
    (measured ``0.538`` for AR(1) noise with ``phi = 0.5``) is what the
    correction is meant to account for.

    Uses lag-1 only (an AR(1) model), which is stable for short and noisy
    series — summing estimates across *all* lags is unstable, because noisy
    high-lag autocorrelations frequently *shrink* the variance and manufacture
    false trends (measured: white-noise false positives rose from 4% to 23%).

    The factor is floored at 1.0: the correction never claims *more*
    significance than the independent test, matching the "won't cry wolf"
    stance. Negative autocorrelation (factor < 1) is therefore not exploited;
    noted as a conservative limitation.
    """
    n = x.size
    if n < 3:
        return 1.0
    xd = _detrended(x)
    denom = float(np.dot(xd, xd))
    if denom <= 0:
        return 1.0
    rho1 = float(np.dot(xd[:-1], xd[1:]) / denom)
    rho1 = min(max(rho1, 0.0), 0.99)
    return (1.0 + rho1) / (1.0 - rho1)


def _mk_pvalue_1d(x: np.ndarray, autocorr_correct: bool) -> float:
    """Two-sided Mann-Kendall p-value for a 1-D series (NaN-aware)."""
    x = x[~np.isnan(x)]
    n = x.size
    if n < 3:
        return math.nan
    if np.all(x == x[0]):  # constant series: no trend, never significant
        return math.nan

    i, j = np.triu_indices(n, 1)
    s = float(np.sign(x[j] - x[i]).sum())

    var_s = n * (n - 1) * (2 * n + 5) / 18.0
    if autocorr_correct:
        var_s *= _effective_sample_size_factor(x)
    if var_s <= 0:
        return math.nan

    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0
    return math.erfc(abs(z) / math.sqrt(2.0))


def _apply_over_pixels(da: xr.DataArray, fn) -> xr.DataArray:
    """Apply a 1-D time-series function to every pixel of a (time, lat, lon) DA."""
    values = np.asarray(da.values, dtype=float)
    t, ny, nx = values.shape
    flat = values.reshape(t, ny * nx)
    out = np.empty(ny * nx, dtype=float)
    for p in range(ny * nx):
        out[p] = fn(flat[:, p])
    return xr.DataArray(
        out.reshape(ny, nx),
        dims=("lat", "lon"),
        coords={"lat": da["lat"], "lon": da["lon"]},
        name=da.name,
    )


def theilsen_slope(da: xr.DataArray) -> xr.DataArray:
    """Theil-Sen slope per pixel (units per month)."""
    return _apply_over_pixels(da, _sen_slope_1d)


def mann_kendall_pvalue(da: xr.DataArray, autocorr_correct: bool = True) -> xr.DataArray:
    """Mann-Kendall p-value per pixel, Hamed-Rao corrected by default."""
    return _apply_over_pixels(da, lambda x: _mk_pvalue_1d(x, autocorr_correct))


def trend_test(
    da: xr.DataArray, autocorr_correct: bool = True
) -> tuple[xr.DataArray, xr.DataArray]:
    """Return ``(slope, p_value)`` per pixel."""
    return theilsen_slope(da), mann_kendall_pvalue(da, autocorr_correct=autocorr_correct)
