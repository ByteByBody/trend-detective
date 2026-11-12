"""Disk persistence for the per-variable cry-wolf analysis body.

The analysis is expensive (STL deseasonalization is minutes per variable) and
the API's ``lru_cache`` only keeps it for the life of one process, so a restart
re-pays the whole cost. This stores the finished JSON body under
``data/cache/analysis/`` (gitignored) so a warm result survives a restart.

Correctness rules, mirroring ``cache.py``:

- the filename carries a **signature** of everything the result depends on, so
  a body computed under different parameters, a different library set, or a
  different data source is never served;
- writes are atomic (``<name>.<pid>.<thread>.part`` then ``os.replace``), so a
  crash or a concurrent writer cannot leave a half-written file in place;
- anything unreadable — a corrupt file, a body without a ``variable`` key — is
  treated as a miss and recomputed, never raised.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import numpy as np

from trend_detective import config
from trend_detective.data import cache

#: Bump when what a persisted body depends on changes, so old files are ignored:
#: the body's shape *or its inputs* — e.g. a variable starts reading a different
#: PSL file, which the source-class signature cannot see.
ANALYSIS_SCHEMA = 2

#: Root under which the analysis files live (``<root>/analysis``). It mirrors the
#: raw data cache root so both share one gitignored tree. It is a module
#: attribute rather than a lookup of ``cache.CACHE_DIR`` so tests can redirect
#: just the analysis cache without moving the raw chunks they may depend on.
CACHE_DIR: Path = cache.CACHE_DIR

#: One lock for analysis writes. Writes are rare and fast compared to the
#: minutes-long analysis, so a single lock is simpler than per-file locks.
_WRITE_LOCK = threading.Lock()


def _has_statsmodels() -> bool:
    """Whether ``statsmodels`` is importable, which flips ``auto`` to STL."""
    try:
        import statsmodels  # noqa: F401
    except ImportError:
        return False
    return True


def signature_of(source: str | None = None) -> str:
    """A short hash of everything an analysis body depends on.

    ``source`` names the source that actually produced the data; the app passes
    the resolved source class. It matters because the *configured* name can be
    reused while different data is served — a test swaps in a synthetic source, a
    deployment points ``DATA_SOURCE`` at a different product — and such a body
    must never be served for the live source. When omitted, the configured name
    is used.

    Any change here produces a different filename, so a stale body is invisible
    rather than wrong.
    """
    payload = {
        "schema": ANALYSIS_SCHEMA,
        "source": config.DATA_SOURCE if source is None else source,
        "start": config.PSL_START,
        "end": config.PSL_END,
        "stride": config.PSL_STRIDE,
        "q": config.FDR_Q,
        "min_patch": config.MIN_PATCH_SIZE,
        "min_valid": config.MIN_VALID_FRACTION,
        "statsmodels": _has_statsmodels(),
        "numpy": np.__version__,
    }
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def analysis_dir(cache_dir: Path) -> Path:
    """The analysis subdirectory of a cache directory."""
    return Path(cache_dir) / "analysis"


def path_for(variable: str, signature: str, cache_dir: Path) -> Path:
    """The file one ``(variable, signature)`` pair lives in."""
    return analysis_dir(cache_dir) / f"{variable}.{signature}.json"


def load(
    variable: str, signature: str | None = None, cache_dir: Path | None = None
) -> dict | None:
    """Return a cached body, or ``None`` when absent or unusable."""
    base = CACHE_DIR if cache_dir is None else cache_dir
    path = path_for(variable, signature or signature_of(), base)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            body = json.load(handle)
    except (OSError, ValueError):
        # Truncated, corrupt, or not JSON: treat as a miss so it is recomputed.
        return None
    if not isinstance(body, dict) or "variable" not in body:
        return None
    return body


def save(
    variable: str,
    body: dict,
    signature: str | None = None,
    cache_dir: Path | None = None,
) -> Path:
    """Write ``body`` atomically and return the path it was placed at."""
    base = CACHE_DIR if cache_dir is None else cache_dir
    directory = analysis_dir(base)
    directory.mkdir(parents=True, exist_ok=True)
    path = path_for(variable, signature or signature_of(), base)

    with _WRITE_LOCK:
        part = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}.part"
        )
        try:
            with open(part, "w", encoding="utf-8") as handle:
                json.dump(body, handle)
            os.replace(part, path)
        except BaseException:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    return path
