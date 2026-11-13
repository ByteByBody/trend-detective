# Trend Detective

A climate trend map that shows its own false alarms.

Most trend maps test every pixel on its own. On a grid of thousands of pixels that
guarantees a crop of false positives: measured on the shipped field, a plain per-pixel
test at `p < 0.05` flags 3,456 cells. Trend Detective computes that naive verdict and a
field-corrected verdict from the same data, ships both in one payload, and puts a switch
between them on one map. The cells the correction drops stay visible on the naive side,
so the disagreement between the two readings is the exhibit rather than something hidden.

Built for the NASA Space Apps 2026 challenge **Be An Earth System Trend Detective!**
(advanced track).

## What it does

For every pixel of a monthly gridded field:

1. Remove the seasonal cycle, because a 12-month swing can otherwise read as a trend.
2. Estimate the trend as a Theil–Sen slope, quoted per decade.
3. Run a Mann–Kendall test, with the variance inflated by a Hamed–Rao correction for
   autocorrelation. Climate series remember last month, and that memory makes a random
   wander look like a trend.
4. Compare two verdicts. **Naive** flags everything with `p < 0.05`. **Corrected** passes
   those p-values through Benjamini–Hochberg at `q = 0.05` and then keeps only pixels
   inside a connected patch of at least five.
5. Mark records that jump from one level to another. A 60-vs-60-month median step that
   clears fifteen robust field sigmas and is still there at the end of the record is a
   record defect rather than a climate trend, so the cell gets a violet marker — in both
   modes, without changing either verdict.

Results on the shipped field, one analysis per variable:

| variable | naive flags | corrected | dropped | step cells |
| --- | --- | --- | --- | --- |
| Surface air temperature | 3,456 | 3,407 | 49 | 0 |
| Evapotranspiration | 2,947 | 2,756 | 191 | 0 |
| Precipitation | 2,856 | 2,678 | 178 | 0 |
| Root-zone soil moisture | 1,062 | 994 | 68 | 1 |
| Snow water equivalent | 5 | 0 | 5 | 0 |

Snow water equivalent is the clean empty case: five scattered flags, no patch behind any
of them, so the corrected map draws nothing.

One soil-moisture record jumps and stays jumped, which is the shape of an upstream ingest
defect rather than a climate trend. It sits at (80.95°N, −67.50°E), where the record holds
190.19 kg m-2 through 1998 and about 729 after: a step of 496.94 kg m-2, and the level at
the end of the record is still 537.96 kg m-2 above where it started, while the neighbouring
cells' end-of-record shifts stay within 1.78. Three more soil pixels clear the magnitude
cut but return to their old level, and the persistence gate drops all three.

## The map

One square Leaflet map over NASA GIBS Blue Marble imagery, with a Naive/Corrected switch
and both pixel counts beside it.

- Flagged cells are filled by trend magnitude, faint near zero and solid at the extremes.
- A one-pixel outline marks the patch edge: solid for survivors, dashed ember for the
  pixels the correction drops.
- A record with a sustained level change gets a violet marker in every mode. It is a data
  quality warning, not a third verdict, so it never moves a cell between naive and
  corrected.
- Unflagged cells stay transparent, so the basemap remains readable.
- Clicking anywhere snaps to the nearest grid cell, outlines it as an area and fills the
  readout with its trend, p-value and both verdicts.
- The chart below is an explorable Plotly view: wheel or pinch to zoom around the cursor,
  drag to pan, double-click to reset.
- Fingerprint search finds the regions whose trends across all five variables look most
  like the selected one.
- The warming-stripe band under the header is the selected variable's global annual
  anomaly, one year per stripe.

Every number on the page carries its own unit, thousands are separated, and trends are
quoted per decade (the monthly slope × 120) instead of per month. Scientific notation
never reaches the screen.

## Data

Two live sources and one synthetic grid, none needing an API key. `DATA_SOURCE` in
`trend_detective/config.py` picks one.

| source | coverage | fetch |
| --- | --- | --- |
| `psl` | whole world, 1.875°, monthly from 1948 | one subset request per five-year chunk, then cached |
| `power` | one 10°×10° box (endpoint limit) | one request per tile |
| `synthetic` | whole world, fabricated with known truth | none, instant |

The shipped default is `psl`: NOAA PSL / NCEP-NCAR reanalysis through the THREDDS NetCDF
Subset Service, decimated to a 3.75° lattice over 1980–2024. That is 47 × 96 pixels and
540 months, one analysis per variable, about 0.9 s to load once warm. Roughly four times
the pixels are available at the native 1.875°. Tune with `PSL_START`, `PSL_END`,
`PSL_STRIDE` and `PSL_CHUNK_YEARS`.

The `psl` source serves all five catalogue variables and converts each file's raw units
at the source boundary, so nothing downstream ever sees a mixed-unit field:

| variable | PSL file units | conversion | catalog units |
| --- | --- | --- | --- |
| `surface_temperature` | K | − 273.15 | °C |
| `snow_water_equivalent` | kg/m2 | identity | kg/m2 |
| `precipitation` | kg/m2/s | × 86,400 | mm/day |
| `soil_moisture_rootzone` | fraction | × 1000 × 1.9 m | kg/m2 |
| `evapotranspiration` | W/m2 | ÷ 2.5e6 × 86,400 | mm/day |

Precipitation needs no density factor. `kg/m2` is already a water depth, since 1 kg of
water over 1 m2 is 1 mm, so only seconds convert to days. Soil moisture does need one,
because its input is a dimensionless volume fraction rather than a per-area mass.

