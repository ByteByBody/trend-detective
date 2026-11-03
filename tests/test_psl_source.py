"""Tests for the global NOAA PSL source.

PSL's THREDDS server exposes a NetCDF-subset service (NCSS) that does the
cutting server-side and returns classic NetCDF3. That matters twice: one small
download replaces a 29.6 MB file, and NetCDF3 is readable by scipy, which is
already a dependency — no HDF5 reader (h5py has no CPython 3.15 wheel).
"""

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from trend_detective.data import base, psl


def _dataset(n_time: int = 36, n_lat: int = 6, n_lon: int = 8) -> xr.Dataset:
    """A stand-in for the PSL NetCDF files, shaped like the real ones."""
    times = np.arange("2000-01", "2003-01", dtype="datetime64[M]")[:n_time]
    values = np.arange(n_time * n_lat * n_lon, dtype=float).reshape(n_time, n_lat, n_lon)
    return xr.Dataset(
        {"air": (("time", "lat", "lon"), values)},
        coords={
            "time": times,
            "lat": np.linspace(87.5, -87.5, n_lat),  # PSL stores north-to-south
            "lon": np.linspace(0.0, 315.0, n_lon),
        },
        attrs={"title": "test"},
    )


class _FakeDataset:
    """A mapping wrapper that records ``close`` (xarray blocks attribute writes)."""

    def __init__(self, ds: xr.Dataset) -> None:
        self._ds = ds
        self.closed = False

    def __contains__(self, name: str) -> bool:
        return name in self._ds

    def __getitem__(self, name: str) -> xr.DataArray:
        return self._ds[name]

    @property
    def data_vars(self):
        return self._ds.data_vars

    def close(self) -> None:
        self.closed = True


