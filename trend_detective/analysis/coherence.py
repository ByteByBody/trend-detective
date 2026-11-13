"""Spatial coherence: keep only significant pixels in connected patches.

A real climate trend covers ground, not one random dot. See docs/algorithms.md
section 6.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

_QUEEN = np.ones((3, 3), dtype=int)
_ROOK = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=int)


def connected_patch_filter(
    mask: np.ndarray, min_size: int = 5, connectivity: int = 8
) -> np.ndarray:
    """Keep only pixels belonging to a connected component of size >= ``min_size``.

    ``connectivity`` 8 = queen (edge or corner), 4 = rook (edge only). The
    longitude seam is treated as a boundary (no wrap), so patches never merge
    across the map edge.
    """
    mask = np.asarray(mask, dtype=bool)
    if connectivity == 8:
        structure = _QUEEN
    elif connectivity == 4:
        structure = _ROOK
    else:
        raise ValueError(f"connectivity must be 4 or 8, got {connectivity!r}")

    labels, n_labels = ndimage.label(mask, structure=structure)
    if n_labels == 0:
        return np.zeros_like(mask)

    counts = np.bincount(labels.ravel())
    keep = counts >= min_size
    keep[0] = False  # background label
    return keep[labels]


def morans_i(values: np.ndarray, mask: np.ndarray) -> float:
    """Moran's I over masked cells with binary rook weights (diagnostic).

    Positive means neighbouring cells resemble each other (a real patch);
    negative means a checkerboard. Returns ``nan`` when undefined.
    """
    values = np.asarray(values, dtype=float)
    mask = np.asarray(mask, dtype=bool)
    idx = np.argwhere(mask)
    n = idx.shape[0]
    if n < 2:
        return math.nan

    x = values[mask]
    dev = x - x.mean()
    denom = float(np.sum(dev**2))
    if denom <= 0:
        return math.nan

    position = {tuple(p): k for k, p in enumerate(idx)}
    weight_sum = 0.0
    cross = 0.0
    for k, (r, c) in enumerate(idx):
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            j = position.get((r + dr, c + dc))
            if j is not None:
                cross += dev[k] * dev[j]
                weight_sum += 1.0
    if weight_sum == 0:
        return math.nan
    return float((n / weight_sum) * (cross / denom))
