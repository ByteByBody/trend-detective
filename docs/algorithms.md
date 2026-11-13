# Trend Detective — Architecture and algorithms

How the tool is built: the analysis pipeline, the data sources, the API, and the
browser client. Each numbered section covers one module, and the source docstrings
point back here, so a section number works as a citable address. For the system-level
view, see [`architecture.md`](architecture.md).

Conventions: `x` is a one-dimensional monthly series (float, may contain NaN). `X` is
a grid of per-pixel results with shape `(ny, nx)`. `n` is the number of valid samples.
Everything is NaN-aware and nothing is imputed.

## At a glance

```
   data sources              analysis pipeline                API            client
┌────────────────────┐   ┌───────────────────────────┐   ┌──────────────┐   ┌──────────────────┐
│ psl  NOAA, global  │   │ deseasonalize             │   │ /catalog     │   │ trend-layer.js   │
│ power NASA, 10 deg │──▶│ Theil–Sen slope           │──▶│ /demo/{var}  │──▶│ projection.js    │
│ synthetic truth    │   │ Mann–Kendall + Hamed–Rao  │   │ /timeseries  │   │ chart-view.js    │
└────────────────────┘   │ BH-FDR + coherence gate   │   │ /fingerprint │   │ format.js        │
                         │ step-change detector      │   │ /health      │   │ app.js           │
                         │ Moran's I                 │   └──────────────┘   └──────────────────┘
                         └───────────────────────────┘
```

| area | module | responsibility |
| --- | --- | --- |
| catalogue | `trend_detective/catalog.py` | the five variables, their labels and units |
| tunables | `trend_detective/config.py` | source selection, FDR level, gate sizes, span and stride |
| sources | `trend_detective/data/` | PSL, POWER, synthetic, plus the download and analysis caches |
| analysis | `trend_detective/analysis/` | decomposition, trend tests, field significance, coherence, step changes, fingerprints |
| API | `trend_detective/api/app.py` | JSON endpoints, the analysis cache, static hosting |
| client | `web/` | one Leaflet map, the raster builder, the Plotly chart |

The product idea is one record with two verdicts. The **naive** verdict flags every
pixel whose own Mann–Kendall test returns `p < 0.05`. The **corrected** verdict passes
those p-values through Benjamini–Hochberg at `q = 0.05` and then keeps only pixels that
sit inside a connected patch of at least five. Both verdicts travel in the same
payload, and the map switch toggles between them.

## 1. Sample grid generator — `data/sample.py`

**Signature:** `generate_land_grid(seed=7, ny=45, nx=90, n_months=240, gap_fraction=0.0, autocorr=0.6) -> (xr.DataArray, GroundTruth)`

For each pixel `(i, j)` and month `t`:

```
value[t,i,j] = seasonal[t] + slope[i,j] * t + noise[t,i,j]
```

- **Seasonal cycle**, the same everywhere: `seasonal[t] = A * sin(2*pi * (t mod 12) / 12)`
  with amplitude `A` around 3.0. Across a whole number of years its contribution to an
  OLS slope is about zero.
- **Injected trend:** `slope` is zero except on a few contiguous rectangular patches
  (`true_patch_mask`), drawn from `{+0.5, -0.3}` per month (`true_slope`, units per
  month). Non-patch pixels get `0.0`.
- **AR(1) noise** with stationary variance `sigma^2`:

  ```
  noise[0] = w0                          w ~ N(0, sigma)
  noise[t] = autocorr * noise[t-1] + sqrt(1 - autocorr^2) * w_t
  ```

  `autocorr = 0` gives white noise. The `sqrt(1-phi^2)` scaling holds the variance near
  `sigma^2` for every `phi`, which keeps the slope-recovery test comparable across
  settings.
- **Gaps:** a random seeded `gap_fraction` of cells become NaN.

Determinism comes from `rng = numpy.random.default_rng(seed)` drawn in a fixed order.
Coordinates: `time` is the monthly index `0..n_months-1`, `lat` descends (90 to -90),
`lon` ascends (-180 to 180), dims in the order `("time", "lat", "lon")`.
`GroundTruth` carries `true_slope: (ny,nx) float` and `true_patch_mask: (ny,nx) bool`.

Bundled IO is `save_bundled(da, path)` and `load_bundled(path)` over xarray NetCDF;
`load_bundled` raises `FileNotFoundError` when the file is absent.

## 2. Seasonal decomposition — `analysis/decomp.py`

**Signature:** `deseasonalize(da, method="auto") -> xr.DataArray` (same shape and coords)

- **`climatology`:** for each calendar month `m` in `0..11`, compute
  `clim[m] = nanmean over all years of value[months where t mod 12 == m]`, then subtract
  `clim[t mod 12]` from every timestep.
- **`stl`:** `statsmodels.tsa.seasonal.STL(series, period=12, robust=True)` per pixel,
  subtracting the returned `seasonal` component.
