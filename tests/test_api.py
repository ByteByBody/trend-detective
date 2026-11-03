from fastapi.testclient import TestClient

from trend_detective.api.app import app

client = TestClient(app)


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
