from fastapi.testclient import TestClient

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
