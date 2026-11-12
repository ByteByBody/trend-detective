# Trend Detective — Architecture

How the pieces fit together: the components, the path a request takes through them, and
the reasoning behind the structural choices. The mathematics of each analysis step, with
pseudocode, lives in [`algorithms.md`](algorithms.md).

## Components

```
   data sources              analysis pipeline                API            client
┌────────────────────┐   ┌───────────────────────────┐   ┌──────────────┐   ┌──────────────────┐
│ psl  NOAA, global  │   │ deseasonalize             │   │ /catalog     │   │ trend-layer.js   │
│ power NASA, 10 deg │──▶│ Theil–Sen slope           │──▶│ /demo/{var}  │──▶│ projection.js    │
│ synthetic truth    │   │ Mann–Kendall + Hamed–Rao  │   │ /timeseries  │   │ chart-view.js    │
└────────────────────┘   │ BH-FDR + coherence gate   │   │ /fingerprint │   │ format.js        │
                         │ Moran's I                 │   │ /health      │   │ app.js           │
                         └───────────────────────────┘   └──────────────┘   └──────────────────┘
```

| component | module | responsibility |
| --- | --- | --- |
| catalogue | `trend_detective/catalog.py` | the five variables, their labels and units |
| tunables | `trend_detective/config.py` | source selection, FDR level, gate sizes, span and stride |
| data sources | `trend_detective/data/` | PSL, POWER and synthetic readers, the download cache, the analysis cache |
| analysis | `trend_detective/analysis/` | decomposition, trend tests, field significance, coherence, fingerprints, the pipeline |
| API | `trend_detective/api/app.py` | JSON endpoints, the persisted analysis cache, static hosting |
| client | `web/` | one Leaflet map, the raster builder, the Plotly chart |

The analysis layer knows nothing about HTTP, and the API layer knows nothing about
statistics beyond calling `pipeline.cry_wolf`. That seam is what let a second data source
be added without touching the trend code.

## How a request travels

1. The browser loads `/`. The dev server serves the shell and every `/static` asset with
   `Cache-Control: no-store`, so a reload always picks up the current build. The `?v=`
   query on each asset is a second cache key.
2. `app.js` fetches `/catalog` and builds the variable chips from it, so the UI can only
   offer variables the active source can actually serve.
3. Selecting a variable fetches `/demo/{variable}`. On a cold process that request pays
   the whole cost: resolve the source, load the field (downloading and caching raw chunks
   on the first ever run), run `cry_wolf`, then write the finished body to
   `data/cache/analysis/`. Warm runs return the stored body in under a second.
4. The payload carries both masks, the per-pixel slope and p-value, the counts, the
   cutoff, Moran's I and the annual anomaly series. The client builds three RGBA rasters
   per variable (naive, corrected and the dropped ghost) and the mode switch swaps image
   opacity rather than refetching anything.
5. Clicking the map snaps to the nearest grid cell, reads the two verdicts straight out
   of the already-loaded payload, and calls `/timeseries` for that cell's monthly series.
6. The fingerprint button calls `/fingerprint`. That build needs all five variables, so it
   runs on a daemon thread and the first response says `preparing` with progress.

## Data sources

Three implementations behind one interface (`data.base.DatasetSource.load(variable) ->
DataArray`), selected by `config.DATA_SOURCE`.

- `psl` is the shipped default and the only source that fills a world map. One NCSS
  request per five-year chunk, cached to disk, then decimated to a 3.75° lattice.
- `power` is a live NASA MERRA-2 reader capped at 10°×10° per request, so it serves a
  regional box. It maps two catalogue variables and no others.
- `synthetic` is the bundled known-truth grid used by the tests.

Sources declare what they can serve through `variables()`, and `base.available_variables`
falls back to the whole catalogue for sources that do not. Units are converted at the
source boundary, so every layer above assumes catalogue units. Details per source:
[`algorithms.md`](algorithms.md) §9 and §10.

## Analysis pipeline

`pipeline.cry_wolf` runs one fixed order, and the order matters:

```
deseasonalize  →  Theil–Sen slope + Mann–Kendall (Hamed–Rao)  →  valid-fraction gate
               →  naive mask (p < 0.05)
               →  Benjamini–Hochberg (q = 0.05)  →  connected-patch gate (>= 5 px)
               →  Moran's I diagnostic
```

Hamed–Rao has to come first because it repairs the p-values themselves; BH then corrects
for the number of tests, and the patch gate removes the lone survivors that no field-wide
test can defend. The per-step math is in [`algorithms.md`](algorithms.md) §2–§7.

