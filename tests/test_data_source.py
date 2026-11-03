import numpy as np
import pytest

from trend_detective.data import base, fetch


def test_synthetic_source_returns_a_grid():
    src = base.get_source("synthetic")
    da = src.load("soil_moisture_rootzone")
    assert da.dims == ("time", "lat", "lon")
    assert da.attrs.get("variable") == "soil_moisture_rootzone"


def test_synthetic_source_is_deterministic_and_variable_specific():
    src = base.get_source("synthetic")
    a = src.load("surface_temperature")
    b = src.load("surface_temperature")
    c = src.load("precipitation")
    assert np.allclose(a.values, b.values, equal_nan=True)
    assert not np.allclose(a.values, c.values, equal_nan=True)


def test_unknown_source_raises_keyerror():
    with pytest.raises(KeyError):
        base.get_source("nope")


def test_earthdata_stub_is_not_implemented():
    with pytest.raises(NotImplementedError):
        fetch.EarthdataSource().load("soil_moisture_rootzone")


def test_earthdata_stub_satisfies_the_source_protocol():
    # A pluggable source only needs `load(variable) -> DataArray`; the stub
    # advertises that shape even though it is deferred.
    assert callable(fetch.EarthdataSource().load)
