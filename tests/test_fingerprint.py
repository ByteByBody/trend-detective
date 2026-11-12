import numpy as np

from trend_detective.analysis import fingerprint as fp


def test_region_vectors_average_each_variable_over_the_patch():
    labels = np.array([[1, 1, 0], [0, 0, 2]])
    slopes = {
        "a": np.array([[1.0, 3.0, 0.0], [0.0, 0.0, 8.0]]),
        "b": np.array([[10.0, 20.0, 0.0], [0.0, 0.0, 50.0]]),
    }
    rv = fp.region_vectors(slopes, labels)
    assert rv.variables == ["a", "b"]
    assert np.allclose(rv.matrix[0], [2.0, 15.0])  # region 1 mean of a and b
    assert rv.usable.tolist() == [2, 2]


def test_a_region_with_one_usable_variable_is_not_silently_matched():
    labels = np.array([[1, 1], [2, 2]])
    slopes = {
        "a": np.array([[1.0, 2.0], [3.0, 4.0]]),
        "b": np.array([[np.nan, np.nan], [5.0, 6.0]]),  # region 1 has none of b
    }
    rv = fp.region_vectors(slopes, labels)
    assert rv.usable.tolist() == [1, 2]


def test_a_variable_at_exactly_half_coverage_is_dropped():
    # Half a region is not a trend. The rule keeps a variable only when more
    # than half the region has a finite slope, so this boundary stays strict.
    labels = np.array([[1, 1], [1, 1]])
    slopes = {"a": np.array([[1.0, np.nan], [3.0, np.nan]])}  # 2 of 4 = half
    rv = fp.region_vectors(slopes, labels)
    assert np.isnan(rv.matrix[0, 0])
    assert rv.usable.tolist() == [0]


def test_a_field_on_a_different_grid_is_skipped_not_crashed():
    # PSL serves air on a 2.5-degree grid and the rest on the T62 grid, so a
    # variable's field can disagree in shape with the selected variable's patch
    # field. It cannot define these regions and is skipped, rather than raising
    # "boolean index did not match indexed array".
    labels = np.array([[1, 1], [0, 2]])
    slopes = {
        "aligned": np.array([[1.0, 3.0], [0.0, 7.0]]),
        "other_grid": np.zeros((3, 5)),
    }
    rv = fp.region_vectors(slopes, labels)  # must not raise
    by_name = {name: rv.matrix[0, i] for i, name in enumerate(rv.variables)}
    assert np.isfinite(by_name["aligned"])
    assert np.isnan(by_name["other_grid"])
    assert rv.usable.tolist() == [1, 1]


def test_standardize_drops_a_zero_variance_variable():
    matrix = np.array([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0]])  # col 1 constant
    z, kept = fp.standardize(matrix, ["a", "b"])
    assert kept == ["a"]
    assert np.allclose(z[:, 0].mean(), 0.0)


def test_nearest_excludes_the_reference_and_returns_k_rows():
    matrix = np.array([[0.0, 0.0], [0.1, 0.0], [5.0, 5.0]])
    hits = fp.nearest(matrix, key=0, k=1)
    assert hits[0][0] == 1


def test_a_single_cell_region_has_a_square_ring():
    labels = np.array([[0, 0, 0], [0, 1, 0], [0, 0, 0]])
    lat = np.array([2.0, 1.0, 0.0])
    lon = np.array([0.0, 1.0, 2.0])
    rings = fp.region_rings(labels, lat, lon)
    assert set(rings) == {1}
    ring = rings[1][0]
    assert ring[0] == ring[-1]  # closed
    assert len(ring) == 5  # four corners + close


def test_a_two_cell_region_is_one_ring_not_two():
    labels = np.array([[1, 1, 0], [0, 0, 0]])
    lat = np.array([1.0, 0.0])
    lon = np.array([0.0, 1.0, 2.0])
    rings = fp.region_rings(labels, lat, lon)
    assert len(rings[1]) == 1