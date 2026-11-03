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