All five files sit on PSL's T62 Gaussian grid, so one horizontal grid is shared and the
fingerprint can compare variables at a single pixel. Temperature reads
`surface_gauss/air.2m.mon.mean.nc`, the true 2 m field, rather than the 2.5° `surface/`
twin that holds the 0.995 sigma level.

`power` uses NASA MERRA-2 and is capped at 10°×10° per request, so it cannot cover a world
map; the shipped default for it is a Greenland box. It maps `surface_temperature` → `T2M`
and `precipitation` → `PRECTOTCORR`.

`/catalog` lists only what the active source serves, so the UI never offers a variable
that would fail, and reports the source that actually produced the data. PSL says NOAA,
not MERRA-2.

### Caching

The first `psl` run downloads the subset into `data/cache/<var>.<years>.s<stride>.nc`,
five years per request, and validates each chunk against its declared length, the NetCDF
magic bytes and the reader itself. A truncated transfer or an HTML error page is
discarded instead of cached. Every later run opens the local file, so the wait is
one-time and a rerun resumes from the chunks already on disk. NCSS returns classic
NetCDF3, which `scipy` reads, so the source needs no extra dependency and no HDF5 reader.

The API also persists each variable's finished cry-wolf body under
`data/cache/analysis/`, keyed by an `ANALYSIS_SCHEMA` fingerprint of the source, grid and
correction parameters. A server restart reuses the minutes-long analysis instead of
recomputing it, and changing the analysis code or the grid invalidates the old body
automatically. Delete `data/cache/` to force a fresh download.

## Quick start

Python 3.13 or newer.

```bash
python -m pip install -e . --no-deps
```

Install it editable even for a plain server run. Uvicorn's `--reload` runs the app in a
spawned child, and without the install a shell that is not in the repo root fails with
`ModuleNotFoundError: No module named 'trend_detective'`.

Run the app, which serves the map at `/`:

```bash
python -m uvicorn trend_detective.api.app:app --reload
```

Then open `http://127.0.0.1:8000`. The first request warms the cache and can take about
ten minutes; later visits reuse it. To warm it ahead of a demo:

```bash
python -c "from trend_detective.data import base; base.get_source('psl').load('surface_temperature')"
```

`--reload` needs `watchfiles`. Without it uvicorn starts but silently never reloads
Python changes.

The dev server serves the HTML shell and every `/static` asset with `Cache-Control:
no-store`, so a reload always picks up the current build, and the `?v=` query on each
asset stays as a second cache key. The masthead prints the build stamp next to the
provenance line. If a page still looks old after a normal refresh, open a private window,
because an already-cached shell will not ask the server again.

## API

| endpoint | returns |
| --- | --- |
| `GET /catalog` | the variables the active source can serve, with units and true provenance |
| `GET /demo/{variable}` | masks including step changes, per-pixel slope and p-value, counts, cutoff, Moran's I, annual anomaly |
| `GET /timeseries/{variable}?lat=&lon=` | the nearest cell's monthly series, with axis labels |
| `GET /fingerprint/{variable}?lat=&lon=&k=` | the clicked region and its nearest twins across all served variables |
| `GET /health` | `{"status": "ok"}`, without touching the data source |
| `GET /` | the map |

`/fingerprint` builds in the background on first call and reports `preparing` with
progress before it reports `ready`.

## Project layout

```
trend_detective/
  analysis/     trend tests, field significance, coherence, step changes, fingerprints, the pipeline
  api/          FastAPI app and the persisted analysis cache
  data/         PSL, POWER and synthetic sources, the download cache, the sample grid
  catalog.py    the five variables, their labels and units
  config.py     source selection and tunables
web/            vanilla-JS client: projection, formatting, raster builder, chart, app
tests/          pytest suite; the JS modules are exercised through node
docs/           architecture.md, algorithms.md
data/cache/     gitignored downloads and analysis bodies
```

## Development

```bash
python -m pytest -q
```

`tests/test_api.py` recomputes synthetic cry-wolf bodies and takes a few minutes, so it can
be skipped while iterating:

```bash
python -m pytest -q --ignore=tests/test_api.py
```

That is the command CI runs on every push (`.github/workflows/tests.yml`), on Python 3.13
with node for the front-end probes.

The front-end modules are checked by the same suite through `node --check` and a few
hand-written probes, so no separate JS toolchain is needed.

Two references. [`docs/architecture.md`](docs/architecture.md) covers the components, the
path a request takes, persistence and the structural decisions.
[`docs/algorithms.md`](docs/algorithms.md) covers the step-by-step math with pseudocode.

## Scope and limitations

- Cry-wolf slice only. The Earthdata-login fetcher (`trend_detective/data/fetch.py`, GES
  DISC) is deferred behind the pluggable data-source interface.
- Linear trends only.
- Ties in the data make the no-tie Mann–Kendall variance slightly off. Noted and accepted
  for this slice.
- Reanalysis is a model fed by observations, not raw satellite pixels, and the app labels
  it that way.
- 45 years is short for slow variables.
- The 5% level and the 5-pixel gate are choices, not truths. Both are single constants.
- The longitude seam is treated as a boundary by the coherence gate, so patches do not
  wrap across it.
- Fingerprints use an exact nearest-neighbour search. The HNSW index the original design
  anticipated stays deferred until the region count justifies it.
- Seasonality removal defaults to the per-calendar-month climatology. STL is used
  automatically when `statsmodels` is installed.
- The step detector needs 60 months on each side of a jump, so it cannot place a step in
  the first or last five years of a record. It marks a broken record; it does not repair
  it.

## Credits

Data from NOAA PSL (NCEP-NCAR reanalysis), with a NASA POWER / MERRA-2 path for regional
fields. Basemap imagery from NASA GIBS Blue Marble. Built for NASA Space Apps 2026.
