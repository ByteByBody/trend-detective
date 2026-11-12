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


def test_raster_columns_land_on_the_cells_cell_bounds_describe():
    # lat/lon are cell centres, so a cell spans half a step either side of its
    # coordinate. The raster must agree with cellBounds, or the selection
    # rectangle drifts half a cell away from the pixels it belongs to.
    lon = [-180 + 30 * j for j in range(12)]
    masks = [[False] * 12]
    masks[0][1] = True  # cell centred on -150
    demo = {
        "lat": [10],
        "lon": lon,
        "slope": [[0.03] + [0.0] * 11],
        "p_value": [[0.01] + [1.0] * 11],
        "naive_mask": [list(masks[0])],
        "corrected_mask": [list(masks[0])],
    }
    r = call(
        f"(() => {{ const r = M.buildRaster({json.dumps(demo)}, 'corrected', 40, null);"
        "const alphaAtLon = (lon) => {"
        "  const x = Math.min(r.width - 1, Math.max(0,"
        "    Math.round((lon + 180) / 360 * r.width - 0.5)));"
        "  return r.data[(Math.floor(r.height / 2) * r.width + x) * 4 + 3];"
        "};"
        # -160 is inside the cell centred on -150 (its west half).
        # -130 is inside the next cell, centred on -120.
        "return { westHalf: alphaAtLon(-160), nextCell: alphaAtLon(-130) }; })()"
    )
    assert r["westHalf"] > 0, "the west half of the cell must be painted"
    assert r["nextCell"] == 0, "the neighbouring unflagged cell must stay clear"


def _paint_lon_span(demo, mode="naive", height=1):
    return call(
        f"(() => {{ const r = M.buildRaster({json.dumps(demo)}, {json.dumps(mode)},"
        f" {height}, null);"
        "let lo = -1, hi = -1;"
        "for (let x = 0; x < r.width; x++) {"
        "  if (r.data[x * 4 + 3] > 0) { if (lo < 0) lo = x; hi = x; } }"
        "const b = M.fieldBounds(demo.lat, demo.lon);"
        "const west = b[0][1], east = b[1][1];"
        "const lon = (px) => west + (px / r.width) * (east - west);"
        "return { width: r.width, west, east, lo: lon(lo), hi: lon(hi + 1) }; })()"
    )


def test_field_bounds_span_the_world_when_the_longitudes_wrap_it():
    # The demo longitudes are cell centres stepping 3.75 from -180: the cells
    # tile the whole circle, so the field really covers all 360 degrees.
    lon = [-180 + 3.75 * j for j in range(96)]
    assert call(f"M.fieldBounds([10], {json.dumps(lon)}, 85.05112878)") == [
        [9.5, -180],
        [10.5, 180],
    ]


def test_field_bounds_hug_a_regional_grid():
    assert call("M.fieldBounds([10, 0, -10], [-20, 0, 20], 85.05112878)") == [
        [-15, -30],
        [15, 30],
    ]


def test_field_bounds_clamp_the_poles_to_the_mercator_limit():
    assert call("M.fieldBounds([90, 80], [-180, -170], 85.05112878)") == [
        [75, -180],
        [85.05112878, -165],
    ]


def test_a_painted_cell_covers_exactly_the_longitudes_its_bounds_describe():
    # The pixels must be laid out on the same box the overlay is drawn on. A
    # regional grid spans only its own degrees; treating it as a whole world
    # (or squashing a whole world down to the last centre) walks every cell off
    # the meridian it belongs to, and the selection rectangle drifts with it.
    for j, (west, east) in enumerate(((-30, -10), (-10, 10), (10, 30))):
        demo = {
            "lat": [10],
            "lon": [-20, 0, 20],
            "slope": [[0.03 if k == j else 0.0 for k in range(3)]],
            "p_value": [[0.01 if k == j else 1.0 for k in range(3)]],
            "naive_mask": [[k == j for k in range(3)]],
            "corrected_mask": [[k == j for k in range(3)]],
        }
        span = _paint_lon_span(demo)
        assert span["west"] == -30 and span["east"] == 30
        assert span["lo"] == pytest.approx(west, abs=0.5), (j, span)
        assert span["hi"] == pytest.approx(east, abs=0.5), (j, span)


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


def test_weak_flags_are_less_opaque_than_strong_flags():
    demo = {
        "lat": [10, 0, -10],
        "lon": [-20, 0, 20],
        "slope": [[-0.001, 0.0, 0.03], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        "p_value": [[0.01, 1.0, 0.01], [1.0, 1.0, 1.0], [1.0, 1.0, 1.0]],
        "naive_mask": [[True, False, True], [False, False, False], [False, False, False]],
        "corrected_mask": [[True, False, True], [False, False, False], [False, False, False]],
    }
    r = call(
        f"(() => {{ const r = M.buildRaster({json.dumps(demo)}, 'corrected', 40, null);"
        "const at = (i, j) => {"
        "  const x = Math.floor((j + 0.5) * r.width / demo.lon.length);"
        "  const y = Math.floor((i + 0.5) * r.height / demo.lat.length);"
        "  return r.data[(y * r.width + x) * 4 + 3];"
        "};"
        "return {weak: at(0, 0), strong: at(0, 2)}; })()"
    )
    assert 0 < r["weak"] < r["strong"]


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


def test_a_flagged_cell_without_a_slope_stays_transparent():
    # Soil moisture has gaps. A flag with no fitted slope has nothing to colour,
    # so it must not paint a neutral block over the map or claim a count.
    demo = {
        "lat": [0],
        "lon": [0],
        "slope": [[None]],
        "p_value": [[0.01]],
        "naive_mask": [[True]],
        "corrected_mask": [[True]],
    }
    r = call(
        f"(() => {{ const r = M.buildRaster({json.dumps(demo)}, 'naive', 4, null);"
        "let opaque = 0;"
        "for (let k = 0; k < r.data.length; k += 4) if (r.data[k+3] > 0) opaque += 1;"
        "return {count: r.count, opaque, empty: r.empty}; })()"
    )
    assert r == {"count": 0, "opaque": 0, "empty": True}