- **`auto`:** `stl` when `statsmodels` imports, otherwise `climatology`. `statsmodels` is
  an optional accelerator rather than a declared dependency, so on a bare install `auto`
  means climatology and on this machine it means STL. STL dominates the whole pipeline:
  minutes for a world grid against seconds for climatology. That is the reason the API
  writes each finished body to disk (§14).
- **NaN rule:** where the input is NaN, the output is NaN. Nothing is filled.
- **STL fallback:** a pixel STL cannot handle, whether it has a missing month or a
  series shorter than two periods (< 24 months), falls back to **climatology** for that
  pixel alone. It is never left with its seasonal cycle in place, because such a pixel
  can still clear the valid-fraction gate and a 12-month swing would manufacture a trend.

## 3. Theil–Sen slope — `analysis/trend.py`

**Signature:** `theilsen_slope(da) -> xr.DataArray` (units per month)

For each pixel with a valid series `x_0..x_{n-1}`:

```
slope = median over all pairs i<j of (x_j - x_i) / (j - i)
```

**Implementation note (measured).** There are `n(n-1)/2` pairs, which is 28,680 for
`n=240`. The pair index vectors `i`, `j` and `j-i` are built once per grid and reused for
every pixel. The no-missing fast path takes `np.median` of a contiguous 1-D pair vector;
pixels with gaps take the NaN-aware per-pixel median.

This per-pixel form is faster than a vectorised `(pixels x pairs)` block, which was
implemented and measured first: at most one pair vector exists at a time, so the memory
pressure that motivates chunking never appears, and the small contiguous vector is
cache-friendly. Measured cold on a 45x90 grid: **3.7 s** per-pixel against 12.7 s
vectorised. Guard: `n < 2` returns NaN.

## 4. Mann–Kendall with the Hamed–Rao correction — `analysis/trend.py`

**Signatures:** `mann_kendall_pvalue(da, autocorr_correct=True) -> xr.DataArray` and
`trend_test(da, autocorr_correct=True) -> (slope, p_value)`

The Mann–Kendall statistic, with `sgn(x_j - x_i)` returning +1, 0 or -1:

```
S = sum_{i<j} sgn(x_j - x_i)
```

Variance without ties:

```
var_S = n * (n - 1) * (2n + 5) / 18
```

Hamed–Rao variance inflation (`autocorr_correct=True`) estimates the lag-1
autocorrelation `rho_1` of the linear-detrended residual under an AR(1) model:

```
r_t = x_t - (a + b t)                         # least-squares line removed
rho_1 = sum_{t=1}^{n-1} (r_t - rbar)(r_{t+1} - rbar) / sum_t (r_t - rbar)^2
rho_1 = clamp(rho_1, 0.0, 0.99)
var_S *= (1 + rho_1) / (1 - rho_1)          # floored at 1 (never less significance)
```

This is where "240 months becomes ~42 real facts" comes from: at `rho_1 = 0.7` the
effective-sample fraction is `(1-0.7)/(1+0.7) ≈ 0.176`.

**Why detrend first.** A genuine linear trend is itself highly autocorrelated. Measured
raw `rho_1` for an injected 0.5/month trend was 0.987, so estimating `rho` on the raw
series penalises the very trend being detected, with an inflation of about 151x that
hides it. The residual autocorrelation, measured at 0.538 for AR(1) noise with
`phi = 0.5`, is what the correction is meant to model.

**Why lag-1 and not all lags.** Summing sample autocorrelations across every lag is
unstable. Noisy high-lag estimates frequently shrink the variance and manufacture false
trends: measured, a white-noise series' false-positive rate rose from 4% to 23%. The
lag-1 form is stable, and the factor is floored at 1.0 so the correction never claims
more significance than the independent test. Negative autocorrelation is not exploited,
which is a documented limitation.

Standardised score with a continuity correction:

```
Z = (S - 1) / sqrt(var_S)   if S > 0
Z = (S + 1) / sqrt(var_S)   if S < 0
Z = 0                        if S = 0
```

Two-sided p-value: `p = 2 * (1 - Phi(|Z|)) = erfc(|Z| / sqrt(2))`, where `Phi` is the
standard normal CDF. `math.erfc` is enough, so SciPy is not needed.

Guards: `n < 3`, `var_S <= 0`, or a zero-variance series returns `p = NaN` rather than
"significant". NaN months are dropped per pixel before computing `n`, `S` and `rho`. As
in §3, indices are built once and the per-pixel loop measured fastest.

## 5. Field significance, BH-FDR — `analysis/significance.py`

**Signatures:** `benjamini_hochberg(p, q=0.05) -> (mask, threshold)` and
`naive_mask(p, alpha=0.05) -> mask`

```
finite  = p[isfinite(p)]; m = len(finite)
order   = argsort(finite, kind="stable")
ranks   = 1..m
thr     = max{ finite[order[k-1]] : finite[order[k-1]] <= (k / m) * q }
mask    = isfinite(p) & (p <= thr)      # NaN -> False
threshold = thr (0.0 if none qualify)
```

`naive_mask` flags `p < alpha`, with NaN mapped to `False`.

