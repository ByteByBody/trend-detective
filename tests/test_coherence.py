import numpy as np

from trend_detective.analysis import coherence


def test_invalid_connectivity_raises():
    m = np.zeros((3, 3), bool)
    m[1, 1] = True
    try:
        coherence.connected_patch_filter(m, connectivity=6)
    except ValueError:
        return
    raise AssertionError("expected ValueError for connectivity=6")


def test_scattered_pixels_removed_block_kept():
    m = np.zeros((10, 10), bool)
    m[2, 2] = True
    m[7, 8] = True
    m[4:7, 4:7] = True  # 3x3 block = 9 px
    out = coherence.connected_patch_filter(m, min_size=5, connectivity=8)
    assert out[4:7, 4:7].all()
    assert not out[2, 2] and not out[7, 8]


def test_diagonal_connectivity_is_queen():
    m = np.zeros((5, 5), bool)
    m[1, 1] = True
    m[2, 2] = True
    assert coherence.connected_patch_filter(m, min_size=2, connectivity=8).sum() == 2
    assert coherence.connected_patch_filter(m, min_size=2, connectivity=4).sum() == 0


def test_edge_strip_does_not_crash():
    m = np.zeros((4, 4), bool)
    m[0, :] = True
    assert coherence.connected_patch_filter(m, min_size=4, connectivity=8).sum() == 4


def test_morans_i_positive_for_a_ramp():
    v = np.mgrid[0:5, 0:5][0].astype(float)  # value = row index
    m = np.ones((5, 5), bool)
    assert coherence.morans_i(v, m) > 0.5


def test_morans_i_negative_for_checkerboard():
    v = (np.indices((5, 5)).sum(axis=0) % 2).astype(float)
    m = np.ones((5, 5), bool)
    assert coherence.morans_i(v, m) < 0
