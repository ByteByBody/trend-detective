"""Pluggable data sources.

The analysis core and the API talk to a variable through a tiny interface —
``load(variable) -> DataArray`` — so a live Earthdata fetcher (see
``trend_detective.data.fetch``) can drop in without touching either. The
cry-wolf slice ships the synthetic source only.
"""

from __future__ import annotations

import zlib
from typing import Protocol

import xarray as xr

from trend_detective import config
from trend_detective.data import sample


class DatasetSource(Protocol):
    """Anything that can produce a ``(time, lat, lon)`` grid for a variable."""

    def load(self, variable: str) -> xr.DataArray:  # pragma: no cover - protocol
        ...


class SyntheticSource:
    """Deterministic synthetic grid with injected trends and known truth.

    The same variable always yields the same grid, and different variables
    yield different grids (the seed is derived from the variable name).
    """

    def __init__(
        self,
        ny: int = config.DEMO_GRID[0],
        nx: int = config.DEMO_GRID[1],
        n_months: int = config.DEFAULT_MONTHS,
        autocorr: float = config.DEMO_AUTOCORR,
    ) -> None:
        self.ny = ny
        self.nx = nx
        self.n_months = n_months
        self.autocorr = autocorr

    def load(self, variable: str) -> xr.DataArray:
        seed = zlib.crc32(variable.encode()) % 100_000
        da, _ = sample.generate_land_grid(
            seed=seed,
            ny=self.ny,
            nx=self.nx,
            n_months=self.n_months,
            autocorr=self.autocorr,
        )
        da.attrs = dict(da.attrs, variable=variable)
        return da


_SOURCES: dict[str, type] = {"synthetic": SyntheticSource}


def get_source(name: str) -> DatasetSource:
    """Return a data source by name; raise ``KeyError`` if unknown."""
    try:
        factory = _SOURCES[name]
    except KeyError:
        raise KeyError(f"unknown data source: {name!r}; known: {sorted(_SOURCES)}")
    return factory()