BH controls the expected fraction of *flagged* pixels that are false, rather than the
per-pixel false-positive rate.

## 6. Spatial coherence — `analysis/coherence.py`

**Signatures:** `connected_patch_filter(mask, min_size=5, connectivity=8) -> mask` and
`morans_i(values, mask) -> float`

Connected patches, queen connectivity:

```
structure = ones((3,3)) if connectivity == 8 else cross      # 8 = queen, 4 = rook
labels, _ = scipy.ndimage.label(mask, structure=structure)
counts    = bincount(labels.ravel())
keep      = labels where counts[labels] >= min_size, else False
```

The longitude seam is treated as a boundary rather than wrapped, so no false connections
appear where the map joins. That is a documented limitation.

Moran's I, a diagnostic:

```
w_ij = rook adjacency over pixels where mask is True, row-standardized; W = sum(w_ij)
I    = (n / W) * ( sum_ij w_ij (x_i - xbar)(x_j - xbar) ) / ( sum_i (x_i - xbar)^2 )
```

Undefined cases, such as no neighbours or zero variance, return `nan`.

## 7. Cry-wolf pipeline — `analysis/pipeline.py`

**Signature:** `cry_wolf(da, q=0.05, min_patch=5, min_valid=0.8, do_deseasonalize=True, do_steps=True) -> CryWolfResult`

```
series = deseasonalize(da) if do_deseasonalize else da
slope, p = trend_test(series)

# per-pixel valid fraction over the period
valid_fraction = valid_months / total_months
insufficient = valid_fraction < min_valid
p = NaN where insufficient                          # "insufficient record"
p = NaN where pixel variance == 0                   # constant series guard

nmask = naive_mask(p, alpha=0.05)
cmask, threshold = benjamini_hochberg(p, q)
cmask = connected_patch_filter(cmask, min_size=min_patch, q=..., connectivity=8)
morans_i = morans_i(slope, cmask)                   # spatial-clustering diagnostic

change = step_change_test(series) if do_steps else empty   # §8, marked not excluded
```

`CryWolfResult` carries `slope, p_value, naive_mask, corrected_mask,
insufficient_mask, step_mask, step_magnitude, threshold, n_naive, n_corrected,
n_insufficient, valid_fraction, morans_i`. The `insufficient_mask` records the pixels
the valid-fraction gate excluded, so "not enough data" stays distinguishable from
"tested, not significant", and the evidence card reports the count.

The step mask is a third, independent layer. Steps are marked, never removed: the two
verdicts are computed exactly as before, so a cell can be a step *and* a corrected flag
at the same time, and the map shows both.

## 8. Step-change detection — `analysis/steps.py`

**Signature:** `step_change_test(da, window=60, stride=6, threshold_sigma=15.0, persistence_min=0.5, persistence_max=2.0) -> StepChange(mask, magnitude)`

A record that jumps from one level to another — an upstream ingest defect, a gauge
relocation, a satellite product change — can pass every trend test for a reason that has
nothing to do with climate. This module flags those pixels so the map can say "this
record is broken" instead of quietly reporting the break as a trend. It is the one
diagnostic that is *additive*: it never changes a verdict.

Two adjacent windows, slid across the record, give the statistic:

```
D(k) = median(x[k : k+window]) - median(x[k-window : k])   # every stride-th k
D    = max_k |D(k)|                                        # the largest jump
split = argmax_k |D(k)|                                    # where it happened
```

`window = 60` months is deliberate. A five-year median is not moved by a single wet
month, so a flat desert series that spikes when it rains does not register; a genuine
level change moves every month's median after it. Both `D` and its sign are returned, so
`step_magnitude` carries the direction of the jump at the winning split.

The cut is field-relative, like the FDR step before it:

```
active    = pixels with D > 0                          # any step at all
reference = D over active pixels
s         = 1.4826 * MAD(reference)                    # robust scale
flagged   = D > median(reference) + 15 * s             # and the persistence band
```

Restricting the reference to active pixels matters on sparse fields. Snow water
equivalent is zero over most of the world, so the whole-field median and MAD are both
zero; a scale of zero would either flag nothing or flag everything. Reading the
distribution of the nonzero steps instead gives a scale that means something. A field
whose steps are all the same size has no scale to cut against and flags nothing.

Magnitude alone is not enough — a smooth ramp also produces large window-to-window
differences. So a second gate compares the jump with the shift that is actually left at
the end of the record:

```
endpoints = median(last window) - median(first window)
keep      = 0.5 * D <= |endpoints| <= 2.0 * D
```

A durable step of size `H` leaves `|endpoints| ~ H ~ D`, so its ratio sits near 1 and it
passes. A smooth ramp over 540 months leaves the whole ramp's worth, several window-steps
(ratios of 4–48 on the real field), and is rejected. A flood or drought excursion that
returns to its old level leaves almost nothing (ratios near 0) and is rejected too. The
band is what separates "the record moved and stayed moved" from "the record went up and
came back" and from "the record was always climbing".

