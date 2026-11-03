"""Bundled sample data: a synthetic land grid with known ground truth.

The sample is *synthetic with known truth* so the false-discovery control can be
verified (real data has no ground truth to check against). A live Earthdata
fetcher is deferred; this module is the pluggable default source.

Model per pixel (i, j) and month t::

    value[t,i,j] = seasonal[t] + slope[i,j] * t + noise[t,i,j]

See docs/algorithms.md section 1.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import xarray as xr

#: Seasonal cycle amplitude (same for every pixel), in data units.
SEASONAL_AMPLITUDE = 3.0

#: AR(1) innovation standard deviation (stationary variance is ~sigma^2).
NOISE_SIGMA = 1.0

#: Injected patch slopes (units per month), assigned in order to patches.
PATCH_SLOPES = (0.5, -0.3)


@dataclass(frozen=True)
class GroundTruth:
    """What we secretly injected, so tests can check recovery."""

    true_slope: np.ndarray  # (ny, nx) float, units per month
    true_patch_mask: np.ndarray  # (ny, nx) bool


def _patch_rectangles(ny: int, nx: int) -> list[tuple[slice, slice]]:
    """Return up to two contiguous rectangular patches, sized to the grid."""
    r0 = max(1, ny // 6)
    r1 = max(r0 + 1, ny // 3)
    c0 = max(1, nx // 6)
    c1 = max(c0 + 1, nx // 3)
    rects = [(slice(r0, r1), slice(c0, c1))]
    if ny >= 4 and nx >= 4:
        rects.append((slice(max(0, ny - r1), max(1, ny - r0)),
                      slice(max(0, nx - c1), max(1, nx - c0))))
    return rects


def generate_land_grid(
    seed: int = 7,
    ny: int = 45,
    nx: int = 90,
    n_months: int = 240,
    gap_fraction: float = 0.0,
    autocorr: float = 0.6,
) -> tuple[xr.DataArray, GroundTruth]:
    """Build a monthly gridded series with injected trends and known truth.

    Returns ``(dataarray, ground_truth)``. Deterministic for a given ``seed``.
    """
    rng = np.random.default_rng(seed)

    # --- injected trend ---
    true_slope = np.zeros((ny, nx), dtype=float)
    true_patch_mask = np.zeros((ny, nx), dtype=bool)
    for slope_value, (rows, cols) in zip(PATCH_SLOPES, _patch_rectangles(ny, nx)):
        true_slope[rows, cols] = slope_value
        true_patch_mask[rows, cols] = True

    # --- seasonal cycle (same everywhere) ---
    t = np.arange(n_months, dtype=float)
    seasonal = SEASONAL_AMPLITUDE * np.sin(2.0 * np.pi * (t % 12.0) / 12.0)

    # --- AR(1) noise with stationary variance ~ sigma^2 ---
    innov = rng.normal(0.0, NOISE_SIGMA, size=(n_months, ny, nx))
    noise = np.empty_like(innov)
    noise[0] = innov[0]
    scale = np.sqrt(max(0.0, 1.0 - autocorr**2))
    for k in range(1, n_months):
        noise[k] = autocorr * noise[k - 1] + scale * innov[k]

    values = seasonal[:, None, None] + true_slope[None, :, :] * t[:, None, None] + noise

    # --- gaps ---
    if gap_fraction > 0.0:
        gaps = rng.random(size=values.shape) < gap_fraction
        values = np.where(gaps, np.nan, values)

    lats = np.linspace(90.0, -90.0, ny)
    lons = np.linspace(-180.0, 180.0, nx)
    da = xr.DataArray(
        values,
        dims=("time", "lat", "lon"),
        coords={"time": np.arange(n_months), "lat": lats, "lon": lons},
        name="value",
        attrs={"units": "synthetic", "source": "generate_land_grid"},
    )
    return da, GroundTruth(true_slope=true_slope, true_patch_mask=true_patch_mask)


def save_bundled(da: xr.DataArray, path: str) -> None:
    """Write the sample grid to NetCDF at ``path``."""
    da.to_netcdf(path)


def load_bundled(path: str) -> xr.DataArray:
    """Load a bundled sample grid; raise ``FileNotFoundError`` if absent."""
    import os

    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return xr.load_dataarray(path)
