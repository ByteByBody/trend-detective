# Trend Detective

NASA Space Apps 2026 entry for **Be An Earth System Trend Detective!** (Advanced-only).

This repository currently implements the **cry-wolf slice**: it computes naive per-pixel
trend significance and field-significance-corrected significance on a gridded Earth variable,
and shows them side by side — the "detective who won't cry wolf."

- Design doc: `docs/superpowers/specs/2026-11-03-trend-detective-design.md`
- Implementation plan: `docs/superpowers/plans/2026-11-03-trend-detective-cry-wolf.md`
- Plain-English concepts: `docs/plain-english-concepts.md`
- Algorithms (math + pseudocode): `docs/algorithms.md`

## What it does

For each pixel: deseasonalize, estimate the Theil–Sen slope, and run a
Mann–Kendall test whose variance is inflated for autocorrelation (a lag-1
AR(1) correction estimated on the detrended residual, the "memory fix"). Two
verdicts are then compared:

- **Naive** — flag every pixel with `p < 0.05`.
- **Corrected** — Benjamini–Hochberg FDR at `q = 0.05`, then keep only pixels in
  a connected patch of at least 5 (queen connectivity).

On the bundled sample the naive test cries wolf on ~98 pixels that the corrected
test rejects, while keeping every injected real patch.

## Real data

Two live sources, neither needing an API key. `DATA_SOURCE` in
`trend_detective/config.py` picks one:

| source | coverage | fetch |
| --- | --- | --- |
| `psl` | **whole world**, 2.5°, monthly from 1948 | one subset request, then cached |
| `power` | one 10°×10° box (endpoint limit) | one request per tile |
| `synthetic` | whole world, fabricated with known truth | none, instant |

`psl` reads NOAA's PSL/THREDDS reanalysis (NOAA, not NASA) through its NetCDF
Subset Service, and ships decimated to a 5° lattice over 1980–2024: 37×72 pixels,
43 s of analysis once warm (0.9 s to load), versus ~140 s at full 2.5°. Tune with
`PSL_START`, `PSL_END`, `PSL_STRIDE` (and `PSL_CHUNK_YEARS`, the years per
request; the server cuts off very long ones).

```python
DATA_SOURCE = "psl"
PSL_START, PSL_END, PSL_STRIDE = 1980, 2024, 2   # stride 1 = 2.5 deg, ~140 s
```

```python
DATA_SOURCE = "power"                             # NASA MERRA-2, regional only
POWER_BBOX = (60.0, 70.0, -50.0, -40.0)           # lat_min, lat_max, lon_min, lon_max
POWER_START, POWER_END = 1990, 2024
```

POWER's regional endpoint is capped at **10°×10° per request**, so it cannot cover
a world map: the shipped default is a Greenland box. `power` maps
`surface_temperature` → `T2M` and `precipitation` → `PRECTOTCORR`; `psl` maps
`surface_temperature` → the NCEP air-temperature grid. `/catalog` lists only what
the active source serves, so the UI never offers a variable that would fail, and
reports the source that actually produced it (`psl` says NOAA, not MERRA-2).

**PSL downloads once.** The subset is written to
`data/cache/<var>.<years>.s<stride>.nc` (gitignored), five years per request.
Every later run opens the local file, so the wait is one-time and a rerun resumes
from the chunks already on disk. The download is atomic and validated against the
declared length, the NetCDF magic bytes, and the reader itself: a truncated
transfer or an HTML error page is discarded instead of cached. NCSS returns classic
NetCDF3, which `scipy` reads — so the source needs **no extra dependency**, not
even an HDF5 reader.

Delete `data/cache/` to force a fresh download.

## Scope (this build)

Cry-wolf slice only. The Earthdata-login fetcher (`trend_detective/data/fetch.py`,
GES DISC) and the trend-fingerprint search are deferred behind a pluggable
data-source interface (`trend_detective/data/base.py`). Seasonality removal
defaults to the per-calendar-month climatology method; STL is used automatically
when `statsmodels` is installed.

## Development

```bash
python -m pytest -q
```

Warm the cache before a demo (first run only — this is the one-time download):

```bash
python -c "from trend_detective.data import base; base.get_source('psl').load('surface_temperature')"
```

Run the web app (serves the maps at `/`):

```bash
python -m uvicorn trend_detective.api.app:app --reload
```

The two panels are square Leaflet maps over NASA GIBS Blue Marble imagery. The
naive panel marks only the pixels it is being accused of: the ones the raw test
flags and the corrected test drops — 49 of 2,014 on the shipped PSL defaults.
The corrected panel has nothing left to mark, which is the point. Click any dot
to plot that cell's monthly series; a click between dots selects the nearest
marked cell, so a near miss still reads the right place.

`GET /health` returns `{"status": "ok"}` without touching the data source, so a
probe never fails just because a cold fetch is in progress.
