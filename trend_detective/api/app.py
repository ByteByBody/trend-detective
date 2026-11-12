"""FastAPI service for the Trend Detective cry-wolf demo.

Exposes the variable catalog, a per-variable cry-wolf analysis over a bundled
synthetic grid, a per-cell time series, and the trend-fingerprint search. See
docs/algorithms.md sections 8 and 11.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from scipy import ndimage

from trend_detective import catalog, config
from trend_detective.analysis import fingerprint, pipeline
from trend_detective.data import analysis_cache
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


def _served_variables() -> list[str]:
    """Variables the active source can actually produce."""
    return data_source.available_variables(_SOURCE)


def _provenance(variable: str) -> str:
    """The source that actually produced ``variable``, not the nominal product."""
    return data_source.provenance(_SOURCE, variable)


def _require_served(variable: str) -> None:
    """404 unless ``variable`` is known *and* the active source can serve it."""
    try:
        catalog.get_variable(variable)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown variable: {variable!r}") from None

    served = _served_variables()
    if variable not in served:
        raise HTTPException(
            status_code=404,
            detail=(
                f"data source {type(_SOURCE).__name__} cannot serve {variable!r}; "
                f"available: {served}"
            ),
        )


def _time_kind(da) -> str:
    """Whether the time axis carries calendar months or a plain month index."""
    return "calendar" if da.attrs.get("time_kind") == "calendar" else "index"


def _time_labels(da) -> list[str]:
    """Display labels for the time axis.

    POWER's ``YYYYMM`` integers are meaningless as numbers on a chart axis
    (``202005`` renders as "202.05k"), so calendar axes become ISO dates and
    index axes stay as plain month numbers.
    """
    values = da["time"].values
    if _time_kind(da) == "calendar":
        return [f"{int(v) // 100:04d}-{int(v) % 100:02d}-01" for v in values]
    return [str(int(v)) for v in values]


def _annual_anomaly(da) -> list[float]:
    """Global annual-mean anomaly, one entry per whole year of the record.

    The masthead's warming-stripe band is the canonical climate visual, so it
    must come from the data, not from a stock image. Years are consecutive
    12-month blocks; a trailing partial year is dropped rather than stretched.
    Values are in the variable's catalog units and centred on zero.
    """
    values = np.asarray(da.values, dtype=float)
    if values.ndim != 3 or values.shape[0] < 12:
        return []

    # Weight by cos(latitude): a polar row covers far less surface than an
    # equatorial one, so an equal-weight grid mean is not a global mean.
    lat = np.asarray(da["lat"].values, dtype=float)
    row_weight = np.cos(np.deg2rad(lat))
    row_weight = np.where(np.isfinite(row_weight) & (row_weight > 0), row_weight, 0.0)

    yearly = []
    for y in range(values.shape[0] // 12):
        block = values[y * 12 : (y + 1) * 12]
        mask = np.isfinite(block)
        weight = np.broadcast_to(row_weight[None, :, None], block.shape)
        denom = float(np.where(mask, weight, 0.0).sum())
        if denom > 0:
            yearly.append(
                float(np.where(mask, block * weight, 0.0).sum() / denom)
            )
        else:
            yearly.append(np.nan)

    yearly = np.asarray(yearly, dtype=float)
    finite = yearly[np.isfinite(yearly)]
    if not finite.size:
        return []
    base = finite.mean()
    return [
        None if not np.isfinite(v) else round(float(v - base), 6) for v in yearly
    ]


@lru_cache(maxsize=None)
def _demo_body(variable: str) -> dict:
    """The cry-wolf body for a variable, served from disk when possible.

    A cold analysis is minutes (STL deseasonalization dominates), so a finished
    body is persisted under ``data/cache/analysis`` and reused across process
    restarts; only a genuine miss recomputes it.
    """
    signature = _analysis_signature()
    cached = analysis_cache.load(variable, signature=signature)
    if cached is not None:
        return cached
    body = _compute_demo_body(variable)
    analysis_cache.save(variable, body, signature=signature)
    return body


def _analysis_signature() -> str:
    """The disk-cache signature for the source that is actually serving.

    Keyed on the resolved source class rather than only ``config.DATA_SOURCE``: a
    test or another deployment can reuse the configured name while serving
    different data, and its body must never be served here.
    """
    source = f"{type(_SOURCE).__module__}.{type(_SOURCE).__qualname__}"
    return analysis_cache.signature_of(source=source)


def _compute_demo_body(variable: str) -> dict:
    """Run the cry-wolf pipeline once per variable and build the JSON body."""
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
        "source": _provenance(variable),
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
        # Per-pixel p-values so a dot's hover readout can quote the number the
        # verdict rests on, not just the verdict.
        "p_value": _grid(res.p_value.values),
        # One global anomaly per year, for the warming-stripe band.
        "annual_anomaly": _annual_anomaly(da),
    }


@app.get("/catalog")
def get_catalog() -> list[dict]:
    """List the variables the active source can serve, with their metadata."""
    served = set(_served_variables())
    return [
        {
            "key": v.key,
            "label": v.label,
            "units": v.units,
            "source": _provenance(key),
            "description": v.description,
        }
        for key, v in catalog.VARIABLES.items()
        if key in served
    ]


# --- trend fingerprints -------------------------------------------------------

#: One fingerprint build at a time. Keyed on (selected variable, served set), so
#: switching variables rebuilds while switching back reuses the result. The lock
#: guards the dict; the expensive work happens on a daemon thread because five
#: cold variables is minutes, not milliseconds.
_FP_LOCK = threading.Lock()
_FP_STATE: dict = {}


def _region_labels(variable: str) -> tuple[np.ndarray | None, str | None]:
    """Corrected connected patches of one variable, or a reason there are none.

    Reuses the cached `/demo` body, so the region field costs nothing beyond the
    analysis the dashboard already asked for.
    """
    body = _demo_body(variable)
    mask = np.asarray(body["corrected_mask"], dtype=bool)
    if not mask.any():
        return None, "no corrected patches for this variable"
    labels, _ = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    return labels, None


def _slope_grid(variable: str) -> np.ndarray:
    """Per-pixel Theil-Sen slope for one variable, in catalog units."""
    body = _demo_body(variable)
    return np.asarray(body["slope"], dtype=float)


def _region_centroids(
    labels: np.ndarray, lat: np.ndarray, lon: np.ndarray, region_ids: list[int]
) -> dict[int, tuple[float, float]]:
    centroids = {}
    for region_id in region_ids:
        cells = np.argwhere(labels == region_id)
        if not cells.size:
            continue
        centroids[region_id] = (
            float(np.mean(lat[cells[:, 0]])),
            float(np.mean(lon[cells[:, 1]])),
        )
    return centroids


def _fingerprint_refresh(key, selected: str, served: list[str], labels: np.ndarray) -> None:
    """Run the build and guarantee the shared state leaves "preparing".

    The build runs on a daemon thread; if it raises anything other than the
    per-variable failures handled inside, the thread would die and the UI would
    poll "preparing 5 of 5" forever. Every other exit path sets a terminal
    status, so this one must too.
    """
    try:
        _fingerprint_build(key, selected, served, labels)
    except Exception as exc:  # noqa: BLE001 - a dead thread must not strand the UI
        with _FP_LOCK:
            if _FP_STATE.get("key") == key:
                _FP_STATE.update(
                    status="unavailable",
                    reason=f"the fingerprint build failed: {exc}",
                )


def _fingerprint_build(key, selected: str, served: list[str], labels: np.ndarray) -> None:
    """Load and analyse every served variable, then store the fingerprint.

    Synchronous body of the background build, kept separate so tests can run it
    without racing a thread.
    """
    grid = _demo_grid(selected)
    lat = np.asarray(grid["lat"].values, dtype=float)
    lon = np.asarray(grid["lon"].values, dtype=float)
    shape = np.shape(labels)
    if shape != (lat.size, lon.size):
        # A body cached from an older dataset can describe another grid (the
        # former 2.5-degree temperature file was 37x72; the T62 grid is 47x96).
        # Every later step indexes one against the other, so refuse here with an
        # actionable reason instead of raising a boolean-index error.
        found = " by ".join(str(n) for n in shape)
        with _FP_LOCK:
            if _FP_STATE.get("key") == key:
                _FP_STATE.update(
                    status="unavailable",
                    reason=(
                        f"the patch field is {found} but the {selected} grid is "
                        f"{lat.size} by {lon.size}; the cached analysis is stale — "
                        "delete data/cache/analysis and reload"
                    ),
                )
        return

    slopes: dict[str, np.ndarray] = {}
    done = 0
    for name in served:
        try:
            slopes[name] = _slope_grid(name)
        except Exception:  # a variable the source cannot actually produce
            pass
        done += 1
        with _FP_LOCK:
            if _FP_STATE.get("key") == key:
                _FP_STATE["progress"] = {"done": done, "total": len(served)}

    if len(slopes) < 2:
        with _FP_LOCK:
            if _FP_STATE.get("key") == key:
                _FP_STATE.update(
                    status="unavailable",
                    reason="fewer than two variables could be analysed",
                )
        return

    rv = fingerprint.region_vectors(slopes, labels)
    z, kept = fingerprint.standardize(rv.matrix, rv.variables)
    if len(kept) < 2:
        # With one surviving axis the "distance" is meaningless; with none,
        # every region sits at distance 0. Either way it is not a fingerprint.
        with _FP_LOCK:
            if _FP_STATE.get("key") == key:
                _FP_STATE.update(
                    status="unavailable",
                    reason="fewer than two variables could be standardized",
                )
        return
    rings = fingerprint.region_rings(labels, lat, lon)
    centroids = _region_centroids(labels, lat, lon, rv.region_ids)

    with _FP_LOCK:
        if _FP_STATE.get("key") == key:
            _FP_STATE.update(
                status="ready",
                reason=None,
                progress={"done": len(served), "total": len(served)},
                labels=labels,
                lat=lat,
                lon=lon,
                region_ids=rv.region_ids,
                vectors=rv.matrix,
                usable=rv.usable,
                variables=rv.variables,
                z=z,
                kept=kept,
                rings=rings,
                centroids=centroids,
            )


def _start_fingerprint_thread(key, selected: str, served: list[str], labels: np.ndarray) -> None:
    threading.Thread(
        target=_fingerprint_refresh,
        args=(key, selected, served, labels),
        daemon=True,
    ).start()


def _nearest_region_row(
    labels: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    lat0: float,
    lon0: float,
    region_ids: list[int],
    centroids: dict[int, tuple[float, float]],
) -> int | None:
    """Row index of the region under the click, or the nearest centroid.

    The click is snapped because a fingerprint request lands on whatever pixel
    the user hit, while regions are patch-shaped; nearest-centroid keeps the
    answer meaningful when the click falls just outside a patch.
    """
    if not region_ids:
        return None

    if np.isfinite(lat0) and np.isfinite(lon0):
        i = int(np.abs(lat - lat0).argmin())
        # Compare longitudes on the shorter way round, so the seam at 180
        # degrees does not look like half a world away.
        wrapped = np.abs(((lon - lon0 + 180.0) % 360.0) - 180.0)
        j = int(wrapped.argmin())
        label = int(labels[i, j])
        if label in centroids:
            return region_ids.index(label)

    if not centroids:
        return 0
    target = min(
        centroids.values(),
        key=lambda c: (c[0] - lat0) ** 2
        + (((c[1] - lon0 + 180.0) % 360.0) - 180.0) ** 2,
    )
    for region_id, centroid in centroids.items():
        if centroid == target:
            return region_ids.index(region_id)
    return 0


def _fingerprint_payload(state: dict, lat: float, lon: float, k: int) -> dict:
    """Build the ready response: the reference region and its nearest twins."""
    region_ids = state["region_ids"]
    vectors = state["vectors"]
    variables = state["variables"]
    kept = state["kept"]
    centroids = state["centroids"]
    rings = state["rings"]
    usable = state["usable"]
    z = state["z"]

    def vector_of(row: int) -> dict:
        return {
            name: (None if not np.isfinite(vectors[row, col]) else round(float(vectors[row, col]), 6))
            for col, name in enumerate(variables)
        }

    def describe(row: int, distance: float | None = None) -> dict:
        region_id = region_ids[row]
        centroid = centroids.get(region_id, (0.0, 0.0))
        item = {
            "region_id": region_id,
            "lat": round(centroid[0], 4),
            "lon": round(centroid[1], 4),
            "vector": vector_of(row),
            "usable": int(usable[row]),
            "rings": rings.get(region_id, []),
        }
        if distance is not None:
            item["distance"] = round(distance, 4)
        return item

    row = _nearest_region_row(
        state["labels"], state["lat"], state["lon"], lat, lon, region_ids, centroids
    )
    if row is None:
        return {"reference": None, "matches": []}

    def standardized_of(which: int) -> dict:
        return {
            name: (
                None
                if not np.isfinite(z[which, col])
                else round(float(z[which, col]), 4)
            )
            for col, name in enumerate(kept)
        }

    reference = describe(row)
    reference["standardized"] = standardized_of(row)

    matches = []
    for hit_row, distance in fingerprint.nearest(z, row, k):
        item = describe(hit_row, distance)
        item["standardized"] = standardized_of(hit_row)
        # Each variable's signed contribution to the distance: how far the match
        # sits from the reference on that axis. Sorting by magnitude names the
        # drivers ("matched mainly on drying"), without claiming a season.
        item["contribution"] = {
            name: (
                None
                if not (np.isfinite(z[hit_row, col]) and np.isfinite(z[row, col]))
                else round(float(z[hit_row, col] - z[row, col]), 4)
            )
            for col, name in enumerate(kept)
        }
        matches.append(item)
    regions = [
        {
            "region_id": region_id,
            "lat": round(centroid[0], 4),
            "lon": round(centroid[1], 4),
        }
        for region_id, centroid in centroids.items()
    ]
    return {
        "reference": reference,
        "matches": matches,
        "kept": kept,
        "regions": regions,
    }


@app.get("/fingerprint/{variable}")
def get_fingerprint(
    variable: str,
    lat: float = 0.0,
    lon: float = 0.0,
    k: int = 5,
) -> dict:
    """Regions elsewhere on the map whose trends look like the one you clicked.

    The first call starts a background build and reports progress; later calls
    return the reference region, its nearest matches, and the outlines to draw.
    """
    served = _served_variables()
    if variable not in served:
        raise HTTPException(
            status_code=404,
            detail=f"data source {type(_SOURCE).__name__} cannot serve {variable!r}",
        )

    base = {
        "progress": {"done": 0, "total": len(served)},
        "reference": None,
        "matches": [],
        "variables": list(served),
    }

    if len(served) < 2:
        return {
            **base,
            "status": "unavailable",
            "reason": "a fingerprint needs at least two served variables",
        }

    labels, reason = _region_labels(variable)
    if labels is None:
        return {**base, "status": "ready", "reason": reason}

    key = (variable, tuple(served))
    start = False
    with _FP_LOCK:
        if _FP_STATE.get("key") != key:
            _FP_STATE.clear()
            _FP_STATE.update(
                key=key,
                status="preparing",
                reason=None,
                progress={"done": 0, "total": len(served)},
                labels=labels,
                variables=list(served),
            )
            start = True
        snapshot = dict(_FP_STATE)

    if start:
        # Start outside the lock: the builder takes the same lock to report
        # progress, and a synchronous test double would otherwise deadlock.
        _start_fingerprint_thread(key, variable, served, labels)
        with _FP_LOCK:
            # Adopt the builder's result only while the shared state is still
            # ours. A concurrent request for a different variable may have
            # replaced it; answering with that payload would mislabel another
            # variable's fingerprint as this one's, so keep our "preparing".
            if _FP_STATE.get("key") == key:
                snapshot = dict(_FP_STATE)

    if snapshot["status"] == "preparing":
        return {**base, "status": "preparing", "progress": snapshot["progress"]}

    if snapshot["status"] == "unavailable":
        return {
            **base,
            "status": "unavailable",
            "reason": snapshot.get("reason"),
            "progress": snapshot["progress"],
        }

    return {
        **base,
        "status": "ready",
        "reason": None,
        "progress": snapshot["progress"],
        "variables": snapshot["variables"],
        **_fingerprint_payload(snapshot, lat, lon, k),
    }


@app.get("/demo/{variable}")
def get_demo(variable: str) -> dict:
    """Cry-wolf result for a variable: naive vs field-significance-corrected."""
    _require_served(variable)
    return _demo_body(variable)


@app.get("/timeseries/{variable}")
def get_timeseries(
    variable: str,
    lat: float = Query(...),
    lon: float = Query(...),
) -> dict:
    """Return the nearest-cell monthly series for a variable."""
    _require_served(variable)

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
        "time_kind": _time_kind(da),
        "time_labels": _time_labels(da),
        "value": [None if not np.isfinite(v) else float(v) for v in series],
    }


@app.get("/health")
def health() -> dict:
    """Liveness probe: never touches the data source, so it cannot time out."""
    return {"status": "ok"}


@app.middleware("http")
async def _no_store_frontend(request, call_next):
    """Serve the frontend shell and assets uncached.

    Caching the HTML shell is the one failure the ``?v=`` query bump cannot
    fix: a stale shell keeps pointing at the old script URLs, so a visitor can
    reload forever and never receive a new feature. Dev serves everything
    no-store; the version bump stays as a belt-and-braces cache key.
    """
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


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
