"""Tests for the live NASA POWER regional source."""

import numpy as np
import pytest

from trend_detective.analysis import pipeline
from trend_detective.data import base, power


def _feature(lat: float, lon: float, values: dict, param: str = "T2M") -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat, 0.0]},
        "properties": {"parameter": {param: values}},
    }


def _payload(features: list[dict], param: str = "T2M") -> dict:
    return {
        "features": features,
        "parameters": {param: {"units": "C", "longname": "test"}},
        "header": {"title": "NASA/POWER test"},
    }


def test_parse_regional_drops_the_annual_row():
    payload = _payload([_feature(60.0, 0.0, {"202001": 1.0, "202002": 2.0, "202013": 99.0})])

    da = power.parse_regional(payload)

    assert list(da["time"].values) == [202001, 202002]


def test_parse_regional_maps_the_fill_sentinel_to_nan():
    payload = _payload([_feature(60.0, 0.0, {"202001": -999.0, "202002": 2.0})])

    da = power.parse_regional(payload)

    assert np.isnan(da.values[0, 0, 0])
    assert da.values[1, 0, 0] == pytest.approx(2.0)


def test_parse_regional_builds_a_north_up_grid():
    payload = _payload(
        [
            _feature(60.0, 0.0, {"202001": 1.0}),
            _feature(60.0, 0.5, {"202001": 2.0}),
            _feature(60.5, 0.0, {"202001": 3.0}),
            _feature(60.5, 0.5, {"202001": 4.0}),
        ]
    )

    da = power.parse_regional(payload)

    assert da.dims == ("time", "lat", "lon")
    assert list(da["lat"].values) == [60.5, 60.0]
    assert list(da["lon"].values) == [0.0, 0.5]
    assert da.sel(lat=60.5, lon=0.5).values[0] == pytest.approx(4.0)


def test_parse_regional_leaves_unreported_cells_nan():
    payload = _payload(
        [
            _feature(60.0, 0.0, {"202001": 1.0}),
            _feature(60.5, 0.5, {"202001": 4.0}),
        ]
    )

    da = power.parse_regional(payload)

    assert da.shape == (1, 2, 2)
    assert np.isnan(da.values[0, 0, 0])  # (60.5, 0.0) never reported


def test_tiles_covers_the_bbox_within_the_ten_degree_limit():
    boxes = power.tiles((55.0, 75.0, -60.0, -30.0))

    assert len(boxes) == 6
    for lat_min, lat_max, lon_min, lon_max in boxes:
        assert lat_max - lat_min <= power.MAX_SPAN_DEG
        assert lon_max - lon_min <= power.MAX_SPAN_DEG
    assert min(b[0] for b in boxes) == pytest.approx(55.0)
    assert max(b[1] for b in boxes) == pytest.approx(75.0)
    assert min(b[2] for b in boxes) == pytest.approx(-60.0)
    assert max(b[3] for b in boxes) == pytest.approx(-30.0)


def test_load_requests_the_mapped_parameter_for_the_configured_box():
    urls: list[str] = []

    def fake_fetch(url: str) -> dict:
        urls.append(url)
        return _payload([_feature(60.0, 0.0, {"202001": 1.0})])

    src = power.PowerRegionalSource(
        bbox=(60.0, 65.0, -45.0, -40.0), start=2020, end=2020, fetch=fake_fetch
    )
    src.load("surface_temperature")

    assert len(urls) == 1
    assert "parameters=T2M" in urls[0]
    assert "latitude-min=60.0" in urls[0]
    assert "latitude-max=65.0" in urls[0]
    assert "longitude-min=-45.0" in urls[0]
    assert "longitude-max=-40.0" in urls[0]
    assert "start=2020" in urls[0]
    assert "end=2020" in urls[0]


def test_load_issues_one_request_per_tile_and_merges_them():
    urls: list[str] = []

    def fake_fetch(url: str) -> dict:
        urls.append(url)
        lat = float(url.split("latitude-min=")[1].split("&")[0])
        lon = float(url.split("longitude-min=")[1].split("&")[0])
        return _payload([_feature(lat, lon, {"202001": 1.0})])

    src = power.PowerRegionalSource(
        bbox=(60.0, 80.0, -40.0, -20.0), start=2020, end=2020, fetch=fake_fetch
    )
    da = src.load("surface_temperature")

    assert len(urls) == 4  # 2 x 2 tiles of 10 degrees each
    assert da.shape == (1, 2, 2)
    assert np.allclose(da.values, 1.0)


def test_load_rejects_a_variable_power_does_not_carry():
    src = power.PowerRegionalSource(fetch=lambda url: {})

    with pytest.raises(KeyError):
        src.load("snow_water_equivalent")


def test_power_source_is_registered():
    assert isinstance(base.get_source("power"), power.PowerRegionalSource)


def test_power_source_advertises_exactly_its_mapped_parameters():
    assert set(power.PowerRegionalSource().variables()) == set(power.PARAMETERS)


def test_parsed_power_grid_runs_through_the_pipeline():
    months = [f"{2020 + k // 12}{k % 12 + 1:02d}" for k in range(48)]
    features = []
    for i, lat in enumerate([60.0, 60.5, 61.0, 61.5]):
        for j, lon in enumerate([-45.0, -44.5, -44.0, -43.5]):
            values = {m: 0.1 * k + 0.01 * i for k, m in enumerate(months)}
            features.append(_feature(lat, lon, values))

    da = power.parse_regional(_payload(features))
    res = pipeline.cry_wolf(da)

    assert res.slope.shape == (4, 4)
    assert res.n_naive == 16
    assert res.n_corrected <= res.n_naive