Thresholds live in `config.py`: `STEP_WINDOW`, `STEP_STRIDE`, `STEP_THRESHOLD_SIGMA`,
`STEP_PERSISTENCE_MIN`, `STEP_PERSISTENCE_MAX`. The rule was signed off against the real
PSL field, where it marks 11 soil-moisture pixels and none in the other four variables.
The clearest is (80.95°N, −67.50°E): its record holds 190.19 kg m-2 for 1980–1998 and
about 729 after, a jump of 532.91 kg m-2 — 111 robust sigmas against the field's 4.76
scale — while its neighbours stay within 0.41. The persistence guard rejects six more
soil pixels that cleared the magnitude cut but whose end-of-record shift ran four to
forty-eight window-steps: smooth ramps, not level changes. That rejection is the reason
the guard exists.

## 9. API — `api/app.py`

- `GET /catalog` lists `{key,label,units,source,description}` from `catalog.VARIABLES`,
  filtered to the variables the active source serves, so the UI can never offer a
  variable the source cannot produce. `source` comes from
  `base.provenance(source, variable)`, which reports what actually produced the data
  rather than the catalogue's nominal product. A source that can say declares
  `provenance(variable) -> str`; the rest fall back to `Variable.source`. Without this,
  PSL air temperature would be served under the catalogue's `MERRA-2`/`GLDAS` label.
- `GET /demo/{variable}` returns the `CryWolfResult` as JSON: `lat`, `lon`, `naive_mask`
  (0/1), `corrected_mask` (0/1), `step_mask` (0/1, §8), `slope`, `p_value` (per pixel, for
  the dot hover readout), `step_magnitude` (signed jump, `null` where unmarked),
  `annual_anomaly` (one global annual-mean anomaly per whole year, for the stripe band),
  plus `units`, `source`, `threshold`, `n_naive`, `n_corrected`, `n_insufficient`,
  `morans_i` and `valid_fraction`. An unknown variable, or one the source cannot serve,
  raises `HTTPException(404)` with the available keys in `detail`. NaN slopes become
  `null`, since JSON has no NaN.
- `GET /fingerprint/{variable}?lat=&lon=&k=` returns the region under the click and its
  nearest twins across all served variables. The first call starts a background build
  and returns `status="preparing"` with `progress {done,total}`; later calls return
  `status="ready"` with `reference`, `matches` (centroid, standardised distance, raw
  vector, usable count, rings), `regions` (every patch centroid, for click-snapping) and
  `kept` (the variables that survived standardisation). It returns
  `status="unavailable"` with a `reason` when fewer than two variables can be analysed,
  or when the source cannot serve the variable. Fewer than two served variables means
  `unavailable` without starting a build.
- `GET /timeseries/{variable}?lat=&lon=` returns the nearest-cell `{time, value}` plus
  `time_kind` and `time_labels`. Non-finite coordinates are a 422; an unknown or unserved
  variable is a 404. `time_kind` is `"calendar"` when the source carries real months
  (POWER and PSL both emit `YYYYMM`) and `"index"` otherwise. `time_labels` renders those
  as ISO dates or plain month numbers, because a `YYYYMM` integer plotted as a number
  reads as `202.05k` on a chart axis.
- `GET /health` returns `{"status": "ok"}` and deliberately never touches the data
  source. A cold PSL load takes seconds, and a probe that waits on it would report a
  healthy app as down.
- `GET /` serves `web/index.html`; static assets live under `/static`, including
  `favicon.svg`, which `index.html` declares as `<link rel="icon">` so browsers do not
  probe `/favicon.ico` and log a 404.

The data layer is pluggable. The API reads through `data.base.DatasetSource`
(`load(variable) -> DataArray`), and three sources are registered by name: `psl`
(`PslSource`, global NOAA reanalysis, §11), `power` (`PowerRegionalSource`, live NASA
MERRA-2, §10) and `synthetic` (`SyntheticSource`, the bundled known-truth grid).
`config.DATA_SOURCE` selects one and ships as `"psl"`, the only source that can fill a
world map. A source that carries only a subset also implements `variables() ->
list[str]`; `base.available_variables(source)` falls back to the whole catalogue for
sources that do not, and the API uses it for both the catalogue listing and the 404
guard. The deferred `data.fetch.EarthdataSource` still raises `NotImplementedError` for
GES DISC.

## 10. Live NASA POWER source — `data/power.py`

**Signature:** `PowerRegionalSource(bbox=None, start=None, end=None, fetch=None).load(variable) -> DataArray`, with `parse_regional(payload) -> DataArray` and `tiles(bbox) -> list[box]` as pure helpers.

The endpoint `power.larc.nasa.gov/api/temporal/monthly/regional` serves MERRA-2 monthly
means on a 0.5° grid from 1981, with no API key and no Earthdata login. Probed
constraints, all encoded in the client:

- at most **10°** of latitude and longitude per request, otherwise HTTP 422;
- at most **one parameter** per request;
- values keyed `YYYYMM`, plus an annual `YYYY13` row to discard;
- missing cells are the sentinel `-999.0`.