class _Fetcher:
    """Stands in for the network: records the URL, returns a fake cache path."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path("/cache/air.nc")
        self.calls: list[str] = []
        self.kwargs: list[dict] = []

    def __call__(self, url: str, name: str | None = None, **kwargs) -> Path:
        self.calls.append(url)
        self.kwargs.append({"name": name, **kwargs})
        return self.path


def _source(fetcher=None, opener=None, **kwargs):
    return psl.PslSource(
        start=kwargs.pop("start", 2000),
        end=kwargs.pop("end", 2002),
        stride=kwargs.pop("stride", 1),
        fetcher=fetcher or _Fetcher(),
        opener=opener or (lambda path: _dataset()),
        **kwargs,
    )


def test_ncss_url_asks_for_netcdf3_because_scipy_cannot_read_hdf5():
    url = psl.ncss_url(
        "Datasets/ncep/surface/air.mon.mean.nc", "air", start=1980, end=2024
    )

    assert url.startswith(psl.NCSS_BASE)
    assert "accept=netcdf3" in url
    assert "time_start=1980-01-01" in url
    assert "time_end=2024-12-31" in url


def test_ncss_url_carries_the_spatial_stride():
    url = psl.ncss_url("Datasets/ncep/air.nc", "air", start=1980, end=2024, stride=2)

    assert "horizStride=2" in url


def test_ncss_url_skips_the_stride_when_it_is_one():
    url = psl.ncss_url("Datasets/ncep/air.nc", "air", start=1980, end=2024, stride=1)

    assert "horizStride" not in url


def test_extract_grid_returns_a_time_lat_lon_array():
    da = psl.extract_grid(_dataset(), "air")

    assert da.dims == ("time", "lat", "lon")
    assert da.name == "value"


def test_extract_grid_sorts_lat_north_up():
    da = psl.extract_grid(_dataset(), "air")

    lats = da["lat"].values
    assert lats[0] > lats[-1]


def test_extract_grid_marks_the_time_axis_as_calendar_months():
    da = psl.extract_grid(_dataset(), "air")

    assert da.attrs["time_kind"] == "calendar"
    assert int(da["time"].values[0]) == 200001
    assert int(da["time"].values[12]) == 200101


def test_extract_grid_filters_to_the_requested_years():
    da = psl.extract_grid(_dataset(), "air", start=2001, end=2001)

    assert da.sizes["time"] == 12
    assert {int(t) // 100 for t in da["time"].values} == {2001}


def test_extract_grid_wraps_longitude_to_the_map_convention():
    # PSL stores 0..360; Leaflet and every other source use -180..180. Serving
    # raw values pins the raster outside the world view: bounds of [-2.5, 357.5]
    # draw the image off the right edge instead of over the globe.
    lon = np.array([0.0, 90.0, 180.0, 270.0])
    ds = xr.Dataset(
        {"air": (("time", "lat", "lon"), np.zeros((1, 2, 4)))},
        coords={"time": np.arange("2000-01", "2000-02", dtype="datetime64[M]"),
                "lat": np.array([90.0, -90.0]), "lon": lon},
    )

    da = psl.extract_grid(ds, "air")

    assert list(da["lon"].values) == [-180.0, -90.0, 0.0, 90.0]
    assert float(da["lon"].min()) >= -180.0
    assert float(da["lon"].max()) < 180.0


def test_extract_grid_keeps_a_full_global_longitude_axis():
    da = psl.extract_grid(_dataset(), "air")

    # The stand-in runs 0..315 in 8 steps; wrapping must not drop or duplicate.
    assert da.sizes["lon"] == 8
    assert len(set(da["lon"].values.tolist())) == 8
    assert float(da["lon"].min()) >= -180.0
    assert float(da["lon"].max()) < 180.0
    assert np.all(np.diff(da["lon"].values) > 0)


def test_extract_grid_wraps_a_real_0_360_axis_without_gaps():
    lon = np.arange(0.0, 360.0, 5.0)
    ds = xr.Dataset(
        {"air": (("time", "lat", "lon"), np.zeros((1, 2, lon.size)))},
        coords={"time": np.arange("2000-01", "2000-02", dtype="datetime64[M]"),
                "lat": np.array([90.0, -90.0]), "lon": lon},
    )

    values = psl.extract_grid(ds, "air")["lon"].values

    assert values.size == 72
    assert np.all(np.diff(values) == 5.0)  # -180, -175, ..., 175
    assert float(values.min()) == -180.0
    assert float(values.max()) == 175.0


def test_extract_grid_leaves_latitude_alone():
    da = psl.extract_grid(_dataset(), "air")

    assert float(da["lat"].max()) == 87.5
    assert float(da["lat"].min()) == -87.5


@pytest.mark.parametrize("stride", [2, 3])
def test_extract_grid_coarsens_space_not_time(stride):
    full = psl.extract_grid(_dataset(), "air")
    coarse = psl.extract_grid(_dataset(), "air", stride=stride)

    assert coarse.sizes["time"] == full.sizes["time"]
    assert coarse.sizes["lat"] == len(full["lat"].values[::stride])
    assert coarse.sizes["lon"] == len(full["lon"].values[::stride])


def test_extract_grid_rejects_a_variable_the_file_lacks():
    with pytest.raises(KeyError):
        psl.extract_grid(_dataset(), "nope")


def test_load_asks_the_cache_for_the_configured_years():
    fetcher = _Fetcher()
    _source(fetcher=fetcher, chunk=5).load("surface_temperature")

    url, = fetcher.calls
    assert "time_start=2000-01-01" in url and "time_end=2002-12-31" in url
    assert fetcher.kwargs[0]["name"] == "air.mon.mean.2000-2002.s1.nc"


def test_chunks_cover_the_range_once_without_gaps():
    assert list(psl._spans(1980, 2024, 5)) == [
        (1980, 1984),
        (1985, 1989),
        (1990, 1994),
        (1995, 1999),
        (2000, 2004),
        (2005, 2009),
        (2010, 2014),
        (2015, 2019),
        (2020, 2024),
    ]


def test_the_last_chunk_is_clipped_to_the_end_year():
    assert list(psl._spans(1980, 2002, 10)) == [(1980, 1989), (1990, 1999), (2000, 2002)]


def test_a_long_range_is_fetched_as_separate_cached_chunks():
    # One 45-year NCSS request was cut off by the server at ~7 min; the request
    # size has to stay small enough to finish.
    fetcher = _Fetcher()
    _source(fetcher=fetcher, start=1980, end=1994, chunk=5).load("surface_temperature")

    starts = [url.split("time_start=")[1][:4] for url in fetcher.calls]
    ends = [url.split("time_end=")[1][:4] for url in fetcher.calls]
    assert starts == ["1980", "1985", "1990"]
    assert ends == ["1984", "1989", "1994"]
    assert [kw["name"] for kw in fetcher.kwargs] == [
        "air.mon.mean.1980-1984.s1.nc",
        "air.mon.mean.1985-1989.s1.nc",
        "air.mon.mean.1990-1994.s1.nc",
    ]


def test_chunks_are_joined_into_one_continuous_calendar_axis():
    def year_dataset(year: int) -> xr.Dataset:
        times = np.arange(f"{year}-01", f"{year + 1}-01", dtype="datetime64[M]")
        values = np.zeros((12, 6, 8), dtype=float)
        return xr.Dataset(
            {"air": (("time", "lat", "lon"), values)},
            coords={
                "time": times,
                "lat": np.linspace(87.5, -87.5, 6),
                "lon": np.linspace(0.0, 315.0, 8),
            },
        )

    datasets = iter([year_dataset(2000), year_dataset(2001)])
    src = _source(start=2000, end=2001, chunk=1, opener=lambda path: next(datasets))

    da = src.load("surface_temperature")

    assert da.sizes["time"] == 24
    assert int(da["time"].values[0]) == 200001
    assert int(da["time"].values[-1]) == 200112


def test_load_hands_the_cache_a_validator():
    # NCSS can cut a transfer short without declaring a shorter length, and a
    # truncated NetCDF3 still starts with the right magic bytes.
    fetcher = _Fetcher()
    _source(fetcher=fetcher).load("surface_temperature")

    assert callable(fetcher.kwargs[0].get("validate"))


def test_the_validator_accepts_the_mapped_variable_and_closes_the_file():
    dataset = _FakeDataset(_dataset())
    src = _source(opener=lambda path: dataset)

    src.validator("air")(Path("/cache/air.nc"))

    assert dataset.closed


def test_the_validator_rejects_a_file_without_the_mapped_variable():
    broken = _FakeDataset(xr.Dataset({"nope": (("time",), np.zeros(3))}))
    src = _source(opener=lambda path: broken)

    with pytest.raises(KeyError):
        src.validator("air")(Path("/cache/air.nc"))

    assert broken.closed


def test_the_validator_rejects_an_empty_time_axis():
    empty = _FakeDataset(xr.Dataset({"air": (("time",), np.zeros(0))}))
    src = _source(opener=lambda path: empty)

    with pytest.raises(ValueError):
        src.validator("air")(Path("/cache/air.nc"))


def test_load_opens_the_local_file_not_the_url():
    opened: list = []

    def opener(path):
        opened.append(path)
        return _dataset()

    fetcher = _Fetcher(path=Path("/cache/air.nc"))
    src = _source(fetcher=fetcher, opener=opener)
    src.load("surface_temperature")

    assert opened == [Path("/cache/air.nc")]
    assert not isinstance(opened[0], str)


def test_load_rejects_a_variable_psl_does_not_carry():
    with pytest.raises(KeyError):
        _source().load("not_a_catalog_variable")


def test_psl_source_advertises_exactly_its_mapped_variables():
    assert set(_source().variables()) == set(psl.DATASETS)


def test_psl_source_is_registered():
    assert isinstance(base.get_source("psl"), psl.PslSource)


def test_load_closes_the_dataset_after_reading_it_into_memory():
    # The file handle must be released; the returned array owns its data.
    dataset = _FakeDataset(_dataset())

    da = _source(opener=lambda path: dataset).load("surface_temperature")

    assert dataset.closed
    assert np.isfinite(da.values).all()


def test_missing_reader_engine_gives_an_actionable_error(monkeypatch):
    monkeypatch.setattr(psl, "_READERS", ())

    with pytest.raises(RuntimeError) as excinfo:
        psl.open_dataset(Path("/cache/air.nc"))

    assert "pip install" in str(excinfo.value)


def test_psl_serves_every_catalog_variable():
    assert sorted(psl.PslSource().variables()) == [
        "evapotranspiration",
        "precipitation",
        "snow_water_equivalent",
        "soil_moisture_rootzone",
        "surface_temperature",
    ]


def test_datasets_rows_point_at_the_probed_files():
    assert psl.DATASETS["precipitation"][0].endswith("prate.sfc.mon.mean.nc")
    assert psl.DATASETS["soil_moisture_rootzone"][0].endswith(
        "soilw.10-200cm.mon.mean.nc"
    )
    assert psl.DATASETS["evapotranspiration"][0].endswith("lhtfl.sfc.mon.mean.nc")
    assert psl.DATASETS["snow_water_equivalent"][0].endswith("weasd.sfc.mon.mean.nc")