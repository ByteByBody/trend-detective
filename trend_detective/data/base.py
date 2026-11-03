"""Pluggable data sources.

The analysis core and the API talk to a variable through a tiny interface —
``load(variable) -> DataArray`` — so a live fetcher can drop in without touching
either. Two sources ship: ``synthetic`` (bundled, with known ground truth) and
``power`` (live NASA MERRA-2 monthly grids, no API key; see
``trend_detective.data.power``). The Earthdata-login fetcher stays deferred.
"""

from __future__ import annotations

import zlib
from typing import Protocol

import xarray as xr

from trend_detective import catalog, config
from trend_detective.data import power, psl, sample


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

    def variables(self) -> list[str]:
        """The generator can fabricate every variable in the catalog."""
        return list(catalog.VARIABLES)

    def provenance(self, variable: str) -> str:
        """Say plainly that these numbers are fabricated, not satellite data."""
        return f"Synthetic ({self.ny}x{self.nx} generated grid, known truth)"


_SOURCES: dict[str, type] = {
    "synthetic": SyntheticSource,
    "power": power.PowerRegionalSource,
    "psl": psl.PslSource,
}


def available_variables(source: DatasetSource) -> list[str]:
    """Variables ``source`` can serve, in catalog order.

    A source that carries only a subset exposes ``variables()``; one without it
    is assumed to serve the whole catalog.
    """
    lister = getattr(source, "variables", None)
    if callable(lister):
        return list(lister())
    return list(catalog.VARIABLES)


def get_source(name: str) -> DatasetSource:
    """Return a data source by name; raise ``KeyError`` if unknown."""
    try:
        factory = _SOURCES[name]
    except KeyError:
        raise KeyError(f"unknown data source: {name!r}; known: {sorted(_SOURCES)}")
    return factory()


def provenance(source: DatasetSource, variable: str) -> str:
    """What actually produced ``variable`` under ``source``.

    The catalog records the product we would *ideally* use (GLDAS, GPM), which
    is wrong as soon as a stand-in source serves the variable. A source that
    knows better declares ``provenance(variable)``; the rest keep the catalog's
    nominal product.
    """
    declared = getattr(source, "provenance", None)
    if callable(declared):
        return str(declared(variable))
    return catalog.get_variable(variable).source
