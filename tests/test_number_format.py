"""The single number formatter, exercised through node so it is the real code."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
MODULE = Path(__file__).resolve().parents[1] / "web" / "format.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _call(expr):
    script = (
        f"const {{formatNumber, MONTHS_PER_DECADE}} = require({json.dumps(str(MODULE))});"
        f"console.log(JSON.stringify({expr}));"
    )
    out = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def test_counts_get_thousands_separators_and_their_unit():
    assert _call("formatNumber(2014, 'pixels')") == "2,014\u2009pixels"


def test_whole_counts_never_grow_a_decimal_tail():
    # Regression: 806 and 153 are counts, not measurements; the magnitude rule
    # used to render them as "806.0 pixels" and "153.0 times".
    assert _call("formatNumber(806, 'pixels')") == "806\u2009pixels"
    assert _call("formatNumber(153.0, 'times')") == "153\u2009times"


def test_no_scientific_notation_for_a_tiny_slope():
    text = _call("formatNumber(0.00012, '°C per decade', {decimals: 3})")
    assert not re.search(r"\d[eE][+-]?\d", text)  # no exponent form
    assert text.endswith("\u2009°C per decade")


def test_missing_values_render_na():
    assert _call("formatNumber(null, 'pixels')") == "n/a"
    assert _call("formatNumber(NaN, 'pixels')") == "n/a"


def test_trend_per_decade_constant_is_120():
    assert _call("MONTHS_PER_DECADE") == 120
