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
    return _p_from_s(s, var_s)


def _sen_slopes(flat: np.ndarray) -> np.ndarray:
    """Theil-Sen slope for every column of ``flat`` (time, pixels).

    The pair-index vectors are built **once** and reused for every pixel; the
    no-missing fast path uses ``np.median`` on a contiguous 1-D pair vector.
    Measured faster than a vectorised (pixels x pairs) block: the per-pixel
    vector stays small and cache-friendly, and at most one pair vector exists
    at a time, so the memory motivation for chunking does not apply.
    """
    n, n_pixels = flat.shape
    out = np.full(n_pixels, math.nan)
    if n < 2:
        return out
    i, j = np.triu_indices(n, 1)
    denom = (j - i).astype(float)
    for p in range(n_pixels):
        x = flat[:, p]
        if np.isfinite(x).all():
            out[p] = np.median((x[j] - x[i]) / denom)
        else:
            out[p] = _sen_slope_1d(x)
    return out


def _p_from_s(s: float, var_s: float) -> float:
    """Two-sided p-value from the Mann-Kendall statistic and its variance."""
    if var_s <= 0:
        return math.nan
    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0
    return math.erfc(abs(z) / math.sqrt(2.0))


def _mk_pvalues(flat: np.ndarray, autocorr_correct: bool = True) -> np.ndarray:
    """Mann-Kendall p-value for every column of ``flat`` (time, pixels)."""
    n, n_pixels = flat.shape
    out = np.full(n_pixels, math.nan)
    if n < 3:
        return out
    i, j = np.triu_indices(n, 1)
    var_base = n * (n - 1) * (2 * n + 5) / 18.0
    for p in range(n_pixels):
        x = flat[:, p]
        if not np.isfinite(x).all():
            out[p] = _mk_pvalue_1d(x, autocorr_correct)
            continue
        if x.max() == x.min():  # constant: no trend, never significant
            continue
        s = float(np.sign(x[j] - x[i]).sum())
        var_s = var_base
        if autocorr_correct:
            var_s *= _effective_sample_size_factor(x)
        out[p] = _p_from_s(s, var_s)
    return out


def _wrap_grid(values: np.ndarray, da: xr.DataArray) -> xr.DataArray:
    ny, nx = da.sizes["lat"], da.sizes["lon"]
    return xr.DataArray(
        values.reshape(ny, nx),
        dims=("lat", "lon"),
        coords={"lat": da["lat"], "lon": da["lon"]},
        name=da.name,
    )


def theilsen_slope(da: xr.DataArray) -> xr.DataArray:
    """Theil-Sen slope per pixel (units per month)."""
    values = np.asarray(da.values, dtype=float)
    t, ny, nx = values.shape
    return _wrap_grid(_sen_slopes(values.reshape(t, ny * nx)), da)


def mann_kendall_pvalue(da: xr.DataArray, autocorr_correct: bool = True) -> xr.DataArray:
    """Mann-Kendall p-value per pixel, Hamed-Rao corrected by default."""
    values = np.asarray(da.values, dtype=float)
    t, ny, nx = values.shape
    return _wrap_grid(_mk_pvalues(values.reshape(t, ny * nx), autocorr_correct), da)


def trend_test(
    da: xr.DataArray, autocorr_correct: bool = True
) -> tuple[xr.DataArray, xr.DataArray]:
    """Return ``(slope, p_value)`` per pixel."""
    return theilsen_slope(da), mann_kendall_pvalue(da, autocorr_correct=autocorr_correct)
