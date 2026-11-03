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
