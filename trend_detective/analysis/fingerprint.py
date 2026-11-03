"""Trend fingerprints: what each significant region's trend looks like across
all served variables, and which other regions look the same.

Pure array code: the module knows region indices and label fields, never
coordinates or variable names beyond the ones handed to it. That keeps the
matching testable without a map and reusable by the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np


@dataclass(frozen=True)
class RegionVectors:
    """One trend vector per region, in catalog units.

    ``matrix`` is regions x variables, NaN wherever a variable cannot be trusted
    for that region. ``usable`` counts the finite entries per region so callers
    can refuse to match a region on a single variable.
    """

    matrix: np.ndarray
    variables: list[str]
    usable: np.ndarray
    region_ids: list[int]


def region_vectors(
    slopes: Mapping[str, np.ndarray], labels: np.ndarray
) -> RegionVectors:
    """Average each variable's slope over every labelled region.

    A variable contributes to a region only when more than half of that
    region's pixels have a finite slope. One surviving pixel is not a trend
    vector; it is a hole in the record wearing a trend's clothes.
    """
    labels = np.asarray(labels)
    variables = list(slopes)
    region_ids = sorted(int(v) for v in np.unique(labels) if v != 0)

    matrix = np.full((len(region_ids), len(variables)), np.nan, dtype=float)
    usable = np.zeros(len(region_ids), dtype=int)

    for row, region_id in enumerate(region_ids):
        mask = labels == region_id
        cell_count = int(mask.sum())
        if not cell_count:
            continue
        for col, name in enumerate(variables):
            field = np.asarray(slopes[name], dtype=float)
            values = field[mask]
            finite = np.isfinite(values)
            if finite.sum() * 2 > cell_count:
                matrix[row, col] = float(values[finite].mean())
                usable[row] += 1

    return RegionVectors(
        matrix=matrix,
        variables=variables,
        usable=usable,
        region_ids=region_ids,
    )


def standardize(
    matrix: np.ndarray, variables: list[str]
) -> tuple[np.ndarray, list[str]]:
    """Z-score each column across regions, dropping useless columns.

    A variable that is constant across regions (or has too few finite rows)
    carries no matching signal; keeping it would divide by zero and would also
    make every region look identical on that axis. NaN-safe throughout.
    """
    values = np.asarray(matrix, dtype=float)
    columns: list[np.ndarray] = []
    kept: list[str] = []

    for col, name in enumerate(variables):
        column = values[:, col]
        finite = np.isfinite(column)
        if finite.sum() < 2:
            continue
        spread = column[finite].std()
        if not np.isfinite(spread) or spread == 0:
            continue
        z = np.full(column.shape, np.nan, dtype=float)
        z[finite] = (column[finite] - column[finite].mean()) / spread
        columns.append(z)
        kept.append(name)

    if not columns:
        return np.empty((values.shape[0], 0), dtype=float), kept
    return np.column_stack(columns), kept


def nearest(matrix: np.ndarray, key: int, k: int) -> list[tuple[int, float]]:
    """The ``k`` rows nearest ``key`` by Euclidean distance, key excluded.

    Only rows with a full finite vector can match: a region missing half its
    variables would otherwise be closest to whichever region is missing the
    same half, which is a fact about the gaps, not the climate.
    """
    values = np.asarray(matrix, dtype=float)
    if values.ndim != 2 or not (0 <= key < values.shape[0]):
        return []

    complete = np.isfinite(values).all(axis=1)
    if not complete[key]:
        return []

    indices = np.where(complete)[0]
    diff = values[indices] - values[key]
    distance = np.sqrt((diff**2).sum(axis=1))
    pairs = [
        (int(i), float(d)) for i, d in zip(indices, distance) if int(i) != key
    ]
    pairs.sort(key=lambda pair: pair[1])
    return pairs[: max(0, k)]


def _edge_coordinates(axis: np.ndarray) -> np.ndarray:
    """Cell-edge coordinates along one axis, length ``len(axis) + 1``.

    Edges sit halfway between adjacent cell centres, with the outer edges
    extended by the same half-step, so latitude may run either direction.
    """
    axis = np.asarray(axis, dtype=float)
    if axis.size < 2:
        return np.array([axis[0] - 0.5, axis[0] + 0.5]) if axis.size else np.array([])
    middle = (axis[:-1] + axis[1:]) / 2.0
    return np.concatenate(
        ([axis[0] - (axis[1] - axis[0]) / 2.0], middle, [axis[-1] + (axis[-1] - axis[-2]) / 2.0])
    )


def region_rings(
    labels: np.ndarray, lat: np.ndarray, lon: np.ndarray
) -> dict[int, list[list[list[float]]]]:
    """Closed outline rings for every labelled region, as ``[lat, lon]`` lists.

    Boundary edges are walked rather than approximated, so a region that is
    two cells wide outlines as one rectangle, not as two squares. Edges are
    directed (counter-clockwise in index space) so chaining never has to guess;
    any chain that fails to close is dropped rather than drawn wrong.
    """
    labels = np.asarray(labels)
    ny, nx = labels.shape
    lat_edges = _edge_coordinates(lat)
    lon_edges = _edge_coordinates(lon)
    region_ids = sorted(int(v) for v in np.unique(labels) if v != 0)

    def is_region(i: int, j: int, region_id: int) -> bool:
        return 0 <= i < ny and 0 <= j < nx and int(labels[i, j]) == region_id

    rings: dict[int, list[list[list[float]]]] = {}
    for region_id in region_ids:
        edges: list[tuple[tuple[int, int], tuple[int, int]]] = []
        for i in range(ny):
            for j in range(nx):
                if int(labels[i, j]) != region_id:
                    continue
                # Corners are indexed (i, j) at the north-west of the cell.
                if not is_region(i - 1, j, region_id):
                    edges.append(((i, j), (i, j + 1)))
                if not is_region(i, j + 1, region_id):
                    edges.append(((i, j + 1), (i + 1, j + 1)))
                if not is_region(i + 1, j, region_id):
                    edges.append(((i + 1, j + 1), (i + 1, j)))
                if not is_region(i, j - 1, region_id):
                    edges.append(((i + 1, j), (i, j)))

        by_start: dict[tuple[int, int], list] = {}
        for edge in edges:
            by_start.setdefault(edge[0], []).append(edge)

        used: set = set()
        loops: list[list[list[float]]] = []
        for edge in edges:
            if edge in used:
                continue
            used.add(edge)
            loop = [edge[0]]
            current = edge
            while True:
                candidates = [e for e in by_start.get(current[1], []) if e not in used]
                if not candidates:
                    break
                nxt = candidates[0]
                used.add(nxt)
                loop.append(nxt[0])
                current = nxt
                if current[1] == loop[0]:
                    loop.append(current[1])
                    break
            if len(loop) >= 4 and loop[0] == loop[-1]:
                loops.append(
                    [
                        [float(lat_edges[i]), float(lon_edges[j])]
                        for i, j in loop
                    ]
                )
        rings[region_id] = loops

    return rings