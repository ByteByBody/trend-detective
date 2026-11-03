"""Catalog of Earth-science variables the tool can study."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Variable:
    """Metadata for one studyable variable."""

    key: str
    label: str
    units: str
    source: str
    description: str


VARIABLES: dict[str, Variable] = {
    "soil_moisture_rootzone": Variable(
        key="soil_moisture_rootzone",
        label="Root-zone soil moisture",
        units="kg m-2",
        source="GLDAS",
        description=(
            "Water content of the 10-200 cm soil layer. Drops during drought, "
            "recovers with rain; a slow decline signals drying."
        ),
    ),
    "surface_temperature": Variable(
        key="surface_temperature",
        label="Surface air temperature",
        units="degC",
        source="MERRA-2",
        description="Near-surface air temperature, monthly mean.",
    ),
    "precipitation": Variable(
        key="precipitation",
        label="Precipitation",
        units="mm day-1",
        source="GPM / MERRA-2",
        description="Total precipitation rate, monthly mean.",
    ),
    "evapotranspiration": Variable(
        key="evapotranspiration",
        label="Evapotranspiration",
        units="mm day-1",
        source="GLDAS",
        description=(
            "Surface latent heat flux, expressed as mm/day of water. Rises "
            "with heating; unlike a land-only product it includes the ocean."
        ),
    ),
    "snow_water_equivalent": Variable(
        key="snow_water_equivalent",
        label="Snow water equivalent",
        units="kg m-2",
        source="GLDAS",
        description="Water stored in the snowpack; a shrinking spring value signals earlier melt.",
    ),
}


def get_variable(key: str) -> Variable:
    """Return the variable with ``key``; raise ``KeyError`` if unknown."""
    return VARIABLES[key]
