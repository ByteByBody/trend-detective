import pytest

from trend_detective import catalog, config


def test_default_variable_exists():
    assert config.DEFAULT_VARIABLE in catalog.VARIABLES


def test_variable_fields_populated():
    v = catalog.get_variable("soil_moisture_rootzone")
    assert v.units and v.label and v.source


def test_unknown_variable_raises():
    with pytest.raises(KeyError):
        catalog.get_variable("nope")


def test_soil_moisture_description_names_the_10_200cm_layer():
    text = catalog.get_variable("soil_moisture_rootzone").description
    assert "10" in text and "200" in text and "top ~1 m" not in text


def test_evapotranspiration_description_says_latent_heat():
    assert "latent heat" in catalog.get_variable("evapotranspiration").description.lower()
