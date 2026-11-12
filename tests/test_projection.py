"""The Mercator row map, exercised through node so it is the real code."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
MODULE = Path(__file__).resolve().parents[1] / "web" / "projection.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

#: The demo grid: 5 degrees of latitude per row, north to south.
LAT = [90 - 5 * i for i in range(37)]


def _call(expr):
    script = (
        f"const {{mercatorRows, nearestRow, latToY, yToLat, MAX_LAT}} = "
        f"require({json.dumps(str(MODULE))});"
        f"console.log(JSON.stringify({expr}));"
    )
    out = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def test_the_mercator_limit_is_where_leaflet_stops():
    assert abs(_call("MAX_LAT") - 85.05112878) < 1e-6


def test_latitude_and_mercator_y_round_trip():
    assert abs(_call("yToLat(latToY(60))") - 60) < 1e-9
    assert abs(_call("yToLat(latToY(-45))") + 45) < 1e-9


def test_the_row_map_is_monotonic_and_skips_the_unplaceable_poles():
    rows = _call(f"mercatorRows({json.dumps(LAT)}, 37)")

    assert len(rows) == 37
    assert rows == sorted(rows)
    # 90 and -90 are beyond Mercator's limit; 85 and -85 are the closest rows.
    assert rows[0] == 1
    assert rows[-1] == 35


def test_the_equator_lands_on_the_middle_row():
    rows = _call(f"mercatorRows({json.dumps(LAT)}, 37)")
    assert LAT[rows[18]] == 0


def test_a_taller_canvas_never_leaves_the_source_grid():
    rows = _call(f"mercatorRows({json.dumps(LAT)}, 200)")
    assert len(rows) == 200
    assert min(rows) >= 0 and max(rows) <= 35


def test_a_mid_latitude_row_resolves_to_its_own_latitude():
    # Regression: the raster was stretched as if screen y were linear in
    # latitude, which drew 60N at about 76N. Every target latitude must pick
    # the real row within half a cell of it.
    for target in (60, 45, 30, -30, -60):
        row = _call(f"nearestRow({json.dumps(LAT)}, {target})")
        assert abs(LAT[row] - target) <= 2.5