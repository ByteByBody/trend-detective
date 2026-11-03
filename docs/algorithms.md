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
- **`auto`:** `stl` if `statsmodels` imports, else `climatology`.
- **NaN rule:** where the input is NaN, the output is NaN. Never fill.

---

## 3. Theil-Sen slope — `analysis/trend.py`

**Signature:** `theilsen_slope(da) -> xr.DataArray` (units per month)

For each pixel with the valid series `x_0..x_{n-1}`:

```
slope = median over all pairs i<j of (x_j - x_i) / (j - i)
```

**Implementation note (memory):** the number of pairs is `n(n-1)/2` (e.g. 28,680 for
`n=240`). Process pixels in **chunks** (e.g. 500 pixels) building a
`(chunk, npairs)` float array, then `np.nanmedian(axis=1)`. Precompute the pair index
difference matrix once. NaN pixels: `nanmedian` drops them.
Guard: `n < 2` → NaN.

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

**Guards:** `n < 2`, `var_S <= 0`, or zero-variance series → `p = NaN` (never "significant").
Chunk over pixels; drop NaN months per pixel before computing `n`, `S`, and `rho_k`.

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
p = NaN where valid_fraction < min_valid          # "insufficient record"
p = NaN where pixel variance ~ 0                  # constant series guard

nmask = naive_mask(p, alpha=0.05)
cmask = connected_patch_filter(benjamini_hochberg(p, q)[0], min_size=min_patch)
threshold = benjamini_hochberg(p, q)[1]
n_naive, n_corrected = int(nmask.sum()), int(cmask.sum())
```

**`CryWolfResult` fields:** `slope, p_value, naive_mask, corrected_mask, threshold,
n_naive, n_corrected, valid_fraction`.

---

## 8. API mapping — `api/app.py`

- `GET /catalog` → list of `{key,label,units,source}` from `catalog.VARIABLES`.
- `GET /demo/{variable}` → `CryWolfResult` as JSON: `lat`, `lon`, `naive_mask` (0/1),
  `corrected_mask` (0/1), `slope`, plus `threshold`, `n_naive`, `n_corrected`,
  `valid_fraction`. Unknown variable → `HTTPException(404)`.
- `GET /timeseries/{variable}?lat=&lon=` → nearest-cell `{time, value}`.
- `GET /` → `web/index.html`.

---

## 9. Implementation notes / pitfalls

- **Ties:** if ties exist, MK's no-tie variance is slightly off; acceptable for this slice,
  noted as a limitation. BH ordering must be **stable** so equal p-values give a stable threshold.
- **Performance:** chunk the pixel loop for Sen's slope (pair matrix) and for MK
  (autocorrelation). Target grid 45×90 = 4,050 pixels, monthly 240 → seconds, cached.
- **Autocorrelation estimate:** compute `rho_k` from the deseasonalized series; cap the lag
  sum at `k < n-2` (Hamed-Rao needs `n-k-2 >= 1`).
- **Significance vs magnitude:** p answers "real vs luck", not size. The evidence card must
  report the slope alongside p so users don't over-read significance.
- **Never invent data:** gaps excluded and flagged; a pixel below `min_valid` is reported as
  "insufficient record", never imputed.