The result is one frozen object carrying both verdicts, so nothing downstream ever has to
decide which one is "the" answer.

## API

Six routes, all thin. `/demo` and `/timeseries` read one variable; `/fingerprint` builds
across all five. `/health` deliberately never touches the data source, because a cold PSL
load takes seconds and a probe that waits on it would report a healthy app as down.
Endpoint-by-endpoint detail: [`algorithms.md`](algorithms.md) §8.

## Web client

Five vanilla-JS modules with no build step and no framework: `projection.js` maps
grid rows to Mercator rows, `format.js` handles numbers and units, `trend-layer.js` owns
cell lookup, classification, the geographic field box and the raster builder, `chart-view.js`
owns the Plotly chart, and `app.js` orchestrates.

The invariant that keeps the map honest is `TDTrend.fieldBounds`: one function returns the
geographic rectangle that the image overlay, the stage border, `fitBounds` and the
per-pixel longitudes all read. When those four computed their own reaches, the overlay
spanned 358.125° while the pixels were mapped as 360°, and every painted cell sat west of
its own meridian by up to 1.86°. The failure mode is invisible by construction now.

## Persistence

| cache | location | keyed by | invalidated by |
| --- | --- | --- | --- |
| raw chunks | `data/cache/<var>.<years>.s<stride>.nc` | variable, year range, stride | deleting the file |
| analysis bodies | `data/cache/analysis/<var>.<signature>.json` | `ANALYSIS_SCHEMA` plus source, span, stride, gates, libraries, NumPy version | any input that goes into the signature, plus `ANALYSIS_SCHEMA` for code changes the signature cannot see |
| in-process | `lru_cache` in the API | variable | a restart |

Both on-disk trees are gitignored. A cache hit still runs the validator but costs no
network. See [`algorithms.md`](algorithms.md) §13 for the signature internals.

## Tunables

| constant | value | effect |
| --- | --- | --- |
| `DATA_SOURCE` | `"psl"` | which source the API serves |
| `FDR_Q` | `0.05` | false-discovery rate for the corrected verdict |
| `MIN_VALID_FRACTION` | `0.8` | months required before a pixel is tested |
| `MIN_PATCH_SIZE` | `5` | smallest connected patch that survives |
| `PSL_START` / `PSL_END` / `PSL_STRIDE` | `1980` / `2024` / `2` | global span and decimation |
| `PSL_CHUNK_YEARS` | `5` | years per download request |
| `POWER_BBOX` / `POWER_START` / `POWER_END` | Greenland, `1990`, `2024` | regional request |

Every one of them is a function argument somewhere, not a literal buried in the analysis
code.

## Testing

The Python suite runs under pytest with `pythonpath = ["."]`. The front-end modules have no
separate toolchain: the tests shell out to `node`, `require` the UMD module, and assert on
JSON, the same way they assert on Python objects. That covers cell snapping, raster
longitudes and the chart config without adding a JS test runner to the project.

## Structural decisions

- **Ship both verdicts.** The naive map and the corrected map travel in one payload and one
  screen, and the dropped cells stay visible. Shipping only the corrected map would hide the
  problem the project exists to show.
- **Canvas raster per mode, not one polygon per pixel.** 3,456 flagged cells would be 3,456
  Leaflet objects per view, with sluggish toggling. Three prebuilt images toggle by opacity
  and cost nothing to switch.
- **Per-pixel Python loops for the slope and the MK test.** Measured faster than the
  vectorised block here, because at most one pair vector exists at a time and the small
  contiguous vector is cache-friendly.
- **Exact nearest neighbour for fingerprints.** At a few dozen regions an HNSW index adds a
  dependency and an approximation for no measurable gain.
- **NCSS NetCDF3 instead of OPeNDAP or the raw HDF5 file.** The raw file needs an HDF5
  reader that has no wheel on this Python, and OPeNDAP took over 7.5 minutes for one global
  slice and paid it again on every restart.
- **Units converted in the source, not the UI.** Five variables arrive in four different
  unit systems; converting once at the boundary means the chart, the slope map and the
  fingerprint vectors all assume the same thing.

## Deferred

- The Earthdata-login fetcher for GES DISC products (`data/fetch.py` raises
  `NotImplementedError`).
- An HNSW index for fingerprints, once the region count justifies it.
- Confidence intervals shown beside p-values.
- A "not enough years yet" mode for records that a longer span would make significant.
