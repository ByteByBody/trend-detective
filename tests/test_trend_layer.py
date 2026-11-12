"""Cell logic and the transparent trend raster, exercised through node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
MODULE = Path(__file__).resolve().parents[1] / "web" / "trend-layer.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

DEMO = {
    "lat": [10, 0, -10],
    "lon": [-20, 0, 20],
    "slope": [[-0.02, 0.0, 0.03], [0.01, 0.0, -0.01], [0.0, 0.0, 0.0]],
    "p_value": [[0.01, 0.02, 0.03], [0.04, 0.05, 0.06], [0.07, 0.08, 0.09]],
    "naive_mask": [[True, False, True], [True, False, False], [False, False, False]],
    "corrected_mask": [[True, False, False], [True, False, False], [False, False, False]],
}


def call(expr):
    script = (
        f"const M = require({json.dumps(str(MODULE))});"
        f"const demo = {json.dumps(DEMO)};"
        f"console.log(JSON.stringify({expr}));"
    )
    out = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def test_nearest_cell_snaps_to_the_grid():
    assert call("M.cellAt(7, 17, demo.lat, demo.lon)") == {
        "i": 0,
        "j": 2,
        "lat": 10,
        "lon": 20,
    }


def test_cell_bounds_are_edges_not_centres():
    assert call("M.cellBounds(1, 1, demo.lat, demo.lon)") == [[-5, -10], [5, 10]]


def test_cell_bounds_stay_inside_the_world():
    assert call("M.cellBounds(0, 0, [90, 80], [-180, -170])") == [
        [85, -180],
        [90, -175],
    ]
    assert call("M.cellBounds(1, 1, [90, 80], [170, 180])") == [[75, 175], [85, 180]]


def test_classification_separates_naive_only_from_survivors():
    assert call("M.classify(demo, 0, 0)") == 2
    assert call("M.classify(demo, 0, 2)") == 1
    assert call("M.classify(demo, 2, 2)") == 0


def test_describe_cell_reports_both_verdicts():
    dropped = call("M.describeCell(demo, 0, 2)")
    assert dropped["status"] == "dropped"
    assert dropped["naive"] is True and dropped["corrected"] is False
    assert call("M.describeCell(demo, 0, 0)")["status"] == "both"
    assert call("M.describeCell(demo, 2, 2)")["status"] == "none"


def test_describe_cell_keeps_a_missing_slope_null():
    demo = dict(DEMO)
    demo["slope"] = [[-0.02, None, 0.03], [0.01, 0.0, -0.01], [0.0, 0.0, 0.0]]
    cell = call(f"M.describeCell({json.dumps(demo)}, 0, 1)")
    assert cell["slope"] is None
    assert cell["p"] == 0.02
    assert cell["status"] == "none"


def test_range_is_symmetric_and_ignores_unflagged_slopes():
    demo = dict(DEMO)
    demo["slope"] = [[-0.02, 99.0, 0.03], [0.01, 0.0, -0.01], [0.0, 0.0, 0.0]]
    r = call(f"M.trendRange({json.dumps(demo)}, 'naive')")
    assert r["min"] == -abs(r["max"])
    assert r["max"] < 1.0
    assert r["empty"] is False


def test_an_empty_range_is_flagged():
    demo = {
        "lat": [0],
        "lon": [0],
        "slope": [[0.0]],
        "p_value": [[1.0]],
        "naive_mask": [[False]],
        "corrected_mask": [[False]],
    }
    assert call(f"M.trendRange({json.dumps(demo)}, 'naive')") == {
        "min": 0,
        "max": 0,
        "empty": True,
    }


def _raster(mode, height=4):
    return call(
        f"(() => {{ const r = M.buildRaster(demo, {json.dumps(mode)}, {height}, null);"
        "const at = (i, j) => {"
        "  const x = Math.floor((j + 0.5) * r.width / demo.lon.length);"
        "  const y = Math.floor((i + 0.5) * r.height / demo.lat.length);"
        "  const k = (y * r.width + x) * 4;"
        "  return Array.from(r.data.slice(k, k + 4));"
        "};"
        "return {width: r.width, height: r.height, count: r.count,"
        " min: r.min, max: r.max,"
        " dropped: at(0, 2), survivor: at(0, 0), empty: at(2, 2),"
        " outlined: (() => { let n = 0;"
        "  for (let k = 0; k < r.data.length; k += 4) {"
        "   if (r.data[k] === 242 && r.data[k+1] === 247 && r.data[k+2] === 247"
        "       && r.data[k+3] > 0) n += 1;"
        "  } return n; })()}; })()"
    )


def test_unflagged_cells_are_transparent():
    assert _raster("naive")["empty"][3] == 0


def test_negative_slope_is_cool_and_positive_is_warm():
    r = _raster("naive")
    assert r["survivor"][2] > r["survivor"][0]
    assert r["dropped"][0] > r["dropped"][2]


def test_count_matches_the_mode_mask():
    assert _raster("naive")["count"] == 3
    assert _raster("corrected")["count"] == 2
    assert _raster("dropped")["count"] == 1


def test_a_flagged_patch_gets_an_outline():
    assert _raster("naive", height=40)["outlined"] > 0


def test_zero_flags_produce_an_empty_raster():
    demo = {
        "lat": [0],
        "lon": [0],
        "slope": [[0.0]],
        "p_value": [[1.0]],
        "naive_mask": [[False]],
        "corrected_mask": [[False]],
    }
    r = call(
        f"(() => {{ const r = M.buildRaster({json.dumps(demo)}, 'corrected', 4, null);"
        "let opaque = 0;"
        "for (let k = 0; k < r.data.length; k += 4) if (r.data[k+3] > 0) opaque += 1;"
        "return {count: r.count, opaque}; })()"
    )
    assert r == {"count": 0, "opaque": 0}
