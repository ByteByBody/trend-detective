"""Field significance: Benjamini-Hochberg false-discovery-rate control.

Turns "5% chance of a false positive per pixel" into "at most 5% of the pixels
I flag are false". See docs/algorithms.md section 5 and concepts doc section 2.1.
"""

from __future__ import annotations

import numpy as np


def benjamini_hochberg(p: np.ndarray, q: float = 0.05) -> tuple[np.ndarray, float]:
    """Benjamini-Hochberg FDR.

    Returns ``(mask, threshold)``: ``mask`` is a boolean array the same shape as
    ``p`` (NaN entries are ``False``), and ``threshold`` is the p-value cutoff
    (``0.0`` when nothing qualifies).
    """
    p = np.asarray(p, dtype=float)
    finite_mask = np.isfinite(p)
    finite = p[finite_mask]
    m = finite.size
    mask = np.zeros(p.shape, dtype=bool)
    if m == 0:
        return mask, 0.0

    order = np.argsort(finite, kind="stable")
    sorted_p = finite[order]
    crit = (np.arange(1, m + 1) / m) * q
    ok = sorted_p <= crit
    if not ok.any():
        return mask, 0.0

    threshold = float(sorted_p[np.nonzero(ok)[0].max()])
    mask = finite_mask & (p <= threshold)
    return mask, threshold


def naive_mask(p: np.ndarray, alpha: float = 0.05) -> np.ndarray:
    """Per-pixel significance: ``p < alpha``; NaN entries are ``False``."""
    p = np.asarray(p, dtype=float)
    return np.isfinite(p) & (p < alpha)