```
boxes  = tiles(bbox)                      # equal spans, each <= 10 degrees
parts  = [parse_regional(fetch(url(param, b, start, end))) for b in boxes]
grid   = combine_by_coords(parts)         # or parts[0] when one tile

parse_regional:
  time  = sorted unique YYYYMM over all features, minus *13     # ints, ascending
  lat   = unique latitudes, descending                          # north-up
  lon   = unique longitudes, ascending
  value[t,i,j] = payload value, or NaN where -999.0 or unreported
```

`combine_by_coords` returns a `Dataset` when the tiles carry disjoint coordinates, so the
single data variable is unwrapped back to a `DataArray`.

**January alignment matters.** `decomp.deseasonalize` assigns calendar months by
*position* (`arange(n) % 12`), not by the `time` coordinate. POWER's monthly regional
response starts at January of `start`, which keeps position and calendar month in phase.
A series that began mid-year would have its climatology subtracted from the wrong months
and would manufacture a trend.

The `PARAMETERS` mapping lists only catalogue variables whose POWER units match the
catalogue metadata, so the API never mislabels a series:

| catalog key | POWER parameter | units |
| --- | --- | --- |
| `surface_temperature` | `T2M` | degC |
| `precipitation` | `PRECTOTCORR` | mm/day |

Any other key raises `KeyError` rather than silently substituting a different quantity,
and `PowerRegionalSource.variables()` reports exactly the mapped keys, so `/catalog`
offers only those and requesting an unmapped variable is a 404 instead of a 500. The
defaults (`config.POWER_BBOX/START/END`) are the Greenland box 60–70°N, 50–40°W,
1990–2024, which is 420 × 21 × 17.

**Measured** on a live normal connection: cold fetch 90 s for that box, analysis 2.3 s,
247 naive flags, 122 kept by the correction, Moran's I 0.87. Precipitation over the same
box is the empty case, naive 1 and corrected 0, so `morans_i` is undefined and
serialises as `null`.

## 11. Global NOAA PSL source — `data/psl.py`

**Signature:** `PslSource(start=None, end=None, stride=None, chunk=None, fetcher=None, opener=None).load(variable)`, with `ncss_url(path, name, start, end, stride)`, `cache_name(path, start, end, stride)`, `_spans(start, end, years)` and `extract_grid(dataset, name, start, end, stride)` as pure helpers.

`psl.noaa.gov/thredds` runs the NetCDF Subset Service (NCSS) over NCEP–NCAR reanalysis.
One GET returns just the requested variable and date range, which is what makes a world
map practical: NASA POWER caps a request at 10°×10°, PSL does not.

```
url  = NCSS_BASE + path + var, time_start, time_end, accept=netcdf3 [, horizStride]
file = cache.fetch(url, name=cache_name(...))   # download once, then local
ds   = opener(file)                              # scipy reads NetCDF3
da   = extract_grid(ds, name, start, end)        # stride already applied server-side

extract_grid:
  keep years in [start, end]                     # datetime64 -> year
  isel(lat=::stride, lon=::stride)               # space only, never time
  transpose("time","lat","lon")
  sortby lat descending, lon ascending           # north-up, like every source
  time = year*100 + month                        # YYYYMM ints, time_kind="calendar"
  convert raw file units -> catalog units         # CONVERSIONS[name], see below
```

Units are converted at the source boundary. Five catalogue variables share one source and
four of the files do not use the catalogue's units. Converting inside `extract_grid`,
rather than in the UI or the pipeline, means every other layer — the chart, the slope
map, the fingerprint vectors — can assume catalogue units. Cached files keep PSL's raw
numbers, so re-extracting never double-converts.

All five files live in `surface_gauss`, PSL's T62 Gaussian directory, so the variables
share one horizontal grid (94×192 raw, 47×96 at stride 2) and the fingerprint can compare
them at a single pixel. Temperature deliberately reads `surface_gauss/air.2m.mon.mean.nc`
rather than the `surface/` twin: that twin is a 2.5° grid *and* holds the 0.995 sigma
level, not the 2 m field the catalogue label promises.

| catalog variable | PSL variable | file units | conversion | catalog units | check |
| --- | --- | --- | --- | --- | --- |
| `surface_temperature` | `air` | K | − 273.15 | °C | 288.15 K → 15 °C |
| `snow_water_equivalent` | `weasd` | kg/m2 | identity | kg/m2 | 1 kg/m2 of water = 1 mm; catalog keeps kg/m2 |
| `precipitation` | `prate` | kg/m2/s | × 86,400 | mm/day | July ITCZ 6.8e-5 → 5.9 mm/day |
| `soil_moisture_rootzone` | `soilw` | fraction | × 1000 × 1.9 m | kg/m2 | Amazon 0.26 → 494 kg/m2 |
| `evapotranspiration` | `lhtfl` | W/m2 | ÷ 2.5e6 × 86,400 | mm/day | July tropics +133.6 → 4.6 mm/day, sign kept |

