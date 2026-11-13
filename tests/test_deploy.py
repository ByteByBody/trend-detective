"""Deploy helpers: the warm-cache bundle and the build-time fetch of it."""

import io
import tarfile

import pytest

from deploy import cache_bundle, fetch_cache


def _cache(tmp_path):
    cache = tmp_path / "cache"
    (cache / "analysis").mkdir(parents=True)
    (cache / "air.2m.mon.mean.1980-1984.s2.nc").write_bytes(b"nc")
    (cache / "analysis" / "surface_temperature.current.json").write_text("{}")
    (cache / "analysis" / "surface_temperature.old.json").write_text("{}")
    return cache


def test_cache_files_ship_chunks_and_only_the_current_signature(tmp_path):
    cache = _cache(tmp_path)
    names = [path.name for path in cache_bundle.cache_files(cache, "current")]
    assert names == [
        "air.2m.mon.mean.1980-1984.s2.nc",
        "surface_temperature.current.json",
    ]


def test_bundle_keeps_paths_relative_to_the_cache_root(tmp_path):
    cache = _cache(tmp_path)
    archive = cache_bundle.write_bundle(cache, "current", tmp_path / "bundle.tar.gz")

    with tarfile.open(archive) as tar:
        assert sorted(tar.getnames()) == [
            "air.2m.mon.mean.1980-1984.s2.nc",
            "analysis/surface_temperature.current.json",
        ]


def test_a_fetched_bundle_lands_under_the_cache_root(tmp_path):
    cache = _cache(tmp_path)
    archive = cache_bundle.write_bundle(cache, "current", tmp_path / "bundle.tar.gz")
    fetched = tmp_path / "fetched"

    fetch_cache.extract(archive, fetched)

    assert (fetched / "analysis" / "surface_temperature.current.json").read_text() == "{}"
    assert (fetched / "air.2m.mon.mean.1980-1984.s2.nc").read_bytes() == b"nc"


def test_cache_url_is_the_release_asset_for_the_signature():
    assert fetch_cache.cache_url("cache-abc", "cache-abc.tar.gz") == (
        "https://github.com/ByteByBody/trend-detective/releases/download/"
        "cache-abc/cache-abc.tar.gz"
    )


def test_archive_members_cannot_escape_the_cache_root(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"x"
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(tarfile.TarError):
        fetch_cache.extract(archive, tmp_path / "dest")


def test_missing_bodies_names_what_the_cache_cannot_serve(tmp_path):
    cache = _cache(tmp_path)

    assert cache_bundle.missing_bodies(
        ["surface_temperature", "precipitation"], cache, "current"
    ) == ["precipitation"]
