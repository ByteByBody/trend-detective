"""Shared pytest fixtures for the Trend Detective test suite."""

import pytest

from trend_detective.api import app as app_module


@pytest.fixture(autouse=True)
def pinned_synthetic_source(monkeypatch):
    """Pin the API to the bundled source for every test.

    ``app._SOURCE`` is built from ``config.DATA_SOURCE`` at import time, so a
    deployment that selects the live POWER source would otherwise make the
    suite depend on network access and on which variables POWER carries.
    """
    monkeypatch.setattr(app_module, "_SOURCE", app_module.data_source.get_source("synthetic"))
    app_module._demo_grid.cache_clear()
    app_module._demo_body.cache_clear()
    yield
    app_module._demo_grid.cache_clear()
    app_module._demo_body.cache_clear()