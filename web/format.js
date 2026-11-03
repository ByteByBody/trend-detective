/* One number formatter for the whole page.
   Every rendered number carries its own unit, thousands are separated, and
   scientific notation never reaches the screen: a slope of 1.2e-4 reads as
   0.0001, not "1.2e-4". Shared by the browser (window.TDFormat) and by the
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
    if (a === 0) return 2;
    // A whole number is a count (pixels, times, regions), not a measurement;
    // it never needs a fractional tail. Measurements are rarely integers.
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

  function formatNumber(value, unit, opts) {
    opts = opts || {};
    var v = Number(value);
    if (value === null || value === undefined || !isFinite(v)) return "n/a";

    var decimals =
      typeof opts.decimals === "number" ? opts.decimals : defaultDecimals(v);
    var s = v.toFixed(decimals);

    var negative = s.charAt(0) === "-";
    if (negative) s = s.slice(1);
    var dot = s.indexOf(".");
    var whole = dot === -1 ? s : s.slice(0, dot);
    var rest = dot === -1 ? "" : s.slice(dot);
    s = (negative ? "-" : "") + group(whole) + rest;

    return unit ? s + "\u2009" + unit : s;
  }

  return { formatNumber: formatNumber, MONTHS_PER_DECADE: MONTHS_PER_DECADE };
});
