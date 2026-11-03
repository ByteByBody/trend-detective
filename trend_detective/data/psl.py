"""Global NOAA PSL source — one subsetted download, then local reads.

PSL's THREDDS server runs the NetCDF Subset Service (NCSS): we ask for a
variable and a date range, and it returns just that slice. Two things make this
the right transport here:

* **One request.** PSL throttles large file downloads from some networks
  (observed: 32 KB of 29.6 MB in 84 s) and per-request OPeNDAP subsetting took
  minutes. One NCSS request asks for exactly the slice once.
* **Classic NetCDF3.** NCSS emits NetCDF3 by default, which ``scipy`` — already
  a dependency — can read. The raw ``.nc`` files are NetCDF4/HDF5 and need
  ``h5py``/``netCDF4``, which have no CPython 3.15 wheels, so this avoids a
  reader dependency that cannot be installed.

The result is cached by :mod:`trend_detective.data.cache`, so the download is
one-time: later runs open the local file.

NOAA, not NASA: PSL is the NOAA Physical Sciences Laboratory. Attribution says
so, and the API reports it verbatim.

Time is normalised to the same calendar form POWER uses: ``YYYYMM`` integers
with ``time_kind="calendar"``, so the API and chart format one shape only.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import xarray as xr

from trend_detective import config
from trend_detective.data import cache

#: THREDDS root.
PSL_BASE = "https://psl.noaa.gov/thredds"

#: NetCDF Subset Service: server-side slicing, classic NetCDF3 out.
NCSS_BASE = f"{PSL_BASE}/ncss/grid"

#: Catalog variable -> (path under the THREDDS root, variable name in the file).
DATASETS: dict[str, tuple[str, str]] = {
    "surface_temperature": (
        "Datasets/ncep.reanalysis.derived/surface/air.mon.mean.nc",
        "air",
    ),
}

#: Attribution carried on every returned array.
SOURCE_NAME = "NOAA PSL / NCEP-NCAR reanalysis"

#: Readers xarray can use for the cached file, best first. ``scipy`` is the only
#: one guaranteed to be installed; it reads the NetCDF3 that NCSS returns.
_READERS: tuple[tuple[str, str], ...] = (
    ("netcdf4", "netCDF4"),
    ("h5netcdf", "h5netcdf"),
    ("scipy", "scipy"),
)


def ncss_url(
    path: str,
    name: str,
    start: int,
    end: int,
    stride: int = 1,
) -> str:
    """Build the NCSS request for one variable over ``start``..``end``.

    ``stride`` decimates the grid server-side, which cuts the download by
    roughly ``stride ** 2``.
    """
    params = {
        "var": name,
        "time_start": f"{start:04d}-01-01",
        "time_end": f"{end:04d}-12-31",
        "accept": "netcdf3",
    }
    if stride and stride > 1:
        params["horizStride"] = stride
    return f"{NCSS_BASE}/{path}?{urlencode(params)}"


def cache_name(path: str, start: int, end: int, stride: int) -> str:
    """A cache filename unique to the request.

    NCSS encodes the whole request in the query string, so the URL basename
    alone would make a 1980-2024 slice and a 2000-2002 slice overwrite each
    other.
    """
    stem = Path(path).stem
    return f"{stem}.{start}-{end}.s{stride}.nc"


def open_dataset(path: Path) -> xr.Dataset:
    """Open a cached NetCDF file with whichever reader is installed."""
    problems = []
    for engine, module in _READERS:
        if importlib.util.find_spec(module) is None:
            continue
        try:
            return xr.open_dataset(path, engine=engine)
        except Exception as exc:  # noqa: BLE001 - report, try the next reader
            problems.append(f"{engine}: {exc}")

    detail = f" (tried {problems})" if problems else " (no reader installed)"
    raise RuntimeError(
        f"no NetCDF reader could open {path}; the NCSS service returns NetCDF3, "
        f"which scipy reads: 'pip install scipy'{detail}"
    )


def _spans(start: int, end: int, years: int) -> list[tuple[int, int]]:
    """Split ``start``..``end`` into inclusive chunks of at most ``years``."""
    if years < 1:
        raise ValueError(f"chunk years must be positive, got {years}")
    return [
        (first, min(first + years - 1, end))
        for first in range(start, end + 1, years)
    ]


def _yyyymm(times: np.ndarray) -> np.ndarray:
    """Convert a decoded datetime axis to ``YYYYMM`` integers."""
    months = np.asarray(times).astype("datetime64[M]")
    years = months.astype("datetime64[Y]").astype(int) + 1970
    return years * 100 + (months.astype(int) % 12 + 1)


def extract_grid(
    dataset: xr.Dataset,
    name: str,
    start: int | None = None,
    end: int | None = None,
    stride: int = 1,
) -> xr.DataArray:
    """Slice a PSL dataset down to the grid the analysis core expects.

    Keeps the requested years, optionally decimates space (never time), sorts
    latitude north-up, and relabels the time axis as ``YYYYMM``.
    """
    if name not in dataset:
        raise KeyError(
            f"{name!r} is not in the dataset; it has {sorted(dataset.data_vars)}"
        )

    da = dataset[name]
    if start is not None or end is not None:
        years = da["time"].values.astype("datetime64[Y]").astype(int) + 1970
        keep = np.ones(years.shape, dtype=bool)
        if start is not None:
            keep &= years >= start
        if end is not None:
            keep &= years <= end
        da = da.isel(time=np.flatnonzero(keep))

    if stride and stride > 1:
        da = da.isel(lat=slice(None, None, stride), lon=slice(None, None, stride))

    units = da.attrs.get("units", "")
    out = da.transpose("time", "lat", "lon")
    # PSL stores longitude 0..360; Leaflet and every other source use -180..180.
    # Handing raw values to the map pins the raster outside the world view, and
    # a click at -50 deg would snap to the wrong cell.
    out = out.assign_coords(lon=((out["lon"] + 180) % 360) - 180)
    out = out.sortby("lat", ascending=False).sortby("lon", ascending=True)
    out = out.assign_coords(time=_yyyymm(out["time"].values))
    out = out.rename("value")
    out.attrs = {"units": units, "source": SOURCE_NAME, "time_kind": "calendar"}
    return out


class PslSource:
    """A ``DatasetSource`` over NOAA PSL's global reanalysis grids."""

    def __init__(
        self,
        start: int | None = None,
        end: int | None = None,
        stride: int | None = None,
        chunk: int | None = None,
        fetcher=None,
        opener=None,
    ) -> None:
        self.start = config.PSL_START if start is None else start
        self.end = config.PSL_END if end is None else end
        self.stride = config.PSL_STRIDE if stride is None else stride
        self.chunk = config.PSL_CHUNK_YEARS if chunk is None else chunk
        self._fetch = cache.fetch if fetcher is None else fetcher
        self._open = open_dataset if opener is None else opener

    def variables(self) -> list[str]:
        """Only the catalog variables this source has a dataset for."""
        return list(DATASETS)

    def provenance(self, variable: str) -> str:
        """NOAA, not NASA — never let a caller present this as a NASA product."""
        return SOURCE_NAME

    def validator(self, name: str):
        """A cache validator: the file must open and carry ``name`` over time."""

        def validate(path: Path) -> None:
            dataset = self._open(path)
            try:
                if name not in dataset:
                    raise KeyError(
                        f"{path}: no {name!r}; it has {sorted(dataset.data_vars)}"
                    )
                if dataset[name].sizes.get("time", 0) == 0:
                    raise ValueError(f"{path}: {name!r} has no time steps")
            finally:
                dataset.close()

        return validate

    def load(self, variable: str) -> xr.DataArray:
        """Return a global ``(time, lat, lon)`` grid for ``variable``."""
        try:
            path, name = DATASETS[variable]
        except KeyError:
            raise KeyError(
                f"PSL has no dataset for {variable!r}; available: {sorted(DATASETS)}"
            ) from None

        parts = []
        for first, last in _spans(self.start, self.end, self.chunk):
            url = ncss_url(path, name, first, last, self.stride)
            file = self._fetch(
                url,
                name=cache_name(path, first, last, self.stride),
                validate=self.validator(name),
            )

            dataset = self._open(file)
            try:
                # The server already applied ``horizStride``, so do not decimate
                # again here — that would silently halve the resolution twice.
                part = extract_grid(dataset, name, start=first, end=last)
                parts.append(part.load())
            finally:
                dataset.close()

        da = parts[0] if len(parts) == 1 else xr.concat(parts, dim="time")
        da.attrs = dict(da.attrs, variable=variable)
        return da