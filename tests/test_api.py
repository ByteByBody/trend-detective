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


class _PrecipOnlySource:
    """A source that serves a single variable, like POWER does."""

    def variables(self) -> list[str]:
        return ["precipitation"]

    def load(self, variable: str) -> xr.DataArray:
        return _FlatSource().load(variable)


@pytest.fixture
def precip_only_source(monkeypatch):
    monkeypatch.setattr(app_module, "_SOURCE", _PrecipOnlySource())
    app_module._demo_grid.cache_clear()
    app_module._demo_body.cache_clear()
    yield
    app_module._demo_grid.cache_clear()
    app_module._demo_body.cache_clear()


class _CalendarSource:
    """A source on a real calendar axis, like the live POWER source."""

    def variables(self) -> list[str]:
        return ["surface_temperature"]

    def load(self, variable: str) -> xr.DataArray:
        months = np.arange(24)
        stamps = 202001 + (months // 12) * 100 + (months % 12)
        return xr.DataArray(
            np.zeros((24, 3, 3), dtype=float),
            dims=("time", "lat", "lon"),
            coords={
                "time": stamps,
                "lat": np.linspace(1.0, -1.0, 3),
                "lon": np.linspace(-1.0, 1.0, 3),
            },
            name="value",
            attrs={"variable": variable, "time_kind": "calendar"},
        )


@pytest.fixture
def calendar_source(monkeypatch):
    monkeypatch.setattr(app_module, "_SOURCE", _CalendarSource())
    app_module._demo_grid.cache_clear()
    app_module._demo_body.cache_clear()
    yield
    app_module._demo_grid.cache_clear()
    app_module._demo_body.cache_clear()


def test_catalog_endpoint():
    r = client.get("/catalog")
    assert r.status_code == 200 and isinstance(r.json(), list) and r.json()


def test_catalog_lists_only_variables_the_source_serves(precip_only_source):
    keys = [v["key"] for v in client.get("/catalog").json()]
    assert keys == ["precipitation"]


def test_demo_for_an_unserved_variable_is_404(precip_only_source):
    r = client.get("/demo/surface_temperature")

    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "surface_temperature" in detail and "precipitation" in detail


def test_timeseries_for_an_unserved_variable_is_404(precip_only_source):
    assert client.get("/timeseries/surface_temperature?lat=0&lon=0").status_code == 404


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


def test_demo_exposes_a_per_pixel_p_value_grid_for_the_hover_readout():
    body = client.get("/demo/soil_moisture_rootzone").json()

    assert len(body["p_value"]) == len(body["lat"])
    assert len(body["p_value"][0]) == len(body["lon"])
    # The dots exist to be inspected, so at least one contested cell must carry
    # a readable number rather than null everywhere.
    contested = [
        (i, j)
        for i, row in enumerate(body["naive_mask"])
        for j, flagged in enumerate(row)
        if flagged
    ]
    assert contested
    assert any(body["p_value"][i][j] is not None for i, j in contested)


def test_demo_carries_the_annual_anomaly_for_the_stripe_band():
    body = client.get("/demo/soil_moisture_rootzone").json()
    anomaly = body["annual_anomaly"]

    assert anomaly  # a record long enough to have years
    finite = [v for v in anomaly if v is not None]
    assert finite
    assert abs(sum(finite) / len(finite)) < 1e-6  # anomalies centre on zero


def test_catalog_reports_the_active_source_not_the_nominal_product(monkeypatch):
    # The catalog names the product we would ideally use (GLDAS etc.). Serving
    # a different source under that label is false attribution.
    class GreenSource:
        def variables(self):
            return ["surface_temperature"]

        def provenance(self, variable):
            return "GREEN-1 sample"

        def load(self, variable):
            return _FlatSource().load(variable)

    monkeypatch.setattr(app_module, "_SOURCE", GreenSource())
    app_module._demo_grid.cache_clear()
    app_module._demo_body.cache_clear()

    try:
        entry = client.get("/catalog").json()[0]
    finally:
        app_module._demo_grid.cache_clear()
        app_module._demo_body.cache_clear()

    assert entry["source"] == "GREEN-1 sample"


def test_catalog_falls_back_to_the_nominal_product_when_undeclared(monkeypatch):
    # A source that says nothing about provenance keeps the catalog's nominal
    # product rather than reporting an empty string.
    class Bare:
        def variables(self):
            return ["precipitation"]

        def load(self, variable):
            return _FlatSource().load(variable)

    monkeypatch.setattr(app_module, "_SOURCE", Bare())
    app_module._demo_grid.cache_clear()
    app_module._demo_body.cache_clear()

    try:
        entry = client.get("/catalog").json()[0]
    finally:
        app_module._demo_grid.cache_clear()
        app_module._demo_body.cache_clear()

    assert entry["source"] == "GPM / MERRA-2"


def test_demo_reports_the_source_that_produced_the_numbers():
    body = client.get("/demo/surface_temperature").json()

    assert "synthetic" in body["source"].lower()


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


def test_timeseries_labels_a_calendar_axis_with_dates(calendar_source):
    # YYYYMM integers plotted as numbers render as "202.05k" on a chart axis.
    body = client.get("/timeseries/surface_temperature?lat=0&lon=0").json()

    assert body["time_kind"] == "calendar"
    assert body["time_labels"][:3] == ["2020-01-01", "2020-02-01", "2020-03-01"]
    assert body["time_labels"][-1] == "2021-12-01"


def test_timeseries_labels_an_index_axis_as_month_numbers():
    body = client.get("/timeseries/soil_moisture_rootzone?lat=0&lon=0").json()

    assert body["time_kind"] == "index"
    assert body["time_labels"][:3] == ["0", "1", "2"]
    assert len(body["time_labels"]) == len(body["time"])


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
