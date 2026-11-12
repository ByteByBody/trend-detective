# Trend Detective — Algorithms Reference

Precise math and pseudocode for each analysis module, to make the TDD implementation
mechanical. Every function here has (or will have) a failing test written first.

Conventions: `x` is a 1-D monthly series (time axis, float, may contain NaN). `X` is a
grid shape `(ny, nx)` of per-pixel results. `n` = number of valid samples. All functions
are NaN-aware and never impute.

---

## 1. Sample grid generator — `data/sample.py`

**Signature:** `generate_land_grid(seed=7, ny=45, nx=90, n_months=240, gap_fraction=0.0, autocorr=0.6) -> (xr.DataArray, GroundTruth)`

**Model.** For each pixel `(i, j)` and month `t`:

```
value[t,i,j] = seasonal[t] + slope[i,j] * t + noise[t,i,j]
```

- **Seasonal cycle** (same everywhere):
  `seasonal[t] = A * sin(2*pi * (t mod 12) / 12)`, with amplitude `A` (e.g. 3.0).
  Over a whole number of years its contribution to an OLS slope is ~0.
- **Injected trend:** `slope` is zero except on a small set of contiguous rectangular
  patches (`true_patch_mask`). Patch slopes drawn from e.g. `{+0.5, -0.3}` per month
  (`true_slope`, units per month). Non-patch pixels: `0.0`.
- **Noise (AR(1)) with stationary variance `sigma^2`:**
  ```
  noise[0] = w0                          w ~ N(0, sigma)
  noise[t] = autocorr * noise[t-1] + sqrt(1 - autocorr^2) * w_t
  ```
  `autocorr = 0` gives white noise. The `sqrt(1-phi^2)` scaling keeps variance ≈ `sigma^2`
  for every `phi`, so the slope-recovery test is comparable across settings.
- **Gaps:** independently set a random `gap_fraction` of cells to NaN (seeded).

**Determinism.** `rng = numpy.random.default_rng(seed)`, drawn in a fixed order.

**Coordinates:** `time` = monthly index (0..n_months-1); `lat` descending (e.g. 90..-90);
`lon` ascending (-180..180). Dims order `("time", "lat", "lon")`.

**GroundTruth:** `true_slope: (ny,nx) float`, `true_patch_mask: (ny,nx) bool`.

**Bundled IO:** `save_bundled(da, path)` / `load_bundled(path)` via xarray NetCDF;
`load_bundled` raises `FileNotFoundError` when absent.

---

## 2. Seasonal decomposition — `analysis/decomp.py`

**Signature:** `deseasonalize(da, method="auto") -> xr.DataArray` (same shape/coords)

- **`climatology`:** for each calendar month `m` in `0..11`,
  `clim[m] = nanmean over all years of value[months where t mod 12 == m]`,
  then subtract `clim[t mod 12]` from each timestep.
- **`stl`:** `statsmodels.tsa.seasonal.STL(series, period=12, robust=True)` per pixel;
  subtract the returned `seasonal` component.
- **`auto`:** `stl` when `statsmodels` imports, else `climatology`. `statsmodels` is
  an optional accelerator, not a declared dependency: without it `auto` is climatology,
  with it (as on this machine) `auto` is STL. STL's per-pixel robust fits dominate the
  whole pipeline — **minutes** for a world grid against seconds for climatology — which
  is why the API persists each variable's finished body to disk (§13).
- **NaN rule:** where the input is NaN, the output is NaN. Never fill.
- **STL fallback:** a pixel the STL cannot handle — any missing month, or a series
  shorter than two periods (< 24 months) — is deseasonalized with the **climatology**
  method for that pixel. It is never left with its seasonal cycle in place: such a
  pixel can still clear the pipeline's valid-fraction gate, and a 12-month swing would
  manufacture a trend.

---

## 3. Theil-Sen slope — `analysis/trend.py`

**Signature:** `theilsen_slope(da) -> xr.DataArray` (units per month)

For each pixel with the valid series `x_0..x_{n-1}`:

```
slope = median over all pairs i<j of (x_j - x_i) / (j - i)
```

**Implementation note (measured).** The number of pairs is `n(n-1)/2` (28,680 for
`n=240`). The pair index vectors `i, j` and `j-i` are built **once per grid** and
reused for every pixel; the no-missing fast path takes `np.median` of a contiguous
1-D pair vector, and pixels with gaps take the NaN-aware per-pixel median.

