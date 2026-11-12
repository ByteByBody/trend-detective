import re

from fastapi.testclient import TestClient

from trend_detective.api import app as app_module
from trend_detective.api.app import app

client = TestClient(app)


def index() -> str:
    return client.get("/").text


def app_js() -> str:
    return client.get("/static/app.js").text


def styles() -> str:
    return client.get("/static/styles.css").text


def test_static_assets_are_served():
    for name in (
        "app.js",
        "styles.css",
        "projection.js",
        "format.js",
        "trend-layer.js",
        "chart-view.js",
    ):
        assert client.get(f"/static/{name}").status_code == 200


def test_the_shell_and_assets_are_not_cached_so_a_stale_build_cannot_persist():
    # A cached HTML shell keeps pointing at old ``?v=`` scripts, so a visitor
    # can reload forever and never receive a new feature. Dev serves everything
    # no-store; the version bump stays as a belt-and-braces cache key.
    for path in ("/", "/static/app.js", "/static/styles.css"):
        r = client.get(path)
        assert r.status_code == 200
        assert "no-store" in r.headers.get("cache-control", "")


def test_the_page_declares_a_build_stamp_matching_the_asset_version():
    html = index()
    version = re.search(r"styles\.css\?v=(\d+)", html).group(1)
    stamp = re.search(r'data-build="(\d+)"', html)

    assert stamp is not None, "the page must show which build it is"
    assert stamp.group(1) == version


def test_health_endpoint_reports_ok():
    r = client.get("/health")

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_does_not_touch_the_data_source(monkeypatch):
    def explode(variable):  # pragma: no cover - must not run
        raise AssertionError("health must not load data")

    monkeypatch.setattr(app_module._SOURCE, "load", explode)

    assert client.get("/health").status_code == 200


def test_index_declares_a_favicon():
    html = index()
    assert 'rel="icon"' in html
    assert "favicon.svg" in html


def test_favicon_is_served_as_svg():
    r = client.get("/static/favicon.svg")

    assert r.status_code == 200
    assert "svg" in r.headers["content-type"]
    assert "<svg" in r.text


def test_index_shows_one_map_and_one_mode_switch():
    html = index()
    assert 'id="map"' in html
    assert "map-naive" not in html
    assert "map-corrected" not in html
    assert 'role="radiogroup"' in html
    assert html.count('id="mode-') == 2


def test_the_map_is_keyboard_reachable():
    html = index()
    map_tag = html.split('id="map"', 1)[1].split(">", 1)[0]
    assert 'tabindex="0"' in map_tag
    assert "Arrow keys" in map_tag


def test_index_carries_the_verdict_hud():
    html = index()
    for element_id in (
        "mode",
        "count-naive",
        "count-corrected",
        "delta",
        "legend",
        "legend-unit",
        "legend-min",
        "legend-zero",
        "legend-max",
        "legend-key-dashed",
        "facts",
        "loupe",
        "loupe-coords",
        "loupe-trend",
        "stage-name",
        "stage-source",
        "stripe-band",
    ):
        assert element_id in html


def test_index_carries_the_cell_readout_and_both_verdicts():
    html = index()
    for element_id in (
        "cell-readout",
        "cell-empty",
        "cell-detail",
        "cell-coords",
        "cell-trend",
        "cell-p",
        "cell-naive",
        "cell-corrected",
        "cell-status",
    ):
        assert element_id in html
    assert "Navie" not in html  # a typo in a verdict pill would be invisible


def test_index_carries_the_chart_and_its_gesture_hint():
    html = index()
    assert 'id="chart"' in html
    assert "Scroll to zoom" in html
    assert "double-click to reset" in html
    assert 'id="chart-reset"' in html
    assert "chart-grip" not in html


def test_index_has_a_method_dialog():
    html = index()
    assert "<dialog" in html
    assert 'id="method"' in html
    assert 'id="method-open"' in html
    assert 'id="data-source"' in html
    for element_id in ("method-cutoff", "method-coverage", "method-morans", "method-grid"):
        assert element_id in html


def test_index_links_the_module_stack_in_order():
    html = index()
    order = [
        "projection.js",
        "format.js",
        "trend-layer.js",
        "chart-view.js",
        "app.js",
    ]
    positions = [html.index(name) for name in order]
    assert positions == sorted(positions)


def test_page_loads_the_new_type_roles():
    html = index()
    assert "Bricolage+Grotesque" in html
    assert "Azeret+Mono" in html
    assert "Instrument+Sans" not in html
    assert "Source+Serif+4" not in html
    assert "IBM+Plex+Mono" not in html


def test_index_does_not_claim_the_bundled_sample_when_serving_live_data():
    html = index()
    assert "Synthetic grid with a known answer" not in html
    assert 'id="data-source"' in html


