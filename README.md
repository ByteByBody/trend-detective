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

## Scope (this build)

Cry-wolf slice only. The live Earthdata fetcher (`trend_detective/data/fetch.py`)
and the trend-fingerprint search are deferred behind a pluggable data-source
interface (`trend_detective/data/base.py`). Seasonality removal defaults to the
per-calendar-month climatology method; STL is used automatically when
`statsmodels` is installed.

## Development

```bash
python -m pytest -q
```

Run the web app (serves the maps at `/`):

```bash
python -m uvicorn trend_detective.api.app:app --reload
```
