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
        f"const {{formatNumber, MONTHS_PER_DECADE, slopeRange, percentile}} = "
        f"require({json.dumps(str(MODULE))});"
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


def test_whole_zero_is_a_count_not_a_measurement():
    # A flat variable corrects to zero flagged pixels; "0.00 pixels" is wrong.
    assert _call("formatNumber(0, 'pixels')") == "0\u2009pixels"


def test_a_real_value_too_small_for_its_decimals_is_floored_not_zeroed():
    # 0.00012 degC per decade is a real, tiny trend; "0.000" would read as none.
    text = _call("formatNumber(0.00012, '\u00b0C per decade', {decimals: 3})")
    assert text.startswith("<")
    assert "0.000\u2009" not in text


def test_trends_use_two_significant_digits():
    assert (
        _call("formatNumber(0.042, '\u00b0C per decade', {significant: 2})")
        == "0.042\u2009\u00b0C per decade"
    )
    assert _call("formatNumber(1.234, '', {significant: 2})") == "1.2"
    assert _call("formatNumber(12.34, '', {significant: 2})") == "12"


def test_a_tiny_trend_keeps_significant_digits_and_never_an_exponent():
    text = _call("formatNumber(0.00012, '\u00b0C per decade', {significant: 2})")
    assert text == "0.00012\u2009\u00b0C per decade"
    assert not re.search(r"\d[eE][+-]?\d", text)


def test_p_values_use_three_significant_digits():
    assert _call("formatNumber(0.0249, 'p', {significant: 3})") == "0.0249\u2009p"


def test_significant_digits_round_a_large_value_to_a_plain_number():
    # 1234.5 at two significant digits is 1200, still plain text (no exponent).
    text = _call("formatNumber(1234.5, '', {significant: 2})")
    assert text == "1,200"
    assert not re.search(r"[eE]", text)


def test_the_slope_ramp_scales_to_the_second_and_ninety_eighth_percentile():
    # One extreme pixel at 1000 must not flatten the ramp: with 100 values at
    # 0..99 the 98th percentile is 98, not 1000.
    out = _call("slopeRange(Array.from({length:100},(_,i)=>i).concat([1000]))")
    assert out["max"] == 98
    assert out["min"] == 2


def test_percentile_interpolates_between_ranks():
    assert _call("percentile([0,10,20,30,40], 0.5)") == 20


def test_no_scientific_notation_for_a_tiny_slope():
    text = _call("formatNumber(0.00012, '°C per decade', {decimals: 3})")
    assert not re.search(r"\d[eE][+-]?\d", text)  # no exponent form
    assert text.endswith("\u2009°C per decade")


def test_missing_values_render_na():
    assert _call("formatNumber(null, 'pixels')") == "n/a"
    assert _call("formatNumber(NaN, 'pixels')") == "n/a"


def test_trend_per_decade_constant_is_120():
    assert _call("MONTHS_PER_DECADE") == 120