This per-pixel form is *faster* than a vectorised `(pixels x pairs)` block, which was
implemented and measured first: at most one pair vector exists at a time, so the
memory pressure that motivates chunking never arises, and the small contiguous vector
is cache-friendly. Measured cold on a 45x90 grid: **3.7 s** per-pixel vs 12.7 s
vectorised. Guard: `n < 2` → NaN.

---

## 4. Mann-Kendall + Hamed-Rao — `analysis/trend.py`

**Signature:** `mann_kendall_pvalue(da, autocorr_correct=True) -> xr.DataArray`
and `trend_test(da, autocorr_correct=True) -> (slope, p_value)`

**Mann-Kendall statistic.** With `sgn(x_j - x_i)` = +1/0/-1:

```
S = sum_{i<j} sgn(x_j - x_i)
```

**Variance (no ties):**

```
var_S = n * (n - 1) * (2n + 5) / 18
```

**Hamed-Rao variance inflation (autocorr_correct=True).** Estimate the lag-1
autocorrelation `rho_1` of the **linear-detrended residual** series (under an
AR(1) model):

```
r_t = x_t - (a + b t)                         # least-squares line removed
rho_1 = sum_{t=1}^{n-1} (r_t - rbar)(r_{t+1} - rbar) / sum_t (r_t - rbar)^2
rho_1 = clamp(rho_1, 0.0, 0.99)
var_S *= (1 + rho_1) / (1 - rho_1)          # floored at 1 (never less significance)
```

This equals the "240 months → ~42 real facts" result: for `rho_1 = 0.7`,
`(1-0.7)/(1+0.7) ≈ 0.176` effective-sample fraction.

**Why detrend first (implementation note).** A genuine linear trend is itself
highly autocorrelated — measured raw `rho_1 = 0.987` for an injected 0.5/month
trend — so estimating `rho` on the raw series penalises the very trend we are
trying to detect (inflation ~151x, hiding it). The residual autocorrelation
(measured `0.538` for AR(1) noise with `phi = 0.5`) is what the correction is
meant to model.

**Why lag-1, not all lags (implementation note).** Summing sample
autocorrelations across *all* lags is unstable: noisy high-lag estimates
frequently *shrink* the variance and manufacture false trends — measured, a
white-noise series' false-positive rate rose from 4% to 23%. The lag-1 form is
stable, and the factor is floored at 1.0 so the correction never claims more
significance than the independent test (conservative; negative autocorrelation
is not exploited — a documented limitation).

**Standardized score with continuity correction:**

```
Z = (S - 1) / sqrt(var_S)   if S > 0
Z = (S + 1) / sqrt(var_S)   if S < 0
Z = 0                        if S = 0
```

**Two-sided p-value:** `p = 2 * (1 - Phi(|Z|)) = erfc(|Z| / sqrt(2))`
where `Phi` is the standard normal CDF (`math.erfc` suffices; no SciPy needed).

**Guards:** `n < 3`, `var_S <= 0`, or zero-variance series → `p = NaN` (never "significant").
Drop NaN months per pixel before computing `n`, `S`, and `rho`. Same implementation
note as section 3: indices built once, per-pixel loop measured fastest.

---

## 5. Field significance (BH-FDR) — `analysis/significance.py`

**Signature:** `benjamini_hochberg(p, q=0.05) -> (mask, threshold)`
and `naive_mask(p, alpha=0.05) -> mask`

```
finite  = p[isfinite(p)]; m = len(finite)
order   = argsort(finite, kind="stable")
ranks   = 1..m
thr     = max{ finite[order[k-1]] : finite[order[k-1]] <= (k / m) * q }
mask    = isfinite(p) & (p <= thr)      # NaN -> False
threshold = thr (0.0 if none qualify)
```

`naive_mask`: `p < alpha`, NaN → `False`.

**Why BH (context):** controls the expected fraction of *flagged* pixels that are false,
instead of the per-pixel false-positive rate. See concepts doc §2.1.

---

## 6. Spatial coherence — `analysis/coherence.py`

**Signature:** `connected_patch_filter(mask, min_size=5, connectivity=8) -> mask`
and `morans_i(values, mask) -> float`

**Connected patches (queen):**

