"""Pack the warm analysis cache into one release asset.

The 48 MB of NetCDF chunks and analysis bodies live outside git: they are written
into a tarball and attached to a GitHub release, and the deploy image downloads
that tarball while it builds. The repository stays small and the running app
still boots with every analysis body already on disk.

Usage::

    python -m deploy.cache_bundle [--tag cache-v1]

The tarball is written to the working directory, then
``gh release create <tag> --title <tag> <tarball>`` publishes it.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache"


def cache_files(cache_dir: Path, signature: str) -> list[Path]:
    """The cache files worth shipping, in a stable order.

    Every raw chunk is needed: the map only reads analysis bodies, but
    ``/timeseries`` and ``/fingerprint`` reopen the NetCDF files. Bodies from an
    older signature are dead weight, so only the current one ships.
    """
    cache_dir = Path(cache_dir)
    chunks = sorted(path for path in cache_dir.glob("*.nc") if path.is_file())
    bodies = sorted(
        path
        for path in (cache_dir / "analysis").glob(f"*.{signature}.json")
        if path.is_file()
    )
    return chunks + bodies


def missing_bodies(
    variables: list[str], cache_dir: Path, signature: str
) -> list[str]:
    """Variables with no analysis body for ``signature``."""
    analysis = Path(cache_dir) / "analysis"
    return [
        variable
        for variable in variables
        if not (analysis / f"{variable}.{signature}.json").is_file()
    ]


def bundle_name(signature: str) -> str:
    """The release asset name for a cache signature."""
    return f"cache-{signature}.tar.gz"


def write_bundle(cache_dir: Path, signature: str, destination: Path) -> Path:
    """Write every current cache file into a gzipped tarball."""
    cache_dir = Path(cache_dir)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w:gz") as tar:
        for path in cache_files(cache_dir, signature):
            tar.add(path, arcname=path.relative_to(cache_dir).as_posix())
    return destination


def main() -> None:
    from trend_detective.api import app as app_module

    signature = app_module._analysis_signature()
    name = bundle_name(signature)
    archive = write_bundle(DEFAULT_CACHE_DIR, signature, Path.cwd() / name)
    print(f"{archive} ({archive.stat().st_size / 1_000_000:.1f} MB)")
    print(f"gh release create cache-{signature} --title cache-{signature} {name}")


if __name__ == "__main__":
    main()
