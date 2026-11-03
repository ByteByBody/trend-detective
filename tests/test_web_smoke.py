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


def test_the_verdict_note_says_what_a_false_alarm_is():
    # "False alarms removed" is jargon on its own: the page has to say what the
    # naive test claimed and why the corrected test will not stand behind it.
    js = client.get("/static/app.js").text
    assert "raw per-pixel test calls a trend" in js
    assert "will not stand behind" in js


def test_flagged_cells_are_drawn_as_dots_not_filled_cells():
    # Solid 5-degree blocks painted over the imagery; the map disappeared under
    # them. A dot per flagged cell leaves the basemap readable.
    js = client.get("/static/app.js").text
    assert "L.circleMarker" in js
    assert "maskToDataURL" not in js


def test_only_the_contested_pixels_are_marked():
    # Thousands of dots buried the map. The dots that matter are the ones the
    # correction drops: those are the claims being argued about.
    js = client.get("/static/app.js").text
    assert "function contestedMask(demo)" in js
    assert "drawDots(\"corrected\"" not in js


def test_each_dot_can_be_clicked():
    js = client.get("/static/app.js").text
    assert 'dot.on("click"' in js


def test_a_missed_click_snaps_to_the_nearest_dot():
    # Clicking between dots should still land on a flagged cell, not on the
    # grid cell under the cursor, which may not be flagged at all.
    js = client.get("/static/app.js").text
    assert "function nearestDot(key, latlng)" in js
    assert "nearestDot(key, e.latlng)" in js


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
    # `/catalog` reports the active source's provenance; the footer is the one
    # place that names it (the variable note stopped repeating it).
    js = client.get("/static/app.js").text
    assert "demo.source" in js
    assert "data-source" in js


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


def test_stylesheet_defines_the_instrument_tokens():
    css = client.get("/static/styles.css").text
    for token in ("--paper", "--ink-2", "--rule", "--signal", "--readout"):
        assert token in css
    assert "#E9EDF2" in css


def test_page_loads_the_three_type_roles():
    html = client.get("/").text
    assert "Instrument+Sans" in html
    assert "Source+Serif+4" in html
    assert "IBM+Plex+Mono" in html
    assert "Space+Grotesk" not in html


def test_no_scientific_notation_survives_in_app_js():
    js = client.get("/static/app.js").text
    assert "toExponential" not in js
    assert "fmtInt" not in js  # replaced by TDFormat.formatNumber


def test_every_stat_label_names_its_unit():
    html = client.get("/").text
    for phrase in ("(pixels)", "(p-value)", "Moran's I", "(%)"):
        assert phrase in html


def test_legend_labels_both_ends_with_the_unit_and_a_zero_mark():
    html = client.get("/").text
    assert "slope-min" in html and "slope-max" in html and "slope-zero" in html
    js = client.get("/static/app.js").text
    assert "MONTHS_PER_DECADE" in js or "formatNumber" in js


def test_tally_states_removed_of_flagged_not_the_raw_flag_count():
    js = client.get("/static/app.js").text
    assert "of" in js and "marked" in js


def test_the_red_dots_have_a_visible_key():
    html = client.get("/").text
    assert 'class="map-key"' in html
    assert "contested" in html.lower() or "dropped" in html.lower()


def test_each_dot_answers_with_its_own_numbers_on_hover():
    js = client.get("/static/app.js").text
    # The dot is the whole point: it has to say which cell it is, what the
    # trend is, and how strongly it was contested, without a click.
    assert "bindTooltip" in js
    assert "p_value" in js
    assert "per decade" in js


def test_the_variable_note_no_longer_repeats_the_source():
    js = client.get("/static/app.js").text
    assert "Source:" not in js  # the footer is the one place that names it


def test_a_default_cell_is_selected_after_the_analysis_lands():
    js = client.get("/static/app.js").text
    assert "function defaultCell(" in js
    assert "defaultCell(" in js.split("function renderDemo")[1]


def test_chart_titles_its_axis_with_units_and_draws_the_trend():
    js = client.get("/static/app.js").text
    assert "function drawChart(body, units" in js  # units flow into the axis
    assert "yaxis" in js and "title" in js
    assert "trendMonthly" in js  # the fitted line is drawn
    assert "toExponential" not in js


def test_the_chart_can_be_resized_and_the_height_persists():
    html = client.get("/").text
    assert "chart-grip" in html
    js = client.get("/static/app.js").text
    assert "td.chartHeight" in js
    assert "Plots.resize" in js
    # The separator reports its current size to assistive tech, not a constant.
    assert "aria-valuenow" in js


def test_the_slope_ramp_uses_the_clipped_range_helper():
    js = client.get("/static/app.js").text
    assert "slopeRange" in js  # not a bare min-max scan


def test_the_selection_is_kept_in_the_url():
    js = client.get("/static/app.js").text
    assert "history.replaceState" in js
    assert "URLSearchParams" in js


def test_a_cold_analysis_shows_progress_rather_than_a_blank_map():
    js = client.get("/static/app.js").text
    assert "function setBusy(" in js
    assert "first run" in js.lower()


def test_the_most_contested_cells_are_listed_and_clickable():
    html = client.get("/").text
    assert "contested-list" in html
    js = client.get("/static/app.js").text
    assert "function topContested(" in js
    assert "map.flyTo" in js


def test_arrow_keys_step_through_contested_cells():
    js = client.get("/static/app.js").text
    assert "ArrowRight" in js and "ArrowLeft" in js


def test_stats_are_one_ruled_panel_not_a_card_grid():
    css = client.get("/static/styles.css").text
    assert "border-radius" not in css.split(".stat {")[1].split("}")[0]


def test_the_map_windows_read_as_instrument_faces():
    html = client.get("/").text
    assert "map-frame" in html or "registration" in html.lower()


def test_the_masthead_carries_a_warming_stripe_band_with_a_fallback():
    js = client.get("/static/app.js").text
    assert "stripe" in js.lower()
    assert "function renderStripes(" in js


def test_the_square_map_rule_survives_the_restyle():
    css = client.get("/static/styles.css").text
    assert "aspect-ratio: 1 / 1" in css


def test_fingerprint_mode_outlines_matched_regions_and_polls_progress():
    js = client.get("/static/app.js").text
    assert "fingerprint" in js.lower()
    assert "region_rings" in js or "rings" in js
    assert "progress" in js


def test_fingerprint_mode_clicks_snap_to_a_region_centroid():
    js = client.get("/static/app.js").text
    assert "nearestRegion" in js
