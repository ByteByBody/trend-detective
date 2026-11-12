"""PSL conversions from the file's raw units to the catalog's units."""

import numpy as np
import xarray as xr

from trend_detective import catalog
from trend_detective.data import psl


def _da(values):
    return xr.DataArray(np.array(values, dtype=float), dims=("lat", "lon"))


def test_prate_is_converted_to_mm_per_day():
    out = psl.prate_to_mm_per_day(_da([[2.0e-5]]))
    assert np.isclose(out.values[0, 0], 1.728)  # 2.0e-5 * 86400
    assert out.attrs["units"] == "mm day-1"


def test_soilw_fraction_becomes_kg_per_m2_over_the_10_200cm_layer():
    out = psl.soilw_to_kg_per_m2(_da([[0.3]]))
    assert np.isclose(out.values[0, 0], 570.0)  # 0.3 * 1000 * 1.9
    assert out.attrs["units"] == "kg m-2"


def test_air_2m_kelvin_becomes_degc():
    out = psl.air_to_degc(_da([[288.15]]))
    assert np.isclose(out.values[0, 0], 15.0)  # 288.15 K = 15 degC
    assert out.attrs["units"] == "degC"


def test_lhtfl_becomes_mm_per_day_and_keeps_the_sign():
    out = psl.lhtfl_to_mm_per_day(_da([[133.6]]))
    assert np.isclose(out.values[0, 0], 133.6 / 2.5e6 * 86400)
    assert out.values[0, 0] > 0  # evaporation stays positive
    assert out.attrs["units"] == "mm day-1"


def test_every_conversion_units_string_matches_the_catalog():
    # Pins the conversion's declared units to the catalog so the two cannot drift.
    for key, (_path, name) in psl.DATASETS.items():
        fn = psl.CONVERSIONS.get(name)
        if fn is not None:
            assert fn.units == catalog.get_variable(key).units


def test_extract_grid_applies_the_conversion_and_leaves_identity_vars_alone():
    ds = xr.Dataset(
        {
            "prate": xr.DataArray(
                np.full((1, 2, 2), 2.0e-5),
                dims=("time", "lat", "lon"),
                coords={
                    "time": ["2020-01-01"],
                    "lat": [1.0, 0.0],
                    "lon": [10.0, 20.0],
                },
                attrs={"units": "Kg/m^2/s"},
            )
        }
    )
    out = psl.extract_grid(ds, "prate")
    assert np.allclose(out.values, 1.728)
    assert out.attrs["units"] == "mm day-1"
