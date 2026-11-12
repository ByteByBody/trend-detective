"""Chart traces and Desmos-style zoom config, through node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
MODULE = Path(__file__).resolve().parents[1] / "web" / "chart-view.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

BODY = {
    "time_kind": "calendar",
    "time_labels": ["Jan 1980", "Feb 1980", "Mar 1980"],
    "value": [1.0, None, 3.0],
}


def call(expr):
    script = (
        f"const C = require({json.dumps(str(MODULE))});"
        f"const body = {json.dumps(BODY)};"
        f"console.log(JSON.stringify({expr}));"
    )
    out = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def test_config_zooms_with_the_wheel_and_resets_on_double_click():
    cfg = call("C.CONFIG")
    assert cfg["scrollZoom"] is True
    assert cfg["displayModeBar"] is False
    assert cfg["doubleClick"] == "reset"


def test_layout_pans_by_drag_and_has_no_title():
    lay = call("C.layout('degC', true, true)")
    assert lay["dragmode"] == "pan"
    assert "title" not in lay
    assert lay["yaxis"]["title"]["text"] == "degC"


def test_layout_uses_a_date_axis_for_calendar_data():
    assert call("C.layout('degC', true, true)")["xaxis"]["type"] == "date"
    assert call("C.layout('degC', true, false)")["xaxis"]["type"] == "linear"


def test_a_finite_trend_adds_a_dashed_trend_line():
    traces = call("C.traces(body, 'degC', 0.001)")
    assert len(traces) == 2
    assert traces[0]["y"] == [1.0, None, 3.0]
    assert traces[1]["line"]["dash"] == "dash"
    assert traces[1]["hoverinfo"] == "skip"


def test_a_missing_trend_draws_only_the_monthly_line():
    assert len(call("C.traces(body, 'degC', null)")) == 1


def test_the_trend_line_is_anchored_on_the_series_mean():
    traces = call("C.traces(body, 'degC', 0.001)")
    # mean of the finite values is 2.0; monthly slope 0.001 over 3 points
    assert traces[1]["y"][0] == pytest.approx(2.0 - 0.001)
    assert traces[1]["y"][2] == pytest.approx(2.0 + 0.001)
