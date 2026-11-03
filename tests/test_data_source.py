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


def test_synthetic_source_serves_the_whole_catalog():
    from trend_detective import catalog

    assert set(base.get_source("synthetic").variables()) == set(catalog.VARIABLES)


def test_source_without_a_variables_method_is_assumed_to_serve_the_catalog():
    from trend_detective import catalog

    class Bare:
        def load(self, variable):  # pragma: no cover - protocol shim
            raise NotImplementedError

    assert set(base.available_variables(Bare())) == set(catalog.VARIABLES)


def test_earthdata_stub_is_not_implemented():
    with pytest.raises(NotImplementedError):
        fetch.EarthdataSource().load("soil_moisture_rootzone")


def test_earthdata_stub_satisfies_the_source_protocol():
    # A pluggable source only needs `load(variable) -> DataArray`; the stub
    # advertises that shape even though it is deferred.
    assert callable(fetch.EarthdataSource().load)