```
structure = ones((3,3)) if connectivity == 8 else cross      # 8 = queen, 4 = rook
labels, _ = scipy.ndimage.label(mask, structure=structure)
counts    = bincount(labels.ravel())
keep      = labels where counts[labels] >= min_size, else False
```

**Longitude seam:** do **not** wrap across the longitude edge (treated as a boundary), to
avoid false connections at the map seam. Documented limitation.

**Moran's I (diagnostic):**

```
w_ij = rook adjacency over pixels where mask is True, row-standardized; W = sum(w_ij)
I    = (n / W) * ( sum_ij w_ij (x_i - xbar)(x_j - xbar) ) / ( sum_i (x_i - xbar)^2 )
```

Undefined (e.g. no neighbours, zero variance) → `nan`.

---

## 7. Cry-wolf pipeline — `analysis/pipeline.py`

**Signature:** `cry_wolf(da, q=0.05, min_patch=5, min_valid=0.8, do_deseasonalize=True) -> CryWolfResult`

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
```

**`CryWolfResult` fields:** `slope, p_value, naive_mask, corrected_mask,
insufficient_mask, threshold, n_naive, n_corrected, n_insufficient, valid_fraction,
morans_i`.

`insufficient_mask` marks the pixels the valid-fraction gate excluded, so "not enough
data" is distinguishable from "tested, not significant"; the evidence card reports the
count.

---

## 8. API mapping — `api/app.py`

- `GET /catalog` → list of `{key,label,units,source,description}` from
  `catalog.VARIABLES`, **filtered to the variables the active source serves**, so
  the UI can never offer a variable the source cannot produce. `source` is
  `base.provenance(source, variable)`: what actually produced the data, not the
  catalog's nominal product. A source that can say declares
  `provenance(variable) -> str`; the rest fall back to `Variable.source`. Without
  this, PSL air temperature is served under the catalog's `MERRA-2`/`GLDAS` label.
- `GET /demo/{variable}` → `CryWolfResult` as JSON: `lat`, `lon`, `naive_mask` (0/1),
  `corrected_mask` (0/1), `slope`, `p_value` (per-pixel, for the dot hover readout),
  `annual_anomaly` (one global annual-mean anomaly per whole year, for the stripe
  band), plus `units`, `source` (same provenance),
  `threshold`, `n_naive`, `n_corrected`, `n_insufficient`, `morans_i`,
  `valid_fraction`. Unknown variable, or one the source cannot serve →
  `HTTPException(404)` with the available keys in `detail`. NaN slopes become
  `null` (JSON has no NaN).
- `GET /fingerprint/{variable}?lat=&lon=&k=` → the region under the click and its
  nearest twins across all served variables. The first call starts a background
  build and returns `status="preparing"` with `progress {done,total}`; later
  calls return `status="ready"` with `reference`, `matches` (centroid, standardized
  distance, raw vector, usable count, rings), `regions` (every patch centroid, for
  click-snapping), and `kept` (the variables that survived standardization).
  `status="unavailable"` when fewer than two variables can be analysed or the
  source cannot serve the variable; `reason` explains either. Fewer than two
  served variables → `unavailable` without starting a build.
- `GET /timeseries/{variable}?lat=&lon=` → nearest-cell `{time, value}` plus
  `time_kind` and `time_labels`; non-finite coordinates → 422; unknown or unserved
  variable → 404. `time_kind` is `"calendar"` when the source carries real months
  (POWER and PSL both emit `YYYYMM`) and `"index"` otherwise; `time_labels` renders
  those as ISO dates or plain month numbers, because a `YYYYMM` integer plotted as
  a number reads as `202.05k` on a chart axis.
- `GET /health` → `{"status": "ok"}`. Deliberately does **not** touch the data
  source: a cold PSL load takes seconds, and a probe that waits on it reports a
  healthy app as down.
- `GET /` → `web/index.html`; static assets under `/static`, including
  `favicon.svg`, which `index.html` declares as `<link rel="icon">` so browsers do
  not probe `/favicon.ico` and log a 404.

**Pluggable data layer:** the API reads through `data.base.DatasetSource`
(`load(variable) -> DataArray`). Sources registered by name: `psl`
(`PslSource`, global NOAA reanalysis — section 10), `power`
(`PowerRegionalSource`, live NASA MERRA-2 — section 9), and `synthetic`
(`SyntheticSource`, the bundled known-truth grid). `config.DATA_SOURCE` selects
one and ships as `"psl"`, the only source that can fill a world map. A source that carries only a subset also implements
`variables() -> list[str]`; `base.available_variables(source)` falls back to the
whole catalog for sources that do not, and the API uses it for both the catalog
listing and the 404 guard. The deferred `data.fetch.EarthdataSource` still raises
`NotImplementedError` for GES DISC.

---

## 9. Live NASA POWER source — `data/power.py`

**Signature:** `PowerRegionalSource(bbox=None, start=None, end=None, fetch=None).load(variable) -> DataArray`
with `parse_regional(payload) -> DataArray` and `tiles(bbox) -> list[box]` as pure helpers.

Endpoint `power.larc.nasa.gov/api/temporal/monthly/regional` serves MERRA-2 monthly
means on a 0.5° grid from 1981, **with no API key and no Earthdata login**. Probed
constraints (all encoded in the client):

- at most **10°** of latitude and longitude per request, else HTTP 422;
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

`combine_by_coords` returns a `Dataset` when tiles carry disjoint coordinates, so the
single data variable is unwrapped back to a `DataArray`.

**January alignment is mandatory.** `decomp.deseasonalize` assigns calendar months by
*position* (`arange(n) % 12`), not by the `time` coordinate. POWER's monthly regional
response starts at January of `start`, which keeps position and calendar month in
phase; a series that began mid-year would have its climatology subtracted from the
wrong months and would manufacture a trend.

**Variable mapping** (`PARAMETERS`) lists only catalog variables whose POWER units
match the catalog metadata, so the API never mislabels a series:

| catalog key | POWER parameter | units |
| --- | --- | --- |
| `surface_temperature` | `T2M` | degC |
| `precipitation` | `PRECTOTCORR` | mm/day |

Any other key raises `KeyError` rather than silently substituting a different
quantity, and `PowerRegionalSource.variables()` reports exactly the mapped keys so
`/catalog` offers only those (requesting an unmapped variable is a 404, never a
500). Defaults (`config.POWER_BBOX/START/END`) are the Greenland box
60–70°N, 50–40°W, 1990–2024 → 420 × 21 × 17.

**Measured (live, from a normal connection):** cold fetch 90 s for the box above,
analysis 2.3 s; naive flags 247 pixels, correction keeps 122, Moran's I 0.87.
Precipitation over the same box is the empty case: naive 1, corrected 0, so
`morans_i` is undefined and serialises as `null`.

---

## 10. Global NOAA PSL source — `data/psl.py`

**Signature:** `PslSource(start=None, end=None, stride=None, chunk=None, fetcher=None, opener=None).load(variable)`
with `ncss_url(path, name, start, end, stride)`, `cache_name(path, start, end, stride)`,
`_spans(start, end, years)` and `extract_grid(dataset, name, start, end, stride)` as
pure helpers.

`psl.noaa.gov/thredds` runs the NetCDF Subset Service (NCSS) over NCEP–NCAR
reanalysis. One GET returns just the requested variable and date range. That is
what makes a world map practical — NASA POWER caps a request at 10°×10°, PSL does
not.

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

**Units are converted at the source boundary.** Five catalog variables share one
source, and four of the files do not use the catalog's units. Converting in
`extract_grid` (rather than in the UI or the pipeline) means every other layer —
the chart, the slope map, the fingerprint vectors — can assume catalog units.
The cached file keeps PSL's raw numbers, so re-extracting never double-converts.

All five files live in `surface_gauss`, PSL's T62 Gaussian directory, so the
variables share one horizontal grid (94×192 raw, 47×96 at stride 2) and the
fingerprint can compare them at a single pixel. Temperature deliberately reads
`surface_gauss/air.2m.mon.mean.nc` rather than the `surface/` twin: that twin is
a 2.5° grid *and* holds the 0.995 sigma level, not the 2 m field the catalog
label promises.

| catalog variable | PSL variable | file units | conversion | catalog units | check |
| --- | --- | --- | --- | --- | --- |
| `surface_temperature` | `air` | K | − 273.15 | °C | 288.15 K → 15 °C |
| `snow_water_equivalent` | `weasd` | kg/m2 | identity | kg/m2 | 1 kg/m2 of water = 1 mm; catalog keeps kg/m2 |
| `precipitation` | `prate` | kg/m²/s | × 86,400 | mm/day | July ITCZ 6.8e-5 → 5.9 mm/day |
| `soil_moisture_rootzone` | `soilw` | fraction | × 1000 × 1.9 m | kg/m² | Amazon 0.26 → 494 kg/m² |
| `evapotranspiration` | `lhtfl` | W/m² | ÷ 2.5e6 × 86,400 | mm/day | July tropics +133.6 → 4.6 mm/day, sign kept |

The latent-heat sign is deliberate. The NCEP file is positive upward over the
warm ocean in July (mean +133.6 W/m²), so it is evaporation, not condensation,
and flipping it would turn a physical fact into a formatting habit.

**Why NCSS and not the obvious paths.**

| transport | result |
| --- | --- |
| raw file (`downloads.psl.noaa.gov`, 29.6 MB) | NetCDF4/HDF5; needs `h5py`/`netCDF4`, which have **no CPython 3.15 wheel** (source build fails); also throttled here: 32 KB in 84 s |
| OPeNDAP per-request subsetting (`pydap`) | works but took >7.5 min for one global slice — and pays it again on every restart |
| NCSS `accept=netcdf3` | one request, classic NetCDF3 (`CDF\x01`), readable by `scipy`, which is already a dependency |

**Cache = one-time.** `data/cache.py` writes to a per-writer part file
(`<name>.<pid>.<thread>.part`), checks the body
against the response's `Content-Length`, checks the NetCDF magic number, then
opens it with the reader before `os.replace`-ing it into place. A truncated
transfer raises `IncompleteDownload`; an HTML error page of matching size raises
`NotNetCDF`; a body with the right magic bytes that the reader cannot open raises
`NotUsable`. Any failure leaves the cache untouched. One lock per cache file
serialises writers, so the dashboard's burst of requests for one variable
downloads each chunk once and the rest wait for it instead of racing on a shared
filename. A cache hit still passes the
validator but costs **no network at all** — not even a `HEAD`. Cache names include
the year range and stride, because NCSS puts the whole request in the query string
and basenames would collide.

**Chunking.** THREDDS cut a single 45-year request off at ~7 min and returned a
6.2 MB body that scipy rejected (the complete slice is ~22 MB). So the range is
requested in `PSL_CHUNK_YEARS` slices (default 5) and concatenated on the time
axis. Each chunk is a separate cache entry, so a failure loses one chunk rather
than the whole download, and a rerun resumes from the chunks already on disk.
Each 60-month chunk is 1.1 MB at stride 2: tens of seconds cold, then 0.0 s from
cache.

A burst of chunk requests earns `429 Too Many Requests`, so `fetch` retries
`429/500/502/503/504` and connection errors with exponential backoff
(`20 s, 40 s, 80 s`). Any other client error fails immediately: a 404 will still
be a 404.

**Measured analysis cost** (cold, on the former 10512-pixel 2.5° temperature
grid; the T62 grid the source now serves has 4512 pixels at stride 2):

| span | months | stride | px | analysis |
| --- | --- | --- | --- | --- |
| 1990–2024 | 420 | 1 | 10512 | 77 s |
| 1948–2026 | 938 | 1 | 10512 | 517 s |
| 2005–2024 | 240 | 1 | 10512 | 17 s |
| 1980–2024 | 540 | 2 | 2664 | 43 s |

Hence the shipped default is stride 2 over 1980–2024: 47×96 pixels, 540 months,
one analysis per variable from the warm cache (0.9 s to load). The first run
additionally downloads nine 1.1 MB chunks. `PSL_STRIDE = 1` buys detail for a
roughly linear cost in pixels.

**Reader dependency:** none beyond the base install. `open_dataset` tries
`netCDF4`, `h5netcdf`, then `scipy`, so an HDF5-capable backend is used if
present; if none can open the file the error names `pip install scipy`.

**Provenance:** NOAA, not NASA. `PslSource.provenance()` and the returned array
both carry `source="NOAA PSL / NCEP-NCAR reanalysis"` so nothing downstream can
present it as a NASA product — including `/catalog`, which reports what the
source declares rather than the catalog's nominal product.

---

## 11. Trend fingerprints — `analysis/fingerprint.py`

**Signatures:** `region_vectors(slopes, labels) -> RegionVectors` (frozen
dataclass: `.matrix` regions×variables, `.variables`, `.usable`, `.region_ids`),
`standardize(matrix, variables) -> (z, kept)`, `nearest(matrix, key, k) ->
[(row, distance)]`, and `region_rings(labels, lat, lon) -> {region_id: [ring]}`
with `ring` a closed list of `[lat, lon]` vertices.

A fingerprint is what a region's trends look like **across every served
variable at once**. Regions are the connected patches of the selected variable's
corrected mask (8-connectivity, the same patches the coherence gate already
found); each region gets a vector whose entries are that variable's mean
Theil–Sen slope over the patch, in catalog units.

- **A variable contributes only above half coverage.** If at most half a
  region's pixels have a finite slope for a variable, that entry is NaN (the
  rule keeps a variable only when more than half the region is covered). One
  surviving pixel is not a trend; it is a hole in the record.
- **Regions with fewer than two usable variables are never matched.** The
  matching distance requires a full vector, so a region missing half its
  variables cannot be "closest" to the region missing the same half — that would
  be a fact about the gaps, not the climate.
- **Standardization is NaN-safe and drops dead columns.** Each variable is
  z-scored across regions; a constant (or too-sparse) column is dropped instead
  of dividing by zero, and the kept names are returned for the legend.
- **The search is exact.** Euclidean distance over standardized vectors, sorted,
  reference excluded. At a few dozen regions an HNSW index would only add a
  dependency and an approximation; the original design's HNSW is deferred until
  the region count makes it worth it.
- **A variable on a different grid is skipped.** A field whose shape disagrees
  with the label field cannot define those regions, so its column stays NaN
  rather than raising. PSL used to make this real — `air` was on a 2.5° grid
  (37 by 72 cells at stride 2) while the other four were on T62 (47 by 96), so
  selecting surface temperature left one aligned field and the build reported
  `unavailable`. Temperature now reads the T62 `air.2m` file, so all five align;
  the guard stays as a safety net for a source that mixes grids.
- **Rings are walked, not approximated.** `region_rings` collects each region's
  boundary edges (cell sides facing a non-region cell or the grid edge) with a
  consistent orientation and chains them into loops, so a two-cell region
  outlines as one rectangle. Unclosed chains are dropped rather than drawn
  wrong. No new dependency.

`api/app.py` builds the fingerprint on a daemon thread — five cold variables is
minutes — keyed on the (selected variable, served set) pair, and reports
`{"status": "preparing"|"ready"|"unavailable", "progress": {done,total}, ...}`.
The `/fingerprint` payload carries the reference region, its nearest matches with
centroids, vectors, and rings, every region centroid for click-snapping, and the
kept variable names for the "driving variables" legend.

---

## 12. Implementation notes / pitfalls

- **Ties:** if ties exist, MK's no-tie variance is slightly off; acceptable for this slice,
  noted as a limitation. BH ordering must be **stable** so equal p-values give a stable threshold.
- **Performance:** pair-index vectors are built once per grid and reused; the per-pixel
  loop is faster than a vectorised block here (measured: 45×90 → 3.7 s cold). Results are
  cached per variable in the process and persisted to disk across restarts (§13).
- **Autocorrelation estimate:** the lag-1 `rho` is estimated on the linear-detrended
  residual, floored at 1.0, clamped to `[0, 0.99]`.
- **Significance vs magnitude:** p answers "real vs luck", not size. The evidence card reports
  the slope alongside p so users don't over-read significance.
- **Never invent data:** gaps excluded and flagged; a pixel below `min_valid` is reported as
  "insufficient record", never imputed. A permanently masked pixel (zero finite months) must
  count **finite** months, not time slots: `_sen_slope_1d` guards on `count_nonzero(isfinite)`
  so an all-NaN pair vector returns NaN quietly instead of firing `np.nanmedian`'s
  "All-NaN slice encountered". Pairs stay on original indices, so a gap keeps its true time
  distance — dropping NaNs would compress the axis and bias the slope.
- **No HDF5 reader on Python 3.15:** `h5py` has no CPython 3.15 wheel and its source build
  fails; `netCDF4` likewise. Anything HDF5-shaped (including PSL's raw `.nc` files) is
  therefore unreadable here. NCSS sidesteps it by emitting NetCDF3, which `scipy` reads.
  `sample.save_bundled`/`load_bundled` pin `engine="scipy"` for the same reason — engine
  autodetection picks HDF5 when a backend is merely half-installed (`h5netcdf` without
  `h5py`), which broke a bundled round-trip that used to pass.
- **Cache correctness beats cache speed:** magic bytes do not detect truncation, and THREDDS
  can close a connection early without changing `Content-Length`. The cache therefore runs
  the consuming reader over the body before accepting it. One lock per cache file serialises
  writers: the dashboard's burst of requests for one variable would otherwise all download
  the same chunk and race on one `.part` name (Windows `WinError 32`).
- **Raster placement on a Mercator map:** the slope grid is evenly spaced in latitude, but
  Web Mercator is not — screen y grows like `ln(tan(pi/4 + lat/2))`. Drawing equal-latitude
  rows put a 60°N feature near 76°N, so continents drifted poleward and their colour spilled
  into the sea. `web/projection.js` samples each output row at its Mercator latitude, and
  `fieldBounds` clamps to `±85.05112878`. The canvas is drawn at 256+ rows so the equatorial
  rows do not go blocky.
- **One geographic box for the raster and the overlay:** the grid longitudes are cell centres,
  so the field reaches a half step past its outermost centre. Clamping the west edge to -180
  while the east stopped at 178.125 made the overlay 358.125° wide while `buildRaster` still
  mapped its pixels as if they filled 360°, so every painted cell sat west of its own meridian
  by up to 1.86° and drifted off the selection rectangle. `TDTrend.fieldBounds` now returns the
  box the overlay, the border, the fit and the pixel longitudes all read; when the centres wrap
  the globe it spans the full 360°, and a regional grid spans only its own degrees.
- **No smoothing on the slope raster:** bilinear upscaling of a few dozen rows blurred
  coastline colour into the ocean as a soft glow. The overlay sets
  `className: "slope-layer"` and `.slope-layer { image-rendering: pixelated }`. Satellite
  tiles keep normal filtering — nearest-neighbour would make them look worse, not better.

---

## 13. Persisted analysis cache — `data/analysis_cache.py`

The cry-wolf body is expensive — with `statsmodels` installed, STL deseasonalization
dominates (§2) and costs minutes per variable — and the API's in-process `lru_cache`
lives only as long as one server process, so a restart would re-pay the whole cost. The
finished JSON body is therefore written under `data/cache/analysis/` and reused.

**Signature:** `signature_of(source=None) -> str`, `load(variable, signature=None,
cache_dir=None) -> dict | None`, `save(variable, body, signature=None, cache_dir=None)
-> Path`.

- **Signature in the filename.** `signature_of()` hashes `ANALYSIS_SCHEMA`, the source,
  the span/stride, the FDR `q`, the valid-fraction and patch-size gates, whether
  `statsmodels` imports, and the NumPy version. The source is the one that *actually
  served* — the app passes the resolved source class via `_analysis_signature()` — not
  merely `config.DATA_SOURCE`, because a test or a second deployment can reuse the
  configured name while serving different data. Any change gives a new name, so a body
  computed under other parameters, libraries, or data is *invisible*, never served.
- **`ANALYSIS_SCHEMA` is the manual lever.** The signature sees configuration, not
  code or the dataset table, so the module bumps `ANALYSIS_SCHEMA` when a body's
  inputs change invisibly. It is `2`: the bump retired bodies computed before
  surface temperature moved to the T62 `air.2m` file.
- **File:** `<cache>/analysis/<variable>.<signature>.json`.
- **Atomic write.** The body goes to `<name>.<pid>.<thread>.part` and is renamed into
  place with `os.replace`, so a crash or a concurrent writer never leaves a half-written
  file at the real name; a failed write deletes its part file.
- **Corrupt reads as absent.** An unreadable file, or JSON without a `variable` key, is
  treated as a miss and recomputed — it is never raised.
- **Cache root.** `CACHE_DIR` defaults to the raw data cache root (`data/cache/`), so the
  two trees are gitignored together; tests override just this module attribute to keep
  off the real cache without disturbing the raw chunks.
- **Measured.** A cold cry-wolf body — STL deseasonalization plus trend testing —
  took **615 s** for `surface_temperature` on the shipped 47×96 grid (four-CPU
  machine). The body is written at the end of that run, so an interrupted analysis
  saves nothing and is recomputed; a disk hit returns the stored body in under a
  second and survives a `--reload`.
