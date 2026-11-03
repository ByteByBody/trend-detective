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

The bundled synthetic grid is the default because it carries known ground truth.
A **live NASA source needs no key**: `PowerRegionalSource`
(`trend_detective/data/power.py`) pulls MERRA-2 monthly means from the POWER
regional endpoint. Switch with one setting in `trend_detective/config.py`:

```python
DATA_SOURCE = "power"          # default is "synthetic"
POWER_BBOX = (60.0, 70.0, -50.0, -40.0)   # lat_min, lat_max, lon_min, lon_max
POWER_START, POWER_END = 1990, 2024
```

The endpoint caps a request at 10° per axis and one parameter, so the client tiles
large boxes and stitches the responses. Only variables whose POWER units match the
catalog are mapped (`surface_temperature` → `T2M`, `precipitation` → `PRECTOTCORR`);
anything else raises rather than substituting a different quantity. Details and
measured runs: `docs/algorithms.md` section 9.

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

Run the web app (serves the maps at `/`):

```bash
python -m uvicorn trend_detective.api.app:app --reload
```
