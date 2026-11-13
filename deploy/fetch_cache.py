"""Unpack the warm cache inside the deploy image.

Runs during the image build, after the tracked files are copied in. The tarball
is a public GitHub release asset, so the build needs no credentials.

Usage::

    python -m deploy.fetch_cache

``CACHE_URL`` overrides the URL; otherwise it is derived from the release tag
that matches the running code's analysis signature.
"""

from __future__ import annotations

import os
import tarfile
import urllib.request
from pathlib import Path

from deploy.cache_bundle import REPO_ROOT, bundle_name

RELEASE_ROOT = "https://github.com/ByteByBody/trend-detective/releases/download"
CACHE_DIR = REPO_ROOT / "data" / "cache"


def cache_url(tag: str, asset: str) -> str:
    """The public download URL of a release asset."""
    return f"{RELEASE_ROOT}/{tag}/{asset}"


def extract(archive: Path, destination: Path) -> None:
    """Unpack ``archive`` under ``destination``, refusing escaping members."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter="data")


def download(url: str, destination: Path) -> Path:
    """Fetch ``url`` to ``destination``."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=600) as response:
        destination.write_bytes(response.read())
    return destination


def main() -> None:
    from trend_detective.api import app as app_module

    signature = app_module._analysis_signature()
    url = os.environ.get("CACHE_URL") or cache_url(
        f"cache-{signature}", bundle_name(signature)
    )
    archive = download(url, Path("/tmp") / bundle_name(signature))
    extract(archive, CACHE_DIR)
    print(f"cache: {url} -> {CACHE_DIR}")


if __name__ == "__main__":
    main()