Precipitation carries no density factor. `kg/m2` is already a depth of water, since 1 kg
over 1 m2 is 1 mm, so `prate` converts only seconds to days. Soil moisture does carry one,
because its input is a dimensionless volume fraction rather than a per-area mass.

The latent-heat sign is deliberate. The NCEP file is positive upward over the warm ocean
in July (mean +133.6 W/m2), so it is evaporation rather than condensation, and flipping
it would turn a physical fact into a formatting habit.

**Why NCSS instead of the obvious paths.**

| transport | result |
| --- | --- |
| raw file (`downloads.psl.noaa.gov`, 29.6 MB) | NetCDF4/HDF5; needs `h5py`/`netCDF4`, which have **no CPython 3.15 wheel** (the source build fails); also throttled here: 32 KB in 84 s |
| OPeNDAP per-request subsetting (`pydap`) | works, but took >7.5 min for one global slice and pays it again on every restart |
| NCSS `accept=netcdf3` | one request, classic NetCDF3 (`CDF\x01`), readable by `scipy`, already a dependency |

**The cache is one-time.** `data/cache.py` writes to a per-writer part file
(`<name>.<pid>.<thread>.part`), checks the body against the response's `Content-Length`,
checks the NetCDF magic number, then opens it with the reader before `os.replace`-ing it
into place. A truncated transfer raises `IncompleteDownload`; an HTML error page of
matching size raises `NotNetCDF`; a body with the right magic bytes that the reader
cannot open raises `NotUsable`. Any failure leaves the cache untouched. One lock per
cache file serialises writers, so the dashboard's burst of requests for one variable
downloads each chunk once and the rest wait for it instead of racing on a shared
filename. A cache hit still passes the validator but costs no network at all, not even a
`HEAD`. Cache names include the year range and stride, because NCSS puts the whole
request in the query string and basenames would collide.

**Chunking.** THREDDS cut a single 45-year request off at about 7 min and returned a
6.2 MB body that scipy rejected; the complete slice is about 22 MB. The range is
therefore requested in `PSL_CHUNK_YEARS` slices (default 5) and concatenated on the time
axis. Each chunk is a separate cache entry, so a failure loses one chunk instead of the
whole download and a rerun resumes from the chunks already on disk. Each 60-month chunk
is 1.1 MB at stride 2: tens of seconds cold, then 0.0 s from cache.

A burst of chunk requests earns `429 Too Many Requests`, so `fetch` retries
`429/500/502/503/504` and connection errors with exponential backoff (20 s, 40 s, 80 s).
Any other client error fails immediately, since a 404 will still be a 404.

**Measured analysis cost**, cold, on the former 10512-pixel 2.5° temperature grid; the
T62 grid the source now serves has 4512 pixels at stride 2:

| span | months | stride | px | analysis |
| --- | --- | --- | --- | --- |
| 1990–2024 | 420 | 1 | 10512 | 77 s |
| 1948–2026 | 938 | 1 | 10512 | 517 s |
| 2005–2024 | 240 | 1 | 10512 | 17 s |
| 1980–2024 | 540 | 2 | 2664 | 43 s |

The shipped default is therefore stride 2 over 1980–2024: 47×96 pixels, 540 months, one
analysis per variable out of the warm cache, 0.9 s to load. The first run additionally
downloads nine 1.1 MB chunks. `PSL_STRIDE = 1` buys detail for a roughly linear cost in
pixels.

**Reader dependency:** none beyond the base install. `open_dataset` tries `netCDF4`,
`h5netcdf`, then `scipy`, so an HDF5-capable backend is used when present, and if none
can open the file the error names `pip install scipy`.

**Provenance:** NOAA, not NASA. `PslSource.provenance()` and the returned array both
carry `source="NOAA PSL / NCEP-NCAR reanalysis"`, so nothing downstream can present it as
a NASA product. `/catalog` included: it reports what the source declares rather than the
catalogue's nominal product.

## 12. Trend fingerprints — `analysis/fingerprint.py`

**Signatures:** `region_vectors(slopes, labels) -> RegionVectors` (a frozen dataclass with
`.matrix` regions×variables, `.variables`, `.usable`, `.region_ids`),
`standardize(matrix, variables) -> (z, kept)`, `nearest(matrix, key, k) -> [(row,
distance)]`, and `region_rings(labels, lat, lon) -> {region_id: [ring]}` where `ring` is a
closed list of `[lat, lon]` vertices.

A fingerprint is what a region's trends look like across every served variable at once.
Regions are the connected patches of the selected variable's corrected mask, using the
same 8-connectivity the coherence gate already found. Each region gets a vector whose
entries are that variable's mean Theil–Sen slope over the patch, in catalogue units.

- **A variable contributes only above half coverage.** If at most half a region's pixels
  have a finite slope for a variable, that entry is NaN. The rule keeps a variable only
  when more than half the region is covered, because one surviving pixel is a hole in the
  record rather than a trend.
- **Regions with fewer than two usable variables are never matched.** The matching
  distance needs a full vector, so a region missing half its variables cannot be
  "closest" to the region missing the same half. That would be a fact about the gaps, not
  the climate.
