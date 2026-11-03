"""Live NASA POWER regional source — real gridded data, no API key.

POWER serves MERRA-2 monthly means on a 0.5 degree grid from 1981 onward.
The regional endpoint needs no Earthdata login, but it is strict:

- at most **10 degrees** of latitude and longitude per request;
- at most **one parameter** per request;
- values keyed ``YYYYMM``, plus an annual ``YYYY13`` row to discard;
- missing cells are the sentinel ``-999.0``.

This module turns those responses into the ``(time, lat, lon)`` grid the
analysis core expects. Multi-tile requests are stitched back together.

The returned time coordinate is ``YYYYMM`` as an integer and starts in the
requested start year's January: ``decomp`` assigns calendar months by
*position* (``arange(n) % 12``), so a series that does not begin in January
would have its seasonal cycle assigned to the wrong phase.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request

import numpy as np
import xarray as xr

from trend_detective import config

#: Regional endpoint (one parameter, <=10 degrees per axis).
POWER_URL = "https://power.larc.nasa.gov/api/temporal/monthly/regional"

#: Maximum degrees of latitude/longitude per request.
MAX_SPAN_DEG = 10.0

#: POWER's sentinel for "no data".
FILL_VALUE = -999.0

#: Month index of the annual summary row (``YYYY13``).
ANNUAL_MONTH = 13

#: Catalog variable -> POWER parameter. Only variables whose units match the
#: catalog entry are listed, so the API never mislabels a series.
PARAMETERS: dict[str, str] = {
    "surface_temperature": "T2M",
    "precipitation": "PRECTOTCORR",
}

#: POWER's attribution text, kept on the loaded array.
SOURCE_NAME = "NASA POWER / MERRA-2"


def tiles(bbox: tuple[float, float, float, float]) -> list[tuple[float, float, float, float]]:
    """Split ``(lat_min, lat_max, lon_min, lon_max)`` into <=10 degree boxes.

    Each axis is cut into equal spans so the boxes tile the bbox exactly.
    """
    lat_min, lat_max, lon_min, lon_max = bbox
    out = []
    for a0, a1 in _spans(lat_min, lat_max):
        for b0, b1 in _spans(lon_min, lon_max):
            out.append((a0, a1, b0, b1))
    return out


def _spans(lo: float, hi: float, span: float = MAX_SPAN_DEG) -> list[tuple[float, float]]:
    n = max(1, math.ceil((hi - lo) / span - 1e-9))
    step = (hi - lo) / n
    return [(lo + k * step, lo + (k + 1) * step) for k in range(n)]


def parse_regional(payload: dict) -> xr.DataArray:
    """Convert a POWER regional GeoJSON payload to a ``(time, lat, lon)`` grid.

    The annual ``YYYY13`` rows are dropped and ``-999.0`` becomes ``NaN``;
    cells the response never mentions stay ``NaN``.
    """
    features = payload.get("features") or []
    if not features:
        raise ValueError("POWER response contained no features")

    declared = payload.get("parameters") or {}
    param = next(iter(declared), None)
    if param is None:
        param = next(iter(features[0]["properties"]["parameter"]))

    per_cell = []
    for feature in features:
        lon, lat = feature["geometry"]["coordinates"][:2]
        per_cell.append((float(lat), float(lon), feature["properties"]["parameter"][param]))

    times = sorted(
        {
            int(key)
            for _, _, series in per_cell
            for key in series
            if int(key) % 100 != ANNUAL_MONTH
        }
    )
    lats = sorted({lat for lat, _, _ in per_cell}, reverse=True)
    lons = sorted({lon for _, lon, _ in per_cell})

    values = np.full((len(times), len(lats), len(lons)), np.nan, dtype=float)
    t_index = {t: i for i, t in enumerate(times)}
    lat_index = {lat: i for i, lat in enumerate(lats)}
    lon_index = {lon: i for i, lon in enumerate(lons)}

    for lat, lon, series in per_cell:
        row = lat_index[lat]
        col = lon_index[lon]
        for key, raw in series.items():
            stamp = int(key)
            if stamp % 100 == ANNUAL_MONTH:
                continue
            value = float(raw)
            values[t_index[stamp], row, col] = np.nan if value <= FILL_VALUE else value

    units = (declared.get(param) or {}).get("units", "")
    return xr.DataArray(
        values,
        dims=("time", "lat", "lon"),
        coords={"time": np.asarray(times, dtype=int), "lat": lats, "lon": lons},
        name="value",
        attrs={"units": units, "source": SOURCE_NAME},
    )


def _url(param: str, box: tuple[float, float, float, float], start: int, end: int) -> str:
    lat_min, lat_max, lon_min, lon_max = box
    query = urllib.parse.urlencode(
        {
            "parameters": param,
            "community": "RE",
            "latitude-min": lat_min,
            "latitude-max": lat_max,
            "longitude-min": lon_min,
            "longitude-max": lon_max,
            "start": start,
            "end": end,
            "format": "JSON",
        }
    )
    return f"{POWER_URL}?{query}"


def _http_get_json(url: str, timeout: float = config.POWER_TIMEOUT) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


class PowerRegionalSource:
    """A ``DatasetSource`` backed by NASA POWER's regional monthly endpoint."""

    def __init__(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        start: int | None = None,
        end: int | None = None,
        fetch=None,
    ) -> None:
        self.bbox = bbox if bbox is not None else config.POWER_BBOX
        self.start = config.POWER_START if start is None else start
        self.end = config.POWER_END if end is None else end
        self._fetch = _http_get_json if fetch is None else fetch

    def load(self, variable: str) -> xr.DataArray:
        """Fetch ``variable`` for the configured box and return a full grid."""
        try:
            param = PARAMETERS[variable]
        except KeyError:
            raise KeyError(
                f"NASA POWER has no parameter for {variable!r}; "
                f"available: {sorted(PARAMETERS)}"
            ) from None

        parts = [
            parse_regional(self._fetch(_url(param, box, self.start, self.end)))
            for box in tiles(self.bbox)
        ]
        da = parts[0] if len(parts) == 1 else xr.combine_by_coords(parts)
        if isinstance(da, xr.Dataset):
            # Stitching tiles with disjoint coordinates makes xarray widen the
            # result to a Dataset; every part carries the same single variable.
            (name,) = list(da.data_vars)
            da = da[name]
        da = da.sortby("lat", ascending=False).sortby("lon", ascending=True)
        da.attrs = dict(da.attrs, variable=variable, source=SOURCE_NAME)
        return da

    def variables(self) -> list[str]:
        """Only the catalog variables that map onto a POWER parameter."""
        return list(PARAMETERS)