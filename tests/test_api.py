import numpy as np
import pytest
import xarray as xr
from fastapi.testclient import TestClient

from trend_detective.api import app as app_module
from trend_detective.api.app import app

client = TestClient(app)


class _FlatSource:
    """A source whose series never varies: no trend, no significant pixels."""

    def load(self, variable: str) -> xr.DataArray:
        values = np.zeros((24, 3, 3), dtype=float)
        return xr.DataArray(
            values,
            dims=("time", "lat", "lon"),
            coords={
                "time": np.arange(24),
                "lat": np.linspace(1.0, -1.0, 3),
                "lon": np.linspace(-1.0, 1.0, 3),
            },
            name="value",
            attrs={"variable": variable},
        )


@pytest.fixture
def flat_source(monkeypatch):
    monkeypatch.setattr(app_module, "_SOURCE", _FlatSource())
    app_module._demo_grid.cache_clear()
    app_module._demo_body.cache_clear()
    yield
    app_module._demo_grid.cache_clear()
    app_module._demo_body.cache_clear()


def test_catalog_endpoint():
    r = client.get("/catalog")
    assert r.status_code == 200 and isinstance(r.json(), list) and r.json()


def test_demo_endpoint_shape():
    r = client.get("/demo/soil_moisture_rootzone")
    assert r.status_code == 200
    body = r.json()
    assert set(
        [
            "threshold",
            "n_naive",
            "n_corrected",
            "n_insufficient",
            "morans_i",
            "naive_mask",
            "corrected_mask",
        ]
    ).issubset(body)
    assert len(body["naive_mask"]) == len(body["lat"])
    assert len(body["naive_mask"][0]) == len(body["lon"])


def test_demo_unknown_variable_404():
    assert client.get("/demo/nope").status_code == 404


def test_timeseries_rejects_non_finite_coordinates():
    assert client.get("/timeseries/soil_moisture_rootzone?lat=nan&lon=0").status_code == 422


def test_timeseries_endpoint():
    meta = client.get("/catalog").json()[0]
    r = client.get(f"/timeseries/{meta['key']}?lat=0&lon=0")
    assert r.status_code == 200
    body = r.json()
    assert len(body["time"]) == len(body["value"])
    assert len(body["time"]) > 0


def test_timeseries_unknown_variable_404():
    assert client.get("/timeseries/nope?lat=0&lon=0").status_code == 404


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_demo_serialises_undefined_diagnostics_as_null(flat_source):
    # A series with no trend leaves the corrected mask empty, so Moran's I is
    # undefined. It must serialise as null, not NaN: real POWER data produces
    # this shape (precipitation over the demo box), and a non-finite literal
    # would be unparseable for strict JSON clients.
    r = client.get("/demo/surface_temperature")

    assert r.status_code == 200
    body = r.json()
    assert body["morans_i"] is None
    assert body["threshold"] == 0.0
    assert body["n_naive"] == 0
    assert body["n_corrected"] == 0
