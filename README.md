# Trend Detective

NASA Space Apps 2026 entry for **Be An Earth System Trend Detective!** (Advanced-only).

This repository currently implements the **cry-wolf slice**: it computes naive per-pixel
trend significance and field-significance-corrected significance on a gridded Earth variable,
and shows them side by side — the "detective who won't cry wolf."

- Design doc: `docs/superpowers/specs/2026-11-03-trend-detective-design.md`
- Implementation plan: `docs/superpowers/plans/2026-11-03-trend-detective-cry-wolf.md`
- Plain-English concepts: `docs/plain-english-concepts.md`

## Scope (this build)

Cry-wolf slice only. The live Earthdata fetcher and the trend-fingerprint search are deferred.

## Development

```bash
python -m pytest -q
```

Run the web app:

```bash
python -m uvicorn trend_detective.api.app:app --reload
```