def test_stylesheet_uses_the_dark_petrol_palette():
    css = styles()
    for token in ("--petrol", "--ember", "--mint", "--ice", "--paper", "--glass"):
        assert token in css
    assert "#041416" in css or "#06191d" in css.lower()
    assert "backdrop-filter" in css


def test_the_stage_is_a_full_bleed_map_plate():
    css = styles()
    stage = css.split(".stage {", 1)[1].split("}", 1)[0]
    assert "position: relative" in stage
    assert "overflow: hidden" in stage
    assert ".map {" in css
    assert "position: absolute" in css.split(".map {", 1)[1].split("}", 1)[0]


def test_the_evidence_strip_holds_the_readout_and_chart_side_by_side():
    css = styles()
    evidence = css.split(".evidence {", 1)[1].split("}", 1)[0]
    assert "display: grid" in evidence
    assert "grid-template-columns" in evidence
    assert ".readout" in css and ".chart-panel" in css


def test_there_is_no_faint_slope_wash_left():
    js = app_js()
    css = styles()
    assert "slopeToDataURL" not in js
    assert "SLOPE_OPACITY" not in js
    assert "VIRIDIS" not in js
    assert ".slope-layer" not in css


def test_no_scientific_notation_survives_in_app_js():
    js = app_js()
    assert "toExponential" not in js
    # Strip colour literals first: #63E6BE is a hex colour, not 63E6.
    without_colours = re.sub(r"#[0-9A-Fa-f]{3,8}\b", "", js)
    assert not re.search(r"\d[eE][+-]?\d", without_colours)


def test_app_js_uses_the_cell_and_raster_module():
    js = app_js()
    assert "TDTrend.buildRaster" in js
    assert "TDTrend.cellAt" in js
    assert "TDTrend.cellBounds" in js
    assert "TDTrend.describeCell" in js
    assert "TDTrend.fieldBounds" in js


def test_app_js_builds_one_raster_per_mode_and_caches_the_urls():
    js = app_js()
    assert '["naive", "corrected", "dropped"]' in js
    assert "function rasterDataUrl(" in js
    assert "state.rasters" in js


def test_app_js_switches_modes_by_changing_raster_opacity():
    js = app_js()
    assert "function setMode(" in js
    assert "setOpacity" in js
    assert "aria-checked" in js
    assert "is-ghost" in js  # the dropped pixels dissolve away


def test_app_js_puts_counts_and_consequences_next_to_the_switch():
    js = app_js()
    assert "count-naive" in js and "count-corrected" in js
    assert "dropped by the correction" in js
    assert "No pixels dropped" in js
    assert "0 pixels flagged" in js
    assert 'fmt(demo.n_naive, "pixels")' in js


def test_app_js_paints_the_wing_ambient_from_the_visible_raster():
    html = index()
    js = app_js()
    assert 'id="stage-ambient"' in html
    assert "function updateAmbient(" in js
    assert "stage-ambient" in js


def test_the_stage_is_framed_like_an_instrument():
    html = index()
    css = styles()
    assert 'class="stage-frame"' in html
    assert ".stage-frame {" in css


def test_app_js_updates_the_legend_for_the_visible_mode():
    js = app_js()
    assert "legend-min" in js and "legend-max" in js and "legend-zero" in js
    assert "per decade" in js
    assert "legend-key-dashed" in js
    assert "function legendEndpoint(" in js


def test_variable_chips_keep_the_catalogue_unit_case():
    css = styles()
    unit = css.split(".variable__unit {", 1)[1].split("}", 1)[0]
    assert "text-transform: none" in unit


def test_the_hud_stacks_below_the_map_on_phones():
    css = styles()
    block = css.split("@media (max-width: 740px)", 1)[1].split("@media (max-width: 480px)", 1)[0]
    stage = block.split(".stage {", 1)[1].split("}", 1)[0]
    assert "height: auto" in stage
    legend = block.split(".hud--legend {", 1)[1].split("}", 1)[0]
    assert "position: relative" in legend
    switch = block.split(".switch-wrap {", 1)[1].split("}", 1)[0]
    assert "position: relative" in switch


def test_app_js_outlines_the_selected_cell_as_an_area():
    js = app_js()
    assert "L.rectangle" in js
    assert "cellBounds(cell.i, cell.j" in js
    assert "cell-select" in js
    assert "L.circleMarker" not in js  # the old dot is gone


def test_app_js_snaps_any_click_to_the_nearest_cell():
    js = app_js()
    assert 'map.on("click"' in js
    assert "TDTrend.cellAt(e.latlng.lat, e.latlng.lng" in js


def test_app_js_reports_trend_p_value_and_both_verdicts():
    js = app_js()
    assert "cell-trend" in js
    assert "cell-p" in js
    assert "Flagged by both tests" in js
    assert "Naive flag, dropped by the correction" in js
    assert "Not flagged by either test" in js
    assert "Naive · flagged" in js and "Corrected · flagged" in js


