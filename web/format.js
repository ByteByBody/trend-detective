/* One number formatter for the whole page.
   Every rendered number carries its own unit, thousands are separated, and
   scientific notation never reaches the screen: a slope of 1.2e-4 reads as
   0.00012, not "1.2e-4". Shared by the browser (window.TDFormat) and by the
   Python tests (required through node). */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.TDFormat = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // A monthly slope becomes a per-decade trend by this factor (12 x 10).
  var MONTHS_PER_DECADE = 120;

  function defaultDecimals(v) {
    var a = Math.abs(v);
    // A whole number is a count (pixels, times, regions), not a measurement;
    // it never needs a fractional tail. Zero is a count too.
    if (Number.isInteger(v)) return 0;
    if (a >= 1000) return 0;
    if (a >= 100) return 1;
    if (a >= 1) return 2;
    if (a >= 0.1) return 3;
    if (a >= 0.01) return 4;
    if (a >= 0.001) return 5;
    return 6;
  }

  function group(intPart) {
    return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  // Fixed significant digits, as plain decimal text (never an exponent).
  function significantDigits(v, n) {
    if (v === 0) return "0";
    var places = n - 1 - Math.floor(Math.log10(Math.abs(v)));
    if (places < 0) {
      // More digits before the point than we want to keep: round to a power of
      // ten, still as plain text (1234.5 at 2 sig digits is 1200).
      var mag = Math.pow(10, -places);
      return (Math.round(v / mag) * mag).toFixed(0);
    }
    var s = v.toFixed(Math.min(places, 100));
    if (s.indexOf(".") !== -1) s = s.replace(/0+$/, "").replace(/\.$/, "");
    return s;
  }

  function formatNumber(value, unit, opts) {
    opts = opts || {};
    var v = Number(value);
    if (value === null || value === undefined || !isFinite(v)) return "n/a";

    var body;
    if (typeof opts.significant === "number") {
      // Trends and p-values are quoted by significant digits, not fixed
      // decimals, so 0.042 and 0.00012 both read at their own scale.
      body = significantDigits(v, opts.significant);
    } else {
      var decimals =
        typeof opts.decimals === "number" ? opts.decimals : defaultDecimals(v);
      body = v.toFixed(decimals);
      if (v !== 0 && Number(body) === 0) {
        // Real but smaller than this many decimals can show; "0.000" would
        // read as no trend at all, so floor it at the smallest it can show.
        body =
          (v < 0 ? "-" : "") + "<" + Math.pow(10, -decimals).toFixed(decimals);
      }
    }

    var negative = body.charAt(0) === "-";
    if (negative) body = body.slice(1);
    if (body.charAt(0) !== "<") {
      var dot = body.indexOf(".");
      var whole = dot === -1 ? body : body.slice(0, dot);
      var rest = dot === -1 ? "" : body.slice(dot);
      body = group(whole) + rest;
    }

    return (negative ? "-" : "") + (unit ? body + "\u2009" + unit : body);
  }

  function percentile(sorted, p) {
    // Linear interpolation between the two nearest ranks (numpy's default).
    if (!sorted.length) return NaN;
    if (sorted.length === 1) return sorted[0];
    var idx = (sorted.length - 1) * p;
    var lo = Math.floor(idx);
    var hi = Math.ceil(idx);
    if (lo === hi) return sorted[lo];
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
  }

  function slopeRange(values, lo, hi) {
    // Scale ramps to the 2nd-98th percentile so one outlier cannot flatten the
    // whole map; the legend ends quote the same clipped range.
    lo = lo === undefined ? 0.02 : lo;
    hi = hi === undefined ? 0.98 : hi;
    var finite = [];
    for (var i = 0; i < values.length; i++) {
      var v = values[i];
      if (v !== null && v !== undefined && isFinite(v)) finite.push(v);
    }
    if (!finite.length) return { min: 0, max: 1 };
    finite.sort(function (a, b) {
      return a - b;
    });
    var min = percentile(finite, lo);
    var max = percentile(finite, hi);
    if (!(max > min)) max = min + (min === 0 ? 1 : Math.abs(min) * 0.01);
    return { min: min, max: max };
  }

  return {
    formatNumber: formatNumber,
    slopeRange: slopeRange,
    percentile: percentile,
    MONTHS_PER_DECADE: MONTHS_PER_DECADE,
  };
});