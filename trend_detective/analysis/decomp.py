"""Seasonal decomposition: remove the repeating yearly cycle before trend testing.

See docs/algorithms.md section 2.
"""

from __future__ import annotations

import warnings

import numpy as np
import xarray as xr


def _deseasonalize_climatology(values: np.ndarray) -> np.ndarray:
    """Subtract the per-calendar-month climatology (nan-aware)."""
    n_months = values.shape[0]
    month_of = np.arange(n_months) % 12
    clim = np.empty((12,) + values.shape[1:], dtype=float)
    for m in range(12):
        # A calendar month can be all-NaN at a pixel (heavy gaps); nanmean then
        # warns "Mean of empty slice". The result is NaN and that is correct —
        # silence the warning, do not impute.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            clim[m] = np.nanmean(values[month_of == m], axis=0)
    out = values - clim[month_of]
    return np.where(np.isnan(values), np.nan, out)


#: STL needs at least two full seasonal periods to estimate a cycle.
_STL_MIN_MONTHS = 24


def _deseasonalize_stl(values: np.ndarray) -> np.ndarray:
    """Remove the STL seasonal component per pixel (requires statsmodels).

    Pixels STL cannot handle — any missing month, or a series shorter than two
    periods — fall back to the climatology method for that pixel. They are
    *not* left with their seasonal cycle intact: a gappy pixel can still pass
    the pipeline's valid-fraction gate, and testing it with a 12-month swing
    still in place would manufacture a trend.
    """
    from statsmodels.tsa.seasonal import STL  # local import: optional dependency

    n_months = values.shape[0]
    fallback = _deseasonalize_climatology(values)
    out = values.copy()
    for i in range(values.shape[1]):
        for j in range(values.shape[2]):
            series = values[:, i, j]
            if n_months < _STL_MIN_MONTHS or np.isnan(series).any():
                out[:, i, j] = fallback[:, i, j]
                continue
            if np.all(series == series[0]):  # constant: nothing to remove
                continue
            seasonal = STL(series, period=12, robust=True).fit().seasonal
            out[:, i, j] = series - seasonal
    return out


def deseasonalize(da: xr.DataArray, method: str = "auto") -> xr.DataArray:
    """Return ``da`` with the seasonal cycle removed along ``time``.

    ``method`` is one of ``"stl"``, ``"climatology"``, or ``"auto"``. ``"auto"``
    uses STL when ``statsmodels`` is importable, else the climatology method.
    NaNs are preserved, never filled.
    """
    if method not in ("stl", "climatology", "auto"):
        raise ValueError(f"unknown method: {method!r}")

    resolve = method
    if method == "auto":
        try:
            import statsmodels  # noqa: F401

            resolve = "stl"
        except ImportError:
            resolve = "climatology"

    values = np.asarray(da.values, dtype=float)
    if resolve == "stl":
        try:
            out = _deseasonalize_stl(values)
        except ImportError:
            out = _deseasonalize_climatology(values)
    else:
        out = _deseasonalize_climatology(values)

    return xr.DataArray(out, dims=da.dims, coords=da.coords, name=da.name, attrs=da.attrs)
