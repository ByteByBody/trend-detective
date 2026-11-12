# Trend Detective

NASA Space Apps 2026 entry for **Be An Earth System Trend Detective!** (Advanced-only).

This repository currently implements the **cry-wolf slice**: it computes naive per-pixel
trend significance and field-significance-corrected significance on a gridded Earth variable,
and shows both readings on one map with a Naive/Corrected switch — the "detective who
won't cry wolf."

- Current UI design: `docs/superpowers/specs/2026-11-12-single-map-redesign-design.md`
- Current UI plan: `docs/superpowers/plans/2026-11-12-single-map-redesign.md`
- Original analysis design: `docs/superpowers/specs/2026-11-03-trend-detective-design.md`
- Original implementation plan: `docs/superpowers/plans/2026-11-03-trend-detective-cry-wolf.md`
- Plain-English concepts: `docs/plain-english-concepts.md`
- Presentation Q&A: `docs/presentation-qa.md`
- Algorithms (math + pseudocode): `docs/algorithms.md`

## What it does

For each pixel: deseasonalize, estimate the Theil–Sen slope, and run a
Mann–Kendall test whose variance is inflated for autocorrelation (a lag-1
AR(1) correction estimated on the detrended residual, the "memory fix"). Two
verdicts are then compared:

- **Naive** — flag every pixel with `p < 0.05`.
- **Corrected** — Benjamini–Hochberg FDR at `q = 0.05`, then keep only pixels in
  a connected patch of at least 5 (queen connectivity).

On the shipped PSL surface-temperature field the naive test flags 3,456 pixels;
the correction drops 49 of them and keeps 3,407. Other variables trade fewer
pixels: soil moisture drops 68 of 1,062, precipitation 178 of 2,856, and snow
water equivalent goes from 5 naive flags to 0 survivors. The correction is not
there to erase most of the map; it is there to refuse the claims the field as a
whole will not stand behind.

## Real data

Two live sources, neither needing an API key. `DATA_SOURCE` in
`trend_detective/config.py` picks one:

| source | coverage | fetch |
| --- | --- | --- |
| `psl` | **whole world**, 1.875°, monthly from 1948 | one subset request, then cached |
| `power` | one 10°×10° box (endpoint limit) | one request per tile |
| `synthetic` | whole world, fabricated with known truth | none, instant |

`psl` reads NOAA's PSL/THREDDS reanalysis (NOAA, not NASA) through its NetCDF
Subset Service, and ships decimated to a 3.75° lattice over 1980–2024: 47×96
pixels, one analysis per variable (0.9 s to load once warm), versus roughly four
times the pixels at full 1.875°. Tune with
`PSL_START`, `PSL_END`, `PSL_STRIDE` (and `PSL_CHUNK_YEARS`, the years per
request; the server cuts off very long ones).

```python
DATA_SOURCE = "psl"
PSL_START, PSL_END, PSL_STRIDE = 1980, 2024, 2   # stride 1 = 1.875 deg, 4x the pixels
```

```python
DATA_SOURCE = "power"                             # NASA MERRA-2, regional only
POWER_BBOX = (60.0, 70.0, -50.0, -40.0)           # lat_min, lat_max, lon_min, lon_max
POWER_START, POWER_END = 1990, 2024
```

POWER's regional endpoint is capped at **10°×10° per request**, so it cannot cover
a world map: the shipped default is a Greenland box. `power` maps
`surface_temperature` → `T2M` and `precipitation` → `PRECTOTCORR`. `psl` serves
all five catalog variables — air temperature, precipitation rate, snow water
equivalent, 10–200 cm soil moisture, and latent heat flux — and converts each
file's raw units to the catalog's units at the source boundary, so nothing
downstream ever sees a mixed-unit field:

| variable | PSL file units | conversion | catalog units |
| --- | --- | --- | --- |
| `surface_temperature` | K | − 273.15 | °C |
| `snow_water_equivalent` | kg/m² | identity | kg/m2 |
| `precipitation` | kg/m²/s | × 86,400 | mm/day |
| `soil_moisture_rootzone` | fraction | × 1000 × 1.9 m | kg/m² |
| `evapotranspiration` | W/m² | ÷ 2.5×10⁶ × 86,400 | mm/day |