- **Standardisation is NaN-safe and drops dead columns.** Each variable is z-scored
  across regions; a constant or too-sparse column is dropped instead of dividing by
  zero, and the kept names are returned for the legend.
- **The search is exact.** Euclidean distance over standardised vectors, sorted, with the
  reference excluded. At a few dozen regions an HNSW index would add a dependency and an
  approximation for nothing, so the original design's HNSW stays deferred until the
  region count makes it worthwhile.
- **A variable on a different grid is skipped.** A field whose shape disagrees with the
  label field cannot define those regions, so its column stays NaN instead of raising.
  PSL used to make this real: `air` was on a 2.5° grid (37 by 72 cells at stride 2) while
  the other four were on T62 (47 by 96), so selecting surface temperature left one
  aligned field and the build reported `unavailable`. Temperature now reads the T62
  `air.2m` file, so all five align, and the guard stays as a safety net for a source that
  mixes grids.
- **Rings are walked, not approximated.** `region_rings` collects each region's boundary
  edges, the cell sides facing a non-region cell or the grid edge, with a consistent
  orientation, then chains them into loops so a two-cell region outlines as one
  rectangle. Unclosed chains are dropped instead of drawn wrong, and no new dependency is
  needed.

`api/app.py` builds the fingerprint on a daemon thread, since five cold variables take
minutes, keyed on the (selected variable, served set) pair. It reports
`{"status": "preparing"|"ready"|"unavailable", "progress": {done,total}, ...}`. The
`/fingerprint` payload carries the reference region, its nearest matches with centroids,
vectors and rings, every region centroid for click-snapping, and the kept variable names
for the "driving variables" legend.

## 13. Implementation notes and pitfalls

- **Ties:** with ties present, MK's no-tie variance is slightly off, which is acceptable
  for this slice and noted as a limitation. BH ordering must be **stable** so equal
  p-values give a stable threshold.
- **Performance:** pair-index vectors are built once per grid and reused, and the
  per-pixel loop is faster than a vectorised block here (measured 45×90 → 3.7 s cold).
  Results are cached per variable in the process and persisted to disk across restarts
  (§14).
- **Autocorrelation estimate:** the lag-1 `rho` is estimated on the linear-detrended
  residual, floored at 1.0 and clamped to `[0, 0.99]`.
- **Significance against magnitude:** p answers "real or luck", not size. The evidence
  card reports the slope next to p so users do not over-read significance.
- **Never invent data:** gaps are excluded and flagged, and a pixel below `min_valid` is
  reported as "insufficient record" rather than imputed. A permanently masked pixel must
  count **finite** months, not time slots: `_sen_slope_1d` guards on
  `count_nonzero(isfinite)` so an all-NaN pair vector returns NaN quietly instead of
  firing `np.nanmedian`'s "All-NaN slice encountered". Pairs stay on original indices, so
  a gap keeps its true time distance; dropping NaNs would compress the axis and bias the
  slope.
- **No HDF5 reader on Python 3.15:** `h5py` has no CPython 3.15 wheel and its source
  build fails, and `netCDF4` likewise. Anything HDF5-shaped, including PSL's raw `.nc`
  files, is unreadable here. NCSS sidesteps it by emitting NetCDF3, which `scipy` reads.
  `sample.save_bundled`/`load_bundled` pin `engine="scipy"` for the same reason, since
  engine autodetection picks HDF5 when a backend is merely half-installed (`h5netcdf`
  without `h5py`), which once broke a bundled round-trip that used to pass.
- **Cache correctness beats cache speed:** magic bytes do not detect truncation, and
  THREDDS can close a connection early without changing `Content-Length`, so the cache
  runs the consuming reader over the body before accepting it. One lock per cache file
  serialises writers, because the dashboard's burst of requests for one variable would
  otherwise all download the same chunk and race on one `.part` name (Windows
  `WinError 32`).
- **Raster placement on a Mercator map:** the slope grid is evenly spaced in latitude but
  Web Mercator is not, since screen y grows like `ln(tan(pi/4 + lat/2))`. Drawing
  equal-latitude rows put a 60°N feature near 76°N, so continents drifted poleward and
  their colour spilled into the sea. `web/projection.js` samples each output row at its
  Mercator latitude, and `fieldBounds` clamps to `±85.05112878`. The canvas is drawn at
  256+ rows so the equatorial rows do not go blocky.
- **One geographic box for the raster and the overlay:** the grid longitudes are cell
  centres, so the field reaches a half step past its outermost centre. Clamping the west
  edge to -180 while the east stopped at 178.125 made the overlay 358.125° wide, while
  `buildRaster` still mapped its pixels as if they filled 360°, so every painted cell sat
  west of its own meridian by up to 1.86° and drifted off the selection rectangle.
  `TDTrend.fieldBounds` now returns the box the overlay, the border, the fit and the
  pixel longitudes all read. When the centres wrap the globe it spans the full 360°, and
  a regional grid spans only its own degrees.
