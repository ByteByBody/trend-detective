"""Tests for the one-time file cache used by the NOAA PSL source."""

from pathlib import Path
import urllib.error

import pytest

from trend_detective.data import cache

NETCDF4_MAGIC = b"\x89HDF\r\n\x1a\n"


def _payload(size: int = 64) -> bytes:
    return NETCDF4_MAGIC + b"\x00" * (size - len(NETCDF4_MAGIC))


class _Fetcher:
    """Records calls and writes a canned body, standing in for the network."""

    def __init__(self, body: bytes = None, declared: int | None = None) -> None:
        self.body = _payload() if body is None else body
        self.declared = len(self.body) if declared is None else declared
        self.downloads: list[Path] = []

    def __call__(self, url: str, dest: Path) -> cache.Download:
        self.downloads.append(dest)
        dest.write_bytes(self.body)
        return cache.Download(len(self.body), self.declared)


def test_fetch_downloads_to_the_named_cache_path(tmp_path):
    fetcher = _Fetcher()

    path = cache.fetch(
        "https://example.test/dir/air.mon.mean.nc",
        cache_dir=tmp_path,
        download=fetcher,
    )

    assert path == tmp_path / "air.mon.mean.nc"
    assert path.read_bytes() == _payload()
    assert len(fetcher.downloads) == 1


def test_second_fetch_is_a_cache_hit_with_no_network(tmp_path):
    fetcher = _Fetcher()
    cache.fetch("https://example.test/air.mon.mean.nc", cache_dir=tmp_path, download=fetcher)

    cache.fetch("https://example.test/air.mon.mean.nc", cache_dir=tmp_path, download=fetcher)

    # A cached file must cost zero network: no HEAD, no GET.
    assert len(fetcher.downloads) == 1


def test_truncated_download_is_rejected_and_not_cached(tmp_path):
    # PSL throttles hard from some networks; the observed failure was 32 KB of
    # a 29.6 MB file. That must never become a "valid" cache entry.
    fetcher = _Fetcher(body=_payload(32), declared=29_583_703)

    with pytest.raises(cache.IncompleteDownload) as excinfo:
        cache.fetch(
            "https://example.test/air.mon.mean.nc", cache_dir=tmp_path, download=fetcher
        )

    assert "32" in str(excinfo.value)
    assert not (tmp_path / "air.mon.mean.nc").exists()
    assert list(tmp_path.iterdir()) == []


def test_download_is_written_to_a_part_file_then_renamed(tmp_path):
    """A crash mid-download must not leave a file that looks cacheable."""
    fetcher = _Fetcher()
    seen: list[tuple[str, bool]] = []

    def download(url: str, dest: Path) -> cache.Download:
        result = fetcher(url, dest)
        seen.append((dest.name, (tmp_path / "air.mon.mean.nc").exists()))
        return result

    path = cache.fetch(
        "https://example.test/air.mon.mean.nc", cache_dir=tmp_path, download=download
    )

    # While bytes are landing, only the .part file exists.
    assert seen == [("air.mon.mean.nc.part", False)]
    assert path.exists()
    assert not path.with_name(path.name + ".part").exists()


def test_leftover_part_file_from_a_crash_is_ignored_and_replaced(tmp_path):
    stale = tmp_path / "air.mon.mean.nc.part"
    stale.write_bytes(b"\x89HDF stale rubbish")
    fetcher = _Fetcher()

    path = cache.fetch(
        "https://example.test/air.mon.mean.nc", cache_dir=tmp_path, download=fetcher
    )

    assert path.read_bytes() == _payload()
    assert not stale.exists()


def test_an_html_error_page_is_not_accepted_as_data(tmp_path):
    """A captive portal or error page is a 200 with a body; size can match."""
    body = b"<!DOCTYPE html><html><body>Service unavailable</body></html>"
    fetcher = _Fetcher(body=body)

    with pytest.raises(cache.NotNetCDF):
        cache.fetch(
            "https://example.test/air.mon.mean.nc", cache_dir=tmp_path, download=fetcher
        )

    assert list(tmp_path.iterdir()) == []


def test_classic_netcdf3_magic_is_accepted(tmp_path):
    fetcher = _Fetcher(body=b"CDF\x01" + b"\x00" * 60)

    path = cache.fetch("https://example.test/old.nc", cache_dir=tmp_path, download=fetcher)

    assert path.exists()


def test_a_server_that_declares_no_length_still_caches(tmp_path):
    fetcher = _Fetcher(declared=None)

    path = cache.fetch(
        "https://example.test/air.mon.mean.nc", cache_dir=tmp_path, download=fetcher
    )

    assert path.read_bytes() == _payload()


def test_cache_dir_is_created_on_demand(tmp_path):
    target = tmp_path / "nested" / "cache"
    fetcher = _Fetcher()

    path = cache.fetch(
        "https://example.test/air.mon.mean.nc", cache_dir=target, download=fetcher
    )

    assert path.parent == target and path.exists()


def test_fetch_honours_an_explicit_cache_name(tmp_path):
    # A subsetting service encodes the request in the query string, so two
    # different year ranges share a basename and must not collide on disk.
    fetcher = _Fetcher()

    short = cache.fetch(
        "https://example.test/ncss/air?time_start=1990-01-01&time_end=1990-12-31",
        cache_dir=tmp_path,
        download=fetcher,
        name="air.1990-1990.nc",
    )
    long = cache.fetch(
        "https://example.test/ncss/air?time_start=1980-01-01&time_end=2024-12-31",
        cache_dir=tmp_path,
        download=fetcher,
        name="air.1980-2024.nc",
    )

    assert short != long
    assert short.name == "air.1990-1990.nc"
    assert long.name == "air.1980-2024.nc"


def test_a_corrupt_cached_file_is_discarded_and_refetched(tmp_path):
    # Cache poisoning by a truncated file left by an older build.
    cached = tmp_path / "air.mon.mean.nc"
    cached.write_bytes(b"not netcdf at all")
    fetcher = _Fetcher()

    path = cache.fetch(
        "https://example.test/air.mon.mean.nc", cache_dir=tmp_path, download=fetcher
    )

    assert len(fetcher.downloads) == 1
    assert path.read_bytes() == _payload()


def test_size_mismatch_does_not_delete_an_unrelated_file(tmp_path):
    """The old cache entry survives a failed refresh of a different name."""
    keep = tmp_path / "other.nc"
    keep.write_bytes(_payload())
    fetcher = _Fetcher(body=_payload(8), declared=10**6)

    with pytest.raises(cache.IncompleteDownload):
        cache.fetch(
            "https://example.test/air.mon.mean.nc", cache_dir=tmp_path, download=fetcher
        )

    assert keep.read_bytes() == _payload()


def test_a_file_the_validator_cannot_read_is_not_cached(tmp_path):
    """A server can cut a transfer short without declaring a smaller length.

    The body still starts with a NetCDF magic number, so the only reliable test
    is the reader that will consume it.
    """

    def validate(path: Path) -> None:
        raise ValueError("truncated record")

    fetcher = _Fetcher()

    with pytest.raises(cache.NotUsable):
        cache.fetch(
            "https://example.test/air.mon.mean.nc",
            cache_dir=tmp_path,
            download=fetcher,
            validate=validate,
        )

    assert list(tmp_path.iterdir()) == []


def test_a_cached_file_the_validator_rejects_is_refetched(tmp_path):
    fetcher = _Fetcher()
    cached = tmp_path / "air.mon.mean.nc"
    cached.write_bytes(_payload(8))  # right magic, unusable content
    checked: list[bytes] = []

    def validate(path: Path) -> None:
        checked.append(path.read_bytes())
        if len(path.read_bytes()) < 32:
            raise ValueError("short")

    path = cache.fetch(
        "https://example.test/air.mon.mean.nc",
        cache_dir=tmp_path,
        download=fetcher,
        validate=validate,
    )

    assert len(fetcher.downloads) == 1
    assert path.read_bytes() == _payload()
    assert checked == [_payload(8), _payload()]


def test_a_rate_limited_download_is_retried_with_backoff(tmp_path):
    # PSL answers a burst of subset requests with 429; the chunked load must
    # wait it out instead of failing the whole variable.
    body = _payload()
    calls: list[int] = []
    slept: list[float] = []

    def download(url: str, dest: Path) -> cache.Download:
        calls.append(len(calls) + 1)
        if len(calls) < 3:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        dest.write_bytes(body)
        return cache.Download(len(body), len(body))

    path = cache.fetch(
        "https://example.test/air.mon.mean.nc",
        cache_dir=tmp_path,
        download=download,
        sleep=slept.append,
        backoff=5.0,
    )

    assert path.read_bytes() == body
    assert len(calls) == 3
    assert slept == [5.0, 10.0]


def test_a_client_error_is_not_retried(tmp_path):
    # A 404 will still be a 404; retrying it just delays the failure.
    slept: list[float] = []

    def download(url: str, dest: Path) -> cache.Download:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    with pytest.raises(urllib.error.HTTPError):
        cache.fetch(
            "https://example.test/absent.nc",
            cache_dir=tmp_path,
            download=download,
            sleep=slept.append,
        )

    assert slept == []


def test_retries_are_bounded(tmp_path):
    slept: list[float] = []

    def download(url: str, dest: Path) -> cache.Download:
        raise urllib.error.HTTPError(url, 503, "Unavailable", {}, None)

    with pytest.raises(urllib.error.HTTPError):
        cache.fetch(
            "https://example.test/air.mon.mean.nc",
            cache_dir=tmp_path,
            download=download,
            sleep=slept.append,
            attempts=3,
            backoff=1.0,
        )

    assert slept == [1.0, 2.0]
    assert list(tmp_path.iterdir()) == []