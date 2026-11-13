/* Mercator row sampling for the slope raster.

   The slope grid is evenly spaced in latitude, but a Web Mercator map is not:
   screen y grows like ln(tan(pi/4 + lat/2)). Drawing equal-latitude rows onto
   a Mercator overlay stretches them, so a feature at 60N lands near 76N and
   its colour bleeds into the sea. This maps each row of the image to the
   source latitude that belongs there.

   Shared by the browser (window.TDProjection) and the Python tests (required
   through node). */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.TDProjection = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  //: Latitude where Web Mercator is clipped; Leaflet uses the same value.
  var MAX_LAT = 85.05112878;

  function latToY(lat) {
    var rad = (lat * Math.PI) / 180;
    return Math.log(Math.tan(Math.PI / 4 + rad / 2));
  }

  function yToLat(y) {
    return ((2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180) / Math.PI;
  }

  function nearestRow(lat, target) {
    // Generic over ascending or descending latitude arrays: the demo grid runs
    // north to south, but nothing here depends on that.
    var best = 0;
    var bestDistance = Infinity;
    for (var i = 0; i < lat.length; i++) {
      var distance = Math.abs(lat[i] - target);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = i;
      }
    }
    return best;
  }

  function mercatorRows(lat, height) {
    // One source row per output row, spaced evenly in Mercator y between the
    // placeable limits. The poles are beyond MAX_LAT, so the outermost rows
    // resolve to the nearest real latitude instead of stretching to infinity.
    height = Math.max(1, Math.round(height || 0));
    var yTop = latToY(MAX_LAT);
    var yBottom = latToY(-MAX_LAT);
    var rows = new Array(height);
    for (var j = 0; j < height; j++) {
      var t = height === 1 ? 0 : j / (height - 1);
      rows[j] = nearestRow(lat, yToLat(yTop + (yBottom - yTop) * t));
    }
    return rows;
  }

  return {
    MAX_LAT: MAX_LAT,
    latToY: latToY,
    yToLat: yToLat,
    nearestRow: nearestRow,
    mercatorRows: mercatorRows,
  };
});