All five files sit on PSL's T62 Gaussian grid, so every variable shares one
horizontal grid and the trend fingerprint can compare them at a single pixel.
Temperature reads `surface_gauss/air.2m.mon.mean.nc` — the true 2 m field — not
the 2.5° `surface/air.mon.mean.nc` twin, which holds the 0.995 sigma level.

`/catalog` lists only what
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

The FastAPI service also persists each variable's finished cry-wolf body under
`data/cache/analysis/` (keyed by an `ANALYSIS_SCHEMA` fingerprint of the source,
grid, and correction). A server restart therefore reuses the 5–11 minute
analysis instead of recomputing it; changing the analysis code or grid bumps the
fingerprint and invalidates the old body automatically.

## Scope (this build)

Cry-wolf slice only. The Earthdata-login fetcher (`trend_detective/data/fetch.py`,
GES DISC) is deferred behind a pluggable data-source interface
(`trend_detective/data/base.py`). Seasonality removal defaults to the
per-calendar-month climatology method; STL is used automatically when
`statsmodels` is installed. Trend fingerprints compare regions across all served
variables with an **exact** nearest-neighbour search: at a few dozen regions the
HNSW index the original design anticipated buys nothing, so it stays deferred.

## Development

Install once, editable, so `import trend_detective` resolves from any directory
(uvicorn's `--reload` runs the app in a spawned child; without this, a shell that
is not in the repo root fails with `ModuleNotFoundError: No module named
'trend_detective'`):

```bash
python -m pip install -e . --no-deps
```

```bash
python -m pytest -q
```

Warm the cache before a demo (first run only — this is the one-time download):

```bash
python -c "from trend_detective.data import base; base.get_source('psl').load('surface_temperature')"
```

The dev server serves the HTML shell and every `/static` asset with
`Cache-Control: no-store`, so a reload always picks up the current build; the
`?v=` query on each asset stays as a second cache key. The masthead prints the
build stamp (`build 7`) next to the provenance line, so you can tell at a glance
whether a tab is stale. If a page still looks old after a normal refresh, open a
private window — an already-cached shell will not ask the server again.

`--reload` needs `watchfiles` (already in the environment); without it uvicorn
starts but silently never reloads Python changes.

Run the web app (serves the map at `/`):

```bash
python -m uvicorn trend_detective.api.app:app --reload
```

The UI is one square Leaflet map over NASA GIBS Blue Marble imagery, with a
Naive/Corrected switch and the pixel counts beside it. Flagged pixels are filled
by trend magnitude, faint near zero and solid at the extremes; a one-pixel
outline marks the patch boundary, solid for survivors and dashed ember for the
pixels the correction drops. Unflagged cells stay transparent, so the basemap
remains readable. A blurred copy of the visible raster fills the stage wings, so
the square world does not float on empty chrome.

Clicking anywhere snaps to the nearest grid cell, outlines that cell as an area,
and fills the readout with its trend, p-value, and both verdicts. The raster and
that outline share one `fieldBounds`, so a painted cell stays inside the
rectangle that marks it. The monthly
chart below is an explorable Plotly view: wheel or pinch zooms around the
cursor, drag pans, double-click resets. The selected cell, mode, and variable
are shareable through the query string.

Every number on the page carries its own unit, thousands are separated, and
trends are quoted **per decade** (the monthly slope × 120) rather than per month;
scientific notation never reaches the screen. The masthead's stripe band is the
selected variable's global annual anomaly, one year per stripe. The fingerprint
button outlines the selected region and its nearest neighbours across all five
variables, polling a background build on the first run and snapping map clicks
to region centres. Moran's I, the cutoff, coverage, grid size, provenance, and
the correction paragraph live in a Method dialog instead of on the page.

`GET /health` returns `{"status": "ok"}` without touching the data source, so a
probe never fails just because a cold fetch is in progress.