- **No smoothing on the slope raster:** bilinear upscaling of a few dozen rows blurred
  coastline colour into the ocean as a soft glow. The overlay sets `className:
  "slope-layer"` and `.slope-layer { image-rendering: pixelated }`. Satellite tiles keep
  normal filtering, since nearest-neighbour would make them look worse rather than
  better.

## 14. Persisted analysis cache — `data/analysis_cache.py`

The cry-wolf body is expensive. With `statsmodels` installed, STL deseasonalisation
dominates (§2) and costs minutes per variable, while the API's in-process `lru_cache`
lives only as long as one server process, so a restart would re-pay the whole cost. The
finished JSON body is therefore written under `data/cache/analysis/` and reused.

**Signatures:** `signature_of(source=None) -> str`, `load(variable, signature=None,
cache_dir=None) -> dict | None`, `save(variable, body, signature=None, cache_dir=None)
-> Path`.

- **Signature in the filename.** `signature_of()` hashes `ANALYSIS_SCHEMA`, the source,
  the span and stride, the FDR `q`, the valid-fraction and patch-size gates, whether
  `statsmodels` imports, and the NumPy version. The source is the one that *actually*
  served, which the app passes as the resolved source class through
  `_analysis_signature()`, not merely `config.DATA_SOURCE`, because a test or a second
  deployment can reuse the configured name while serving different data. Any change gives
  a new name, so a body computed under other parameters, libraries or data stays
  *invisible* rather than being served.
- **`ANALYSIS_SCHEMA` is the manual lever.** The signature sees configuration, not code
  or the dataset table, so the module bumps `ANALYSIS_SCHEMA` when a body's inputs change
  invisibly. It is `2`, which retired bodies computed before surface temperature moved to
  the T62 `air.2m` file.
- **File:** `<cache>/analysis/<variable>.<signature>.json`.
- **Atomic write.** The body goes to `<name>.<pid>.<thread>.part` and is renamed into
  place with `os.replace`, so a crash or a concurrent writer never leaves a half-written
  file at the real name. A failed write deletes its part file.
- **A corrupt file reads as absent.** An unreadable file, or JSON without a `variable`
  key, is treated as a miss and recomputed instead of raised.
- **Cache root.** `CACHE_DIR` defaults to the raw data cache root (`data/cache/`), so the
  two trees are gitignored together. Tests override just this module attribute to stay off
  the real cache without disturbing the raw chunks.
- **Measured.** A cold cry-wolf body, STL deseasonalisation plus trend testing, took
  **615 s** for `surface_temperature` on the shipped 47×96 grid on a four-CPU machine.
  The body is written at the end of that run, so an interrupted analysis saves nothing and
  is recomputed, while a disk hit returns the stored body in under a second and survives a
  `--reload`.

## 15. Web client — `web/`

Five vanilla-JS modules, no build step, no framework. `index.html` loads them with a
`?v=` cache key that tracks the build stamp on the `<html>` element.

| module | responsibility |
| --- | --- |
| `projection.js` | `mercatorRows(lat, height)` returns the raster row each latitude maps to, inverting Web Mercator so the slope grid lands where it belongs |
| `format.js` | number and unit formatting: thousands separators, one unit per value, no scientific notation |
| `trend-layer.js` | cell lookup, cell classification, the geographic field box, the RGBA raster builder, and the step marker |
| `chart-view.js` | Plotly traces, layout and config for the explorable cell chart |
| `app.js` | the orchestrator: catalogue, catalogue-driven variable chips, one Leaflet map, the mode switch, the readout, the fingerprint, the stripe band |

The map is a single Leaflet instance over NASA GIBS Blue Marble tiles. `buildRaster(demo,
mode, height, mercatorRows, bounds)` paints one RGBA image per mode: transparent where a
cell is unflagged, a diverging fill scaled by trend magnitude where it is flagged, and a
one-pixel patch outline (solid for survivors, dashed for pixels the correction drops).
`TDTrend.fieldBounds(lat, lon, maxLat)` owns the geographic rectangle that the overlay,
the border, `fitBounds` and the per-pixel longitudes all read, so a painted cell and the
outline that marks it cannot drift apart. `app.js` prebuilds the naive, corrected and
dropped rasters per variable, and the switch swaps image opacity rather than refetching.
A blurred, dimmed copy of the visible raster fills the stage wings.

`stepAt(demo, i, j)` reads the step mask, defaulting to clean when an older body has no
such field, and `stepCount(demo)` totals it. `buildRaster` paints a violet 2×2 marker at
the centre of every marked cell in all three modes — a broken record is not a verdict, so
it is not part of the flag count and does not disappear with the corrected view. The
legend key, the hover card and the readout line appear only when a variable has at least
one marked cell.

Clicking the map snaps to the nearest grid cell through `TDTrend.cellAt` and redraws the
selection from `TDTrend.cellBounds`. The readout shows the trend per decade, the p-value,
both verdicts, and — when the record holds one — the size of its step change. The chart
below it is a Plotly view configured to zoom on the wheel, pan on drag and reset on
double-click.
