"""The cry-wolf pipeline: naive vs field-significance-corrected trend maps.

Composes decomposition -> trend test -> BH-FDR -> coherence. See
docs/algorithms.md section 7.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import xarray as xr

from trend_detective import config
from trend_detective.analysis import coherence, decomp, significance, trend


@dataclass(frozen=True)
class CryWolfResult:
    """Naive and corrected trend-significance maps plus diagnostics."""

    slope: xr.DataArray
    p_value: xr.DataArray
    naive_mask: xr.DataArray
    corrected_mask: xr.DataArray
    insufficient_mask: xr.DataArray
    threshold: float
    n_naive: int
    n_corrected: int
    n_insufficient: int
    valid_fraction: float
    morans_i: float


def _grid(values: np.ndarray, like: xr.DataArray, name: str) -> xr.DataArray:
    return xr.DataArray(
        values,
        dims=("lat", "lon"),
        coords={"lat": like["lat"], "lon": like["lon"]},
        name=name,
    )


def cry_wolf(
    da: xr.DataArray,
    q: float = config.FDR_Q,
    min_patch: int = config.MIN_PATCH_SIZE,
    min_valid: float = config.MIN_VALID_FRACTION,
    do_deseasonalize: bool = True,
) -> CryWolfResult:
    """Run the cry-wolf analysis on a ``(time, lat, lon)`` DataArray."""
    series = decomp.deseasonalize(da) if do_deseasonalize else da
    slope, p = trend.trend_test(series)

    values = np.asarray(series.values, dtype=float)
    valid_frac_per_pixel = np.isfinite(values).mean(axis=0)
    insufficient = valid_frac_per_pixel < min_valid

    p_values = np.asarray(p.values, dtype=float).copy()
    p_values = np.where(insufficient, np.nan, p_values)
    variance = np.nanvar(values, axis=0)
    p_values = np.where(variance <= 0.0, np.nan, p_values)  # constant-series guard

    naive = significance.naive_mask(p_values, alpha=0.05)
    bh_mask, threshold = significance.benjamini_hochberg(p_values, q=q)
    corrected = coherence.connected_patch_filter(
        bh_mask, min_size=min_patch, connectivity=8
    )
    slope_values = np.asarray(slope.values, dtype=float)
    morans = coherence.morans_i(slope_values, corrected)

    return CryWolfResult(
        slope=slope,
        p_value=_grid(p_values, da, "p_value"),
        naive_mask=_grid(naive, da, "naive_mask"),
        corrected_mask=_grid(corrected, da, "corrected_mask"),
        insufficient_mask=_grid(insufficient, da, "insufficient_mask"),
        threshold=float(threshold),
        n_naive=int(naive.sum()),
        n_corrected=int(corrected.sum()),
        n_insufficient=int(insufficient.sum()),
        valid_fraction=float(np.isfinite(values).mean()),
        morans_i=float(morans),
    )
