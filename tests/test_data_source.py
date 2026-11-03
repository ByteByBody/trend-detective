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


def test_provenance_prefers_what_the_source_declares():
    class Declared:
        def load(self, variable):  # pragma: no cover - protocol shim
            raise NotImplementedError

        def provenance(self, variable):
            return "ACME reanalysis v3"

    assert base.provenance(Declared(), "precipitation") == "ACME reanalysis v3"


def test_provenance_falls_back_to_the_catalog_when_undeclared():
    from trend_detective import catalog

    class Bare:
        def load(self, variable):  # pragma: no cover - protocol shim
            raise NotImplementedError

    assert base.provenance(Bare(), "precipitation") == catalog.VARIABLES["precipitation"].source


def test_synthetic_source_does_not_claim_a_satellite_product():
    # The bundled grid is fabricated; labelling it GLDAS would be a lie even
    # though that is the catalog's nominal product for the variable.
    from trend_detective import catalog

    src = base.get_source("synthetic")
    label = base.provenance(src, "soil_moisture_rootzone")

    assert "synthetic" in label.lower()
    assert label != catalog.VARIABLES["soil_moisture_rootzone"].source


def test_power_source_declares_merra2():
    from trend_detective.data import power

    src = power.PowerRegionalSource()

    assert "MERRA-2" in base.provenance(src, "surface_temperature")
    assert "POWER" in base.provenance(src, "precipitation")


def test_psl_source_declares_noaa_not_nasa():
    from trend_detective.data import psl

    label = base.provenance(psl.PslSource(), "surface_temperature")

    assert label == psl.SOURCE_NAME
    assert "NOAA" in label


def test_earthdata_stub_is_not_implemented():
    with pytest.raises(NotImplementedError):
        fetch.EarthdataSource().load("soil_moisture_rootzone")


def test_earthdata_stub_satisfies_the_source_protocol():
    # A pluggable source only needs `load(variable) -> DataArray`; the stub
    # advertises that shape even though it is deferred.
    assert callable(fetch.EarthdataSource().load)
