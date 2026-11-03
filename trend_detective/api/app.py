"""FastAPI service for the Trend Detective cry-wolf demo.

Exposes the variable catalog, a per-variable cry-wolf analysis over a bundled
synthetic grid, and a per-cell time series. See docs/algorithms.md section 8.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from trend_detective import catalog, config
from trend_detective.analysis import pipeline
from trend_detective.data import base as data_source

_WEB_DIR = Path(__file__).resolve().parents[2] / "web"

app = FastAPI(title="Trend Detective", version="0.1.0")

#: The data source the demo reads from. Swapping this for the Earthdata
#: fetcher is the only change needed to run on live data.
_SOURCE = data_source.get_source(config.DATA_SOURCE)


@lru_cache(maxsize=None)
def _demo_grid(variable: str):
    """Load and cache one deterministic grid per variable."""
    return _SOURCE.load(variable)


@lru_cache(maxsize=None)
def _demo_body(variable: str) -> dict:
    """Run the cry-wolf pipeline once per variable and cache the JSON body."""
    da = _demo_grid(variable)
    res = pipeline.cry_wolf(da)

    slope = res.slope.values

    def _grid(values) -> list:
        return np.where(np.isfinite(values), np.round(values, 6), None).tolist()

    def _mask(values) -> list:
        return np.asarray(values, dtype=int).tolist()

    return {
        "variable": variable,
        "units": catalog.get_variable(variable).units,
        "lat": [float(v) for v in da["lat"].values],
        "lon": [float(v) for v in da["lon"].values],
        "threshold": float(res.threshold),
        "n_naive": int(res.n_naive),
        "n_corrected": int(res.n_corrected),
        "n_insufficient": int(res.n_insufficient),
        "morans_i": float(res.morans_i),
        "valid_fraction": float(res.valid_fraction),
        "naive_mask": _mask(res.naive_mask.values),
        "corrected_mask": _mask(res.corrected_mask.values),
        "slope": _grid(slope),
    }


@app.get("/catalog")
def get_catalog() -> list[dict]:
    """List the studyable variables and their metadata."""
    return [
        {
            "key": v.key,
            "label": v.label,
            "units": v.units,
            "source": v.source,
            "description": v.description,
        }
        for v in catalog.VARIABLES.values()
    ]


@app.get("/demo/{variable}")
def get_demo(variable: str) -> dict:
    """Cry-wolf result for a variable: naive vs field-significance-corrected."""
    try:
        catalog.get_variable(variable)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown variable: {variable!r}")
    return _demo_body(variable)


@app.get("/timeseries/{variable}")
def get_timeseries(
    variable: str,
    lat: float = Query(...),
    lon: float = Query(...),
) -> dict:
    """Return the nearest-cell monthly series for a variable."""
    try:
        catalog.get_variable(variable)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown variable: {variable!r}")

    if not (np.isfinite(lat) and np.isfinite(lon)):
        raise HTTPException(status_code=422, detail="lat and lon must be finite")

    da = _demo_grid(variable)
    i = int(np.abs(da["lat"].values - lat).argmin())
    j = int(np.abs(da["lon"].values - lon).argmin())
    series = da.isel(lat=i, lon=j).values
    return {
        "variable": variable,
        "lat": float(da["lat"].values[i]),
        "lon": float(da["lon"].values[j]),
        "time": [int(t) for t in da["time"].values],
        "value": [None if not np.isfinite(v) else float(v) for v in series],
    }


@app.get("/")
def index() -> FileResponse:
    """Serve the single-page frontend."""
    path = _WEB_DIR / "index.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="frontend not built")
    return FileResponse(path)


if _WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
