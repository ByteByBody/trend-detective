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


def test_standardize_drops_a_zero_variance_variable():
    matrix = np.array([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0]])  # col 1 constant
    z, kept = fp.standardize(matrix, ["a", "b"])
    assert kept == ["a"]
    assert np.allclose(z[:, 0].mean(), 0.0)


def test_nearest_excludes_the_reference_and_returns_k_rows():
    matrix = np.array([[0.0, 0.0], [0.1, 0.0], [5.0, 5.0]])
    hits = fp.nearest(matrix, key=0, k=1)
    assert hits[0][0] == 1