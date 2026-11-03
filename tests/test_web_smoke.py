import re

from fastapi.testclient import TestClient

from trend_detective.api import app as app_module
from trend_detective.api.app import app

client = TestClient(app)


def test_index_contains_key_elements():
    html = client.get("/").text
    assert "naive" in html.lower() and "corrected" in html.lower()
    assert "leaflet" in html.lower()


def test_index_links_app_js_and_css():
    html = client.get("/").text
    assert "app.js" in html and "styles.css" in html
    assert "plotly" in html.lower()


def test_index_exposes_diagnostics_and_keyboard_path():
    html = client.get("/").text
    for element_id in ("stat-insufficient", "stat-morans", "verdict-line", "chart"):
        assert element_id in html
    assert 'tabindex="0"' in html  # maps are keyboard-reachable


def test_static_assets_served():
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_health_endpoint_reports_ok():
    # Deployments and the hackathon demo check this before trusting the URL.
    r = client.get("/health")

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_does_not_touch_the_data_source(monkeypatch):
    # A health check that loads a live grid would time out on a cold PSL fetch
    # and report the app unhealthy while it is merely busy.
    def explode(variable):  # pragma: no cover - must not run
        raise AssertionError("health must not load data")

    monkeypatch.setattr(app_module._SOURCE, "load", explode)

    assert client.get("/health").status_code == 200


def test_index_declares_a_favicon():
    # Without a <link rel="icon"> the browser requests /favicon.ico and logs 404.
    html = client.get("/").text
    assert 'rel="icon"' in html
    assert "favicon.svg" in html


def test_favicon_is_served_as_svg():
    r = client.get("/static/favicon.svg")

    assert r.status_code == 200
    assert "svg" in r.headers["content-type"]
    assert "<svg" in r.text


def test_index_does_not_claim_the_bundled_sample_when_serving_live_data():
    # The footer used to say "Synthetic grid with a known answer" even while
    # serving NOAA reanalysis.
    html = client.get("/").text
    assert "Synthetic grid with a known answer" not in html
    assert 'id="data-source"' in html


def test_app_js_attributes_the_data_to_its_source():
    js = client.get("/static/app.js").text
    assert "data-source" in js
    assert "demo.source" in js


def test_flagged_pixels_stay_translucent_so_the_map_shows_through():
    # At 235/255 alpha the red and green slabs hid every coastline: the panels
    # read as coloured blobs, not as a map with regions flagged on it.
    match = re.search(r"const MASK_ALPHA = (\d+)", client.get("/static/app.js").text)
    assert match, "MASK_ALPHA is not a named constant"
    assert 100 <= int(match.group(1)) <= 190


def test_map_panels_are_square():
    # The grid covers the same -180..180 / -90..90 extent as the Web Mercator
    # world, which is square. In a wider window the image either letterboxes or
    # gets cropped, so the panel has to be square for the raster to fill it.
    css = client.get("/static/styles.css").text
    assert "aspect-ratio: 1 / 1" in css


def test_map_view_fits_the_world_exactly():
    js = client.get("/static/app.js").text
    # Leaflet snaps to whole zoom levels by default, which rounds a fitted view
    # outward and leaves a margin around the raster. Fractional zoom and a
    # zero minimum let the world fill the panel at any window width.
    assert "zoomSnap: 0" in js
    assert "minZoom: 0" in js
    assert "padding: [0, 0]" in js


def test_app_js_uses_a_keyless_basemap():
    # CARTO's light_all now answers with a 2 KB "API KEY REQUIRED" watermark, so
    # the panel showed grey blobs and text instead of a map. NASA GIBS serves
    # Blue Marble tiles with no key.
    js = client.get("/static/app.js").text
    assert "gibs.earthdata.nasa.gov" in js
    assert "cartocdn" not in js
    # Blue Marble is published up to level 8; beyond that upscale the same tile
    # instead of requesting a 404.
    assert "maxNativeZoom" in js


def test_app_js_clamps_overlay_bounds_to_the_world():
    # A source in the 0..360 convention would otherwise push the overlay outside
    # the visible map; the raster bounds must never exceed -180..180.
    js = client.get("/static/app.js").text
    assert "Math.max(-180" in js
    assert "Math.min(180" in js


def test_app_js_shows_which_source_produced_the_data():
    # `/catalog` reports the active source's provenance; hiding it in the UI
    # would leave the NOAA data presented as generic "satellite data".
    js = client.get("/static/app.js").text
    assert "v.source" in js
    assert "Source:" in js


def test_app_js_reports_undefined_morans_i_as_not_available():
    # Real POWER data can leave the corrected mask empty, so `morans_i` arrives
    # as null. `Number(null)` is 0, which would claim "no clustering" instead of
    # "undefined"; the UI has to say so.
    js = client.get("/static/app.js").text
    lines = [line.strip() for line in js.splitlines()]
    hits = [i for i, line in enumerate(lines) if "stat-morans" in line]
    assert hits, "app.js no longer renders the Moran's I statistic"
    block = " ".join(lines[hits[0] : hits[0] + 5])
    assert "null" in block
    assert "n/a" in block.lower()


def test_app_js_zooms_to_the_data_extent():
    # A regional source covers a small box far from the initial world view, so
    # the map has to fit its bounds or the data is an invisible speck.
    js = client.get("/static/app.js").text
    assert "fitBounds" in js
    assert "setView" not in js


def test_app_js_charts_dates_for_a_calendar_axis():
    # YYYYMM integers plotted directly render as "202.05k" on the axis.
    js = client.get("/static/app.js").text
    assert "time_labels" in js
    assert "time_kind" in js


def test_app_js_keeps_the_world_reachable():
    # Clamping the view to a regional data box hides the rest of the map, so
    # the only bounds clamp left is the initial world one.
    js = client.get("/static/app.js").text
    assert js.count("setMaxBounds") == 1
