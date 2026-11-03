"""One-time download cache for large gridded files.

Some data servers are happy to serve a 30 MB file but throttle per-request
OPeNDAP subsetting into minutes. Downloading the file once and opening it from
disk turns every run after the first into a local read.

Correctness matters more than speed here: a truncated download would open as a
valid NetCDF file with most of the record missing, silently producing a mostly
NaN map. So a download is written to ``<name>.part``, checked against the
``Content-Length`` of that same response, checked for a NetCDF magic number, and
only then renamed into place. A failure leaves the cache exactly as it was.

The declared length comes from the download response rather than a separate
``HEAD``: THREDDS' subset service answers ``HEAD`` with 500, and a second
request would be both slower and racier.
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

#: Where downloaded files live. Gitignored: it is a cache, never a source.
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"

#: First bytes of a NetCDF4/HDF5 file, and of a classic NetCDF3 file.
_NETCDF_MAGICS: tuple[bytes, ...] = (b"\x89HDF\r\n\x1a\n", b"CDF\x01", b"CDF\x02")

#: Network timeout for the download, in seconds.
TIMEOUT = 900.0

#: Statuses worth waiting for: rate limiting and transient server trouble.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Attempts per chunk and the first backoff, in seconds (doubles each retry).
ATTEMPTS = 4
BACKOFF = 20.0


class CacheError(RuntimeError):
    """Base class for cache failures."""


class IncompleteDownload(CacheError):
    """The server sent fewer bytes than it promised."""


class NotNetCDF(CacheError):
    """The body is not NetCDF — usually an HTML error page behind a 200."""


class NotUsable(CacheError):
    """The body claims to be NetCDF but the reader cannot open it."""


class Download(NamedTuple):
    """What a download produced, and what the server said it would produce."""

    written: int
    expected: int | None


class Downloader:
    """Callable ``(url, dest) -> Download``."""  # documentation only


def download_file(url: str, dest: Path, timeout: float = TIMEOUT) -> Download:
    """Stream ``url`` into ``dest``; report bytes written and declared length."""
    written = 0
    with urllib.request.urlopen(url, timeout=timeout) as response:
        header = response.headers.get("Content-Length")
        expected = int(header) if header else None
        with open(dest, "wb") as handle:
            while chunk := response.read(1 << 20):
                handle.write(chunk)
                written += len(chunk)
    return Download(written, expected)


def _is_netcdf(path: Path) -> bool:
    """Whether ``path`` starts with a known NetCDF/HDF5 magic number."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(8)
    except OSError:
        return False
    return any(head.startswith(magic) for magic in _NETCDF_MAGICS)


def _as_download(result) -> Download:
    """Accept a ``Download`` or a bare byte count from a downloader."""
    if isinstance(result, Download):
        return result
    return Download(int(result), None)


def fetch(
    url: str,
    cache_dir: Path = CACHE_DIR,
    download: Callable[[str, Path], Download] = download_file,
    name: str | None = None,
    validate: Callable[[Path], None] | None = None,
    attempts: int = ATTEMPTS,
    backoff: float = BACKOFF,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Return a local copy of ``url``, downloading it only when needed.

    A valid cached file costs no network at all.
    ``name`` overrides the filename derived from the URL, which matters for
    services that put the whole request in the query string. ``validate`` is
    called with the file before it is accepted, and on every cache hit, so a
    body that is truncated without a shorter ``Content-Length`` is discarded
    instead of served. Rate limiting and transient server errors are retried
    with exponential backoff; other client errors are not.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if name is None:
        name = url.rstrip("/").rsplit("/", 1)[-1]
    path = cache_dir / name

    if path.exists():
        if _is_netcdf(path) and _accepts(validate, path):
            return path
        # A previous run (or another tool) left something unusable here.
        path.unlink()

    part = path.with_name(path.name + ".part")
    try:
        result = _as_download(
            _with_retries(url, part, download, attempts, backoff, sleep)
        )
        if result.expected is not None and result.written != result.expected:
            raise IncompleteDownload(
                f"{url}: got {result.written} of {result.expected} bytes; "
                "the server truncated the transfer"
            )
        if not _is_netcdf(part):
            raise NotNetCDF(
                f"{url}: response is not a NetCDF file "
                "(an error page or a captive portal?)"
            )
        if not _accepts(validate, part):
            raise NotUsable(
                f"{url}: downloaded file starts like NetCDF but does not open; "
                "the transfer was probably cut short"
            )
        os.replace(part, path)
    except BaseException:
        # Never leave a partial file that a later run might mistake for data.
        part.unlink(missing_ok=True)
        raise

    return path


def _with_retries(
    url: str,
    part: Path,
    download: Callable[[str, Path], Download],
    attempts: int,
    backoff: float,
    sleep: Callable[[float], None],
) -> Download:
    """Download, waiting out rate limits and transient server errors."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return download(url, part)
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_STATUSES:
                raise
            last = exc
            if attempt + 1 < attempts:
                sleep(backoff * (2**attempt))
        except (urllib.error.URLError, TimeoutError) as exc:
            # Connection resets and read timeouts are worth one more attempt.
            last = exc
            if attempt + 1 < attempts:
                sleep(backoff * (2**attempt))
    raise last  # type: ignore[misc]


def _accepts(validate: Callable[[Path], None] | None, path: Path) -> bool:
    """Whether ``validate`` accepts ``path``; no validator means yes."""
    if validate is None:
        return True
    try:
        validate(path)
    except Exception:  # noqa: BLE001 - any reader failure means unusable
        return False
    return True