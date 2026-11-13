/* Trend-layer: cell math and the transparent trend raster.

   The map is a 47 x 96 grid of monthly slopes and two boolean verdicts. This
   module turns that into one RGBA image per mode: fill only on flagged cells,
   diverging by trend sign, with connected-patch outlines. Unflagged cells stay
   fully transparent so the satellite basemap remains readable.

   Shared by the browser (window.TDTrend) and the Python tests (required
   through node). No DOM here. */

(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.TDTrend = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const CELL_PX = 6;
  // Fill opacity tracks magnitude: a weak flag is a whisper, a strong one is
  // solid. The patch outline, not the fill, carries the flag/no-flag verdict,
  // so faint near-zero cells still read as flagged without washing out the map.
  const MIN_FILL_ALPHA = 72;
  const MAX_FILL_ALPHA = 235;
  const OUTLINE_ALPHA = 255;
  const COOL = [91, 140, 255];
  const NEUTRAL = [242, 233, 216];
  const WARM = [255, 90, 54];
  const PAPER = [242, 247, 247];
  const EMBER = [255, 107, 69];
  // Step changes are data defects, not verdicts, so they get their own hue
  // that is in neither the cool/warm ramp nor the outline palette.
  const STEP_COLOR = [178, 132, 255];

  function cellAt(lat, lon, latGrid, lonGrid) {
    let bi = 0;
    let bj = 0;
    let best = Infinity;
    for (let i = 0; i < latGrid.length; i++) {
      const dLat = latGrid[i] - lat;
      for (let j = 0; j < lonGrid.length; j++) {
        const dLon = lonGrid[j] - lon;
        const d = dLat * dLat + dLon * dLon;
        if (d < best) {
          best = d;
          bi = i;
          bj = j;
        }
      }
    }
    return { i: bi, j: bj, lat: latGrid[bi], lon: lonGrid[bj] };
  }

  function spacing(grid) {
    if (!grid || grid.length < 2) return 1;
    const steps = [];
    for (let i = 1; i < grid.length; i++) {
      const d = Math.abs(grid[i] - grid[i - 1]);
      if (d > 0) steps.push(d);
    }
    if (!steps.length) return 1;
    steps.sort((a, b) => a - b);
    return steps[Math.floor(steps.length / 2)];
  }

  function cellBounds(i, j, latGrid, lonGrid) {
    const dLat = spacing(latGrid) / 2;
    const dLon = spacing(lonGrid) / 2;
    const south = Math.max(-90, latGrid[i] - dLat);
    const north = Math.min(90, latGrid[i] + dLat);
    const west = Math.max(-180, lonGrid[j] - dLon);
    const east = Math.min(180, lonGrid[j] + dLon);
    return [
      [Math.min(south, north), Math.min(west, east)],
      [Math.max(south, north), Math.max(west, east)],
    ];
  }

  function fieldBounds(latGrid, lonGrid, maxLat) {
    // The geographic box the raster image is drawn on. buildRaster maps every
    // pixel's longitude through this same box, so a cell cannot drift away from
    // the selection rectangle that outlines it.
    const limit = maxLat === undefined || maxLat === null ? 90 : maxLat;
    const dLat = spacing(latGrid) / 2;
    const dLon = spacing(lonGrid) / 2;
    const latLo = Math.min.apply(null, latGrid);
    const latHi = Math.max.apply(null, latGrid);
    const lonLo = Math.min.apply(null, lonGrid);
    const lonHi = Math.max.apply(null, lonGrid);
    const south = Math.max(-limit, latLo - dLat);
    const north = Math.min(limit, latHi + dLat);
    let west;
    let east;
    if (lonHi - lonLo + 2 * dLon >= 359.999) {
      // The centres wrap the globe, so the cells tile the full circle. Anchoring
      // west at -180 and spanning all 360 degrees is what keeps every cell on
      // its own meridian; clamping the west edge to -180 while letting the east
      // stop a half step short squeezed the whole field westward.
      west = -180;
      east = 180;
    } else {
      west = Math.max(-180, lonLo - dLon);
      east = Math.min(180, lonHi + dLon);
    }
    return [
      [south, west],
      [north, east],
    ];
  }

  function flagAt(demo, i, j) {
    const naive = Boolean(demo.naive_mask && demo.naive_mask[i] && demo.naive_mask[i][j]);
    const corrected = Boolean(
      demo.corrected_mask && demo.corrected_mask[i] && demo.corrected_mask[i][j]
    );
    return { naive, corrected };
  }

  function classify(demo, i, j) {
    const flag = flagAt(demo, i, j);
    if (flag.corrected) return 2;
    if (flag.naive) return 1;
    return 0;
  }

  function columnForLongitude(lon, lonGrid) {
    // Grid longitudes are cell CENTRES, so a cell owns the half-step either
    // side of its coordinate. Distance wraps at the dateline: a grid whose
    // first centre sits on -180 also owns the sliver east of 178.125.
    let bj = 0;
    let best = Infinity;
    for (let j = 0; j < lonGrid.length; j++) {
      let d = lonGrid[j] - lon;
      if (d > 180) d -= 360;
      if (d < -180) d += 360;
      const dd = d * d;
      if (dd < best) {
        best = dd;
        bj = j;
      }
    }
    return bj;
  }

  function stepAt(demo, i, j) {
    // Sources and cached bodies from before the detector have no step field at
    // all; that is "nothing marked", not an error.
    return Boolean(demo.step_mask && demo.step_mask[i] && demo.step_mask[i][j]);
  }

  function stepCount(demo) {
    let n = 0;
    for (let i = 0; i < demo.lat.length; i++) {
      for (let j = 0; j < demo.lon.length; j++) {
        if (stepAt(demo, i, j)) n += 1;
      }
    }
    return n;
  }

  function describeCell(demo, i, j) {
    const flag = flagAt(demo, i, j);
    let status = "none";
    if (flag.naive && flag.corrected) status = "both";
    else if (flag.naive) status = "dropped";
    else if (flag.corrected) status = "corrected-only";
    const magnitude =
      demo.step_magnitude && demo.step_magnitude[i]
        ? demo.step_magnitude[i][j]
        : null;
    return {
      lat: demo.lat[i],
      lon: demo.lon[j],
      slope: demo.slope[i][j],
      p: demo.p_value ? demo.p_value[i][j] : null,
      naive: flag.naive,
      corrected: flag.corrected,
      status: status,
      step: stepAt(demo, i, j),
      stepMagnitude: magnitude === undefined ? null : magnitude,
    };
  }

  function quantile(values, p) {
    if (!values.length) return 0;
    const idx = (values.length - 1) * p;
    const lo = Math.floor(idx);
    const hi = Math.ceil(idx);
    if (lo === hi) return values[lo];
    return values[lo] + (values[hi] - values[lo]) * (idx - lo);
  }

  function modeMask(mode) {
    if (mode === "corrected") return function (cls) { return cls === 2; };
    if (mode === "dropped") return function (cls) { return cls === 1; };
    return function (cls) { return cls > 0; };
  }

  function trendRange(demo, mode) {
    const wanted = modeMask(mode);
    const values = [];
    const ny = demo.lat.length;
    const nx = demo.lon.length;
    for (let i = 0; i < ny; i++) {
      for (let j = 0; j < nx; j++) {
        if (!wanted(classify(demo, i, j))) continue;
        const v = demo.slope[i][j];
        if (v !== null && v !== undefined && isFinite(v)) values.push(Math.abs(v));
      }
    }
    if (!values.length) return { min: 0, max: 0, empty: true };
    values.sort((a, b) => a - b);
    const max = quantile(values, 0.98);
    return { min: -max, max: max, empty: false };
  }

  function mix(a, b, t) {
    return [
      Math.round(a[0] + (b[0] - a[0]) * t),
      Math.round(a[1] + (b[1] - a[1]) * t),
      Math.round(a[2] + (b[2] - a[2]) * t),
    ];
  }

  function fillFor(value, min, max) {
    const span = max - min;
    let t = span > 0 && isFinite(value) ? (value - min) / span : 0.5;
    t = Math.min(1, Math.max(0, t));
    return t < 0.5 ? mix(COOL, NEUTRAL, t * 2) : mix(NEUTRAL, WARM, (t - 0.5) * 2);
  }

  function rowsFor(lat, height, mercatorRows) {
    if (typeof mercatorRows === "function") return mercatorRows(lat, height);
    const rows = new Array(height);
    for (let y = 0; y < height; y++) {
      const t = height === 1 ? 0 : y / (height - 1);
      rows[y] = Math.round(t * (lat.length - 1));
    }
    return rows;
  }

  function buildRaster(demo, mode, height, mercatorRows, bounds) {
    const ny = demo.lat.length;
    const nx = demo.lon.length;
    const width = nx * CELL_PX;
    const h = Math.max(1, Math.round(height || 576));
    const range = trendRange(demo, mode);
    const wanted = modeMask(mode);
    const rows = rowsFor(demo.lat, h, mercatorRows);
    const box = bounds || fieldBounds(demo.lat, demo.lon);
    const west = box[0][1];
    const east = box[1][1];
    // Column ownership comes from each pixel's own longitude across the same
    // box the overlay is drawn on: the grid coordinates are cell centres, so a
    // pixel belongs to the cell whose meridian is nearest. Mapping the pixels
    // as if they filled the whole world while the overlay stopped a half step
    // short shifted every cell west of its own meridian.
    const columns = new Uint16Array(width);
    for (let x = 0; x < width; x++) {
      const lon = west + ((x + 0.5) / width) * (east - west);
      columns[x] = columnForLongitude(lon, demo.lon);
    }
    // Pixel spans each cell owns, so the step marker can sit at its centre
    // instead of being pinned to an assumed linear grid.
    const columnSpan = new Array(nx);
    for (let x = 0; x < width; x++) {
      const j = columns[x];
      if (!columnSpan[j]) columnSpan[j] = [x, x];
      else columnSpan[j][1] = x;
    }
    const rowSpan = new Array(ny);
    for (let y = 0; y < h; y++) {
      const i = Math.min(ny - 1, Math.max(0, rows[y]));
      if (!rowSpan[i]) rowSpan[i] = [y, y];
      else rowSpan[i][1] = y;
    }
    const data = new Uint8ClampedArray(width * h * 4);
    const classes = new Uint8Array(width * h);
    let count = 0;

    for (let y = 0; y < h; y++) {
      const i = Math.min(ny - 1, Math.max(0, rows[y]));
      for (let x = 0; x < width; x++) {
        const j = columns[x];
        const cls = classify(demo, i, j);
        if (!wanted(cls)) continue;
        const value = demo.slope[i][j];
        // A flag with no fitted slope has nothing to colour. Leave it
        // transparent instead of painting a neutral block over the map.
        if (value === null || value === undefined || !isFinite(value)) continue;
        const k = (y * width + x) * 4;
        const rgb = fillFor(value, range.min, range.max);
        // Symmetric range: |max| is the 98th-percentile magnitude.
        const strength = Math.min(1, Math.abs(value) / (Math.abs(range.max) || 1));
        data[k] = rgb[0];
        data[k + 1] = rgb[1];
        data[k + 2] = rgb[2];
        data[k + 3] = Math.round(
          MIN_FILL_ALPHA + (MAX_FILL_ALPHA - MIN_FILL_ALPHA) * strength
        );
        classes[y * width + x] = cls;
      }
    }

    for (let i = 0; i < ny; i++) {
      for (let j = 0; j < nx; j++) {
        if (!wanted(classify(demo, i, j))) continue;
        const value = demo.slope[i][j];
        if (value === null || value === undefined || !isFinite(value)) continue;
        count += 1;
      }
    }

    // Outline every boundary between a patch and something else. A dropped
    // pixel next to anything marks the boundary dashed, so the Naive view
    // shows exactly which claims the correction refuses to keep.
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < width; x++) {
        const cls = classes[y * width + x];
        if (!cls) continue;
        const left = x > 0 ? classes[y * width + x - 1] : 0;
        const right = x + 1 < width ? classes[y * width + x + 1] : 0;
        const up = y > 0 ? classes[(y - 1) * width + x] : 0;
        const down = y + 1 < h ? classes[(y + 1) * width + x] : 0;
        if (left === cls && right === cls && up === cls && down === cls) continue;

        const dropped = cls === 1 || left === 1 || right === 1 || up === 1 || down === 1;
        if (dropped && (x + y) % 12 >= 6) continue;
        const color = dropped ? EMBER : PAPER;
        const k = (y * width + x) * 4;
        data[k] = color[0];
        data[k + 1] = color[1];
        data[k + 2] = color[2];
        data[k + 3] = OUTLINE_ALPHA;
      }
    }

    // Step changes are painted last, in every mode: a data defect is worth
    // seeing whether or not the trend test flagged the cell.
    for (let i = 0; i < ny; i++) {
      for (let j = 0; j < nx; j++) {
        if (!stepAt(demo, i, j)) continue;
        const xs = columnSpan[j];
        const ys = rowSpan[i];
        if (!xs || !ys) continue;
        const cx = xs[0] + Math.floor((xs[1] - xs[0]) / 2);
        const cy = ys[0] + Math.floor((ys[1] - ys[0]) / 2);
        for (let dy = 0; dy < 2; dy++) {
          for (let dx = 0; dx < 2; dx++) {
            const x = cx + dx;
            const y = cy + dy;
            if (x >= width || y >= h) continue;
            const k = (y * width + x) * 4;
            data[k] = STEP_COLOR[0];
            data[k + 1] = STEP_COLOR[1];
            data[k + 2] = STEP_COLOR[2];
            data[k + 3] = OUTLINE_ALPHA;
          }
        }
      }
    }

    return {
      width: width,
      height: h,
      data: data,
      min: range.min,
      max: range.max,
      empty: range.empty,
      count: count,
    };
  }

  return {
    CELL_PX: CELL_PX,
    STEP_COLOR: STEP_COLOR,
    cellAt: cellAt,
    cellBounds: cellBounds,
    fieldBounds: fieldBounds,
    classify: classify,
    stepAt: stepAt,
    stepCount: stepCount,
    describeCell: describeCell,
    trendRange: trendRange,
    buildRaster: buildRaster,
  };
});