def test_app_js_keeps_a_missing_slope_readable():
    js = app_js()
    assert "trend n/a" in js
    assert "p n/a" in js


def test_app_js_has_a_hover_loupe_and_crosshair():
    js = app_js()
    assert "loupe-coords" in js
    assert "loupe-trend" in js
    assert "crosshair" in js
    assert 'map.on("mousemove"' in js


def test_app_js_hides_the_hover_loupe_when_a_cell_is_selected():
    js = app_js()
    block = js.split("function selectCell(", 1)[1].split("function selectCellAt", 1)[0]
    assert "hideLoupe()" in block


def test_app_js_pluralises_the_fingerprint_summary():
    js = app_js()
    assert "regions resemble" in js
    assert "region resembles" in js
    assert "DRIVER_NAMES" in js


def test_app_js_charts_through_the_chart_module():
    js = app_js()
    assert "TDChart.traces(" in js
    assert "TDChart.layout(" in js
    assert "TDChart.CONFIG" in js
    assert 'Plotly.react("chart"' in js
    assert "chart-reset" in js
    assert "autorange" in js


def test_app_js_keeps_the_selection_in_the_url_including_mode():
    js = app_js()
    assert "history.replaceState" in js
    assert "URLSearchParams" in js
    assert 'params.set("variable"' in js
    assert 'params.set("mode"' in js
    assert 'params.set("lat"' in js


def test_app_js_supports_keyboard_selection_and_zoom():
    js = app_js()
    for key in ("ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown", "Enter"):
        assert key in js
    assert "zoomIn" in js and "zoomOut" in js
    assert "keyboard: false" in js  # our handler owns the arrow keys


def test_app_js_draws_a_graticule_and_scales_the_map():
    js = app_js()
    assert "graticule" in js
    assert "L.control.scale" in js
    assert "L.control.zoom" in js


def test_app_js_uses_a_keyless_basemap_with_a_native_zoom_cap():
    js = app_js()
    assert "gibs.earthdata.nasa.gov" in js
    assert "maxNativeZoom" in js
    assert "cartocdn" not in js


def test_app_js_takes_the_field_bounds_from_the_raster_module():
    # One source for the box: the overlay, the border, the fitBounds and the
    # per-pixel longitude all read the same bounds, so the painted cells and
    # the selection rectangle cannot separate.
    js = app_js()
    assert "TDProjection.MAX_LAT" in js
    assert "TDTrend.fieldBounds" in js
    assert "L.imageOverlay(entry.url, state.bounds" in js
    assert "TDTrend.buildRaster(demo, mode, height, rows, state.bounds)" in js


def test_app_js_shows_the_source_and_diagnostics_under_method():
    js = app_js()
    assert "demo.source" in js
    assert "data-source" in js
    assert "method-cutoff" in js
    assert "method-coverage" in js
    assert "method-morans" in js
    assert "method-grid" in js


def test_app_js_reports_undefined_morans_i_as_not_available():
    js = app_js()
    lines = [line.strip() for line in js.splitlines()]
    hits = [i for i, line in enumerate(lines) if "method-morans" in line]
    assert hits, "app.js no longer renders the Moran's I statistic"
    block = " ".join(lines[hits[0] : hits[0] + 6])
    assert "null" in block or "Number.isFinite" in block
    assert "n/a" in block.lower()


def test_app_js_opens_the_method_dialog():
    js = app_js()
    assert "showModal" in js
    assert "method-open" in js


def test_app_js_shows_loading_progress_and_a_delayed_cold_run_note():
    js = app_js()
    assert "function setBusy(" in js
    assert "busy-note" in js
    # The copy lives in the markup; the script only reveals it after a slow load.
    assert "First run can take about 10 minutes" in index()


def test_app_js_renders_the_warming_stripes_from_the_payload():
    js = app_js()
    assert "function renderStripes(" in js
    assert "annual_anomaly" in js
    assert "stripe-band" in js


def test_app_js_fingerprint_mode_uses_the_one_map():
    js = app_js()
    assert "function nearestRegion(" in js
    assert "fingerprint-panel" in js
    assert "fingerprint-list" in js
    assert "map-naive" not in js


def test_app_js_reports_an_empty_corrected_field_as_valid():
    js = app_js()
    assert "n_corrected" in js
    assert "0 pixels flagged" in js
    assert "empty" in js


def test_the_legend_names_both_outline_states():
    html = index()
    assert "survives correction" in html
    assert "dropped by correction" in html
    assert "legend-key-dashed" in html


def test_the_page_has_a_skip_link_to_the_map():
    html = index()
    assert 'class="skip"' in html
    assert 'href="#stage"' in html


def test_reduced_motion_is_respected():
    css = styles()
    assert "prefers-reduced-motion" in css
    assert "animation: none" in css or "transition: none" in css
