"""Deferred live Earthdata fetcher — interface stub only.

The real GES DISC / OPeNDAP fetcher ships in a later build; the data layer is
pluggable (see ``trend_detective.data.base``), so it drops in without rework.
This class exists so the seam is fixed now: it satisfies the ``DatasetSource``
shape and fails loudly until implemented.
"""

from __future__ import annotations

import xarray as xr


class EarthdataSource:
    """GES DISC / OPeNDAP fetcher. Not implemented in the cry-wolf slice."""

    def __init__(self, short_name: str | None = None) -> None:
        self.short_name = short_name

    def load(self, variable: str) -> xr.DataArray:
        raise NotImplementedError(
            "Live Earthdata fetching is deferred; the cry-wolf slice runs on "
            "the bundled synthetic source. Supply credentials and implement "
            "EarthdataSource.load to enable it."
        )
