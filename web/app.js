/* Trend Detective — one map, two verdicts.

   The whole page is one climate field. The switch picks which field is drawn:
   the raw per-pixel verdict, or the field-significance-corrected verdict.
   Everything numeric comes from /demo and /timeseries; everything visual is
   built here. Pure cell and raster maths live in trend-layer.js; the chart
   contract lives in chart-view.js. */

(() => {
  "use strict";

  const TILE_URL =
    "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/" +
    "BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/" +
    "{z}/{y}/{x}.jpeg";
  const TILE_ATTR =
    'Imagery &copy; <a href="https://earthdata.nasa.gov/gibs">NASA EOSDIS GIBS</a>';
  const TILE_MAX_NATIVE_ZOOM = 8;

  const WORLD_BOUNDS = [
    [-TDProjection.MAX_LAT, -180],
    [TDProjection.MAX_LAT, 180],
  ];

  // One raster per state: both verdicts, plus a ghost of the dropped claims
  // that dissolves when the correction wins the switch.
  const RASTER_MODES = ["naive", "corrected", "dropped"];
  const MODES = ["naive", "corrected"];

  const SHORT_NAMES = {
    soil_moisture_rootzone: "Soil",
    surface_temperature: "Air",
    precipitation: "Rain",
    evapotranspiration: "ET",
    snow_water_equivalent: "Snow",
  };

  const DRIVER_NAMES = {
    soil_moisture_rootzone: "soil moisture",
    surface_temperature: "temperature",
    precipitation: "precipitation",
    evapotranspiration: "evapotranspiration",
    snow_water_equivalent: "snow",
  };

  const CELL_STATUS = {
    both: "Flagged by both tests",
    dropped: "Naive flag, dropped by the correction",
    "corrected-only": "Flagged only after correction",
    none: "Not flagged by either test",
  };

  const VERDICT_WORDS = {
    naive: { on: "Naive · flagged", off: "Naive · clear" },
    corrected: { on: "Corrected · flagged", off: "Corrected · clear" },
  };

  const state = {
    variable: null,
    catalogByKey: {},
    demo: null,
    bounds: WORLD_BOUNDS,
    map: null,
    mode: "naive",
    selected: null,
    initial: null,
    rasters: null,
    overlays: {},
    cellLayer: null,
    borderLayer: null,
    demoToken: 0,
    seriesToken: 0,
    hoverFrame: 0,
    correctTimer: null,
    fingerprint: { on: false, data: null, timer: null, token: 0 },
  };

  const el = (id) => document.getElementById(id);
  const fmt = (value, unit, opts) => TDFormat.formatNumber(value, unit, opts);
  const MONTHS_PER_DECADE = TDFormat.MONTHS_PER_DECADE;

  // --- small formatters ----------------------------------------------------

  function coord(value) {
    return `${fmt(value, "", { decimals: 1 })}°`;
  }

  function place(lat, lon) {
    return `${coord(lat)}, ${coord(lon)}`;
  }

  function trendParts(slope, units) {
    if (slope === null || slope === undefined || !isFinite(Number(slope))) {
      return null;
    }
    const value = Number(slope) * MONTHS_PER_DECADE;
    const unit = units ? `${units} per decade` : "per decade";
    const text = fmt(value, unit, { significant: 2 });
    const cut = text.indexOf("\u2009");
    const number = cut === -1 ? text : text.slice(0, cut);
    const label = cut === -1 ? unit : text.slice(cut + 1);
    return {
      number: value > 0 ? `+${number}` : number,
      unit: label,
      text,
      value,
    };
  }

  function trendLabel(slope, units) {
    const parts = trendParts(slope, units);
    return parts ? parts.text : "trend n/a";
  }

  function setTrendText(node, slope, units) {
    if (!node) return;
    const parts = trendParts(slope, units);
    node.textContent = "";
    if (!parts) {
      node.textContent = "trend n/a";
      node.dataset.sign = "none";
      return;
    }
    const number = document.createElement("span");
    number.className = "readout__number";
    number.textContent = parts.number;
    const unit = document.createElement("span");
    unit.className = "readout__unit";
    unit.textContent = parts.unit;
    node.appendChild(number);
    node.appendChild(document.createTextNode(" "));
    node.appendChild(unit);
    node.dataset.sign = parts.value > 0 ? "warm" : parts.value < 0 ? "cool" : "flat";
  }

  function fingerprintStatus(text) {
    const node = el("fingerprint-status");
    if (node) node.textContent = text;
  }

  // --- map -----------------------------------------------------------------

  function makeMap() {
    const map = L.map("map", {
      center: [10, 0],
      zoom: 1,
      minZoom: 0,
      maxZoom: 12,
      // Whole-number zoom leaves a margin around a fitted world; fractional
      // zoom lets the field fill the plate at any window width.
      zoomSnap: 0,
      worldCopyJump: false,
      attributionControl: true,
      zoomControl: false,
      keyboard: false,
      maxBoundsViscosity: 1,
    });
    state.map = map;

    L.tileLayer(TILE_URL, {
      attribution: TILE_ATTR,
      noWrap: true,
      maxNativeZoom: TILE_MAX_NATIVE_ZOOM,
    }).addTo(map);

    map.createPane("graticule");
    map.getPane("graticule").style.zIndex = "350";
    map.getPane("graticule").style.pointerEvents = "none";
    map.createPane("raster");
    map.getPane("raster").style.zIndex = "400";
    map.getPane("raster").style.pointerEvents = "none";
    map.createPane("field");
    map.getPane("field").style.zIndex = "425";
    map.getPane("field").style.pointerEvents = "none";
    map.createPane("selection");
    map.getPane("selection").style.zIndex = "450";

    map.setMaxBounds(WORLD_BOUNDS);
    map.fitBounds(WORLD_BOUNDS, { padding: [0, 0], animate: false });
    L.control.zoom({ position: "bottomright" }).addTo(map);
    L.control.scale({ position: "bottomleft", imperial: false, maxWidth: 110 }).addTo(map);

    map.on("click", (e) => {
      if (!state.demo) return;
      // In fingerprint mode the unit of interest is the region, not the pixel.
      if (state.fingerprint.on) {
        const region = nearestRegion(e.latlng.lat, e.latlng.lng);
        if (region) {
          selectCellAt(region.lat, region.lon, { fly: true });
          return;
        }
      }
      const cell = TDTrend.cellAt(e.latlng.lat, e.latlng.lng, state.demo.lat, state.demo.lon);
      selectCell(cell.i, cell.j, { pan: false });
    });

    map.on("mousemove", (e) => {
      scheduleHover(e.latlng, e.containerPoint);
    });
    map.on("mouseout", hideLoupe);

    return map;
  }

  function drawGraticule(map) {
    const group = L.layerGroup();
    const line = (points, text, weight, dash) =>
      L.polyline(points, {
        pane: "graticule",
        className: `graticule${text ? " graticule--major" : ""}`,
        color: text ? "rgba(142, 216, 255, 0.18)" : "rgba(142, 216, 255, 0.1)",
        weight: weight,
        dashArray: dash,
        interactive: false,
      }).addTo(group);

    for (let lat = -60; lat <= 60; lat += 30) {
      line(
        [
          [lat, -180],
          [lat, 180],
        ],
        lat === 0,
        lat === 0 ? 1 : 0.6,
        lat === 0 ? null : "2 7"
      );
    }
    for (let lon = -150; lon <= 150; lon += 30) {
      line(
        [
          [-TDProjection.MAX_LAT, lon],
          [TDProjection.MAX_LAT, lon],
        ],
        lon === 0,
        lon === 0 ? 1 : 0.6,
        lon === 0 ? null : "2 7"
      );
    }
    group.addTo(map);
  }

  function scheduleHover(latlng, point) {
    if (state.hoverFrame) return;
    state.hoverFrame = window.requestAnimationFrame(() => {
      state.hoverFrame = 0;
      if (!state.demo) return;
      const cell = TDTrend.cellAt(
        latlng.lat,
        latlng.lng,
        state.demo.lat,
        state.demo.lon
      );
      const info = TDTrend.describeCell(state.demo, cell.i, cell.j);
      const stage = el("stage");
      const loupe = el("loupe");
      const crosshair = el("crosshair");
      if (!stage || !loupe || !crosshair) return;
      const names = state.demo.units || "";
      el("loupe-coords").textContent = place(cell.lat, cell.lon);
      el("loupe-trend").textContent = trendLabel(info.slope, names);
      loupe.dataset.status = info.status;
      loupe.hidden = false;
      crosshair.hidden = false;

      const x = Math.round(point.x);
      const y = Math.round(point.y);
      const stageBox = stage.getBoundingClientRect();
      const box = loupe.getBoundingClientRect();
      let left = x + 18;
      let top = y + 18;
      if (left + box.width > stageBox.width - 8) left = x - box.width - 18;
      if (top + box.height > stageBox.height - 8) top = y - box.height - 18;
      loupe.style.transform = `translate(${Math.max(8, left)}px, ${Math.max(
        8,
        top
      )}px)`;

      const vertical = crosshair.querySelector(".crosshair__x");
      const horizontal = crosshair.querySelector(".crosshair__y");
      if (vertical) vertical.style.left = `${x}px`;
      if (horizontal) horizontal.style.top = `${y}px`;
    });
  }

  function hideLoupe() {
    if (state.hoverFrame) {
      window.cancelAnimationFrame(state.hoverFrame);
      state.hoverFrame = 0;
    }
    const loupe = el("loupe");
    const crosshair = el("crosshair");
    if (loupe) loupe.hidden = true;
    if (crosshair) crosshair.hidden = true;
  }

  // --- field extent and raster --------------------------------------------

  function gridBounds(demo) {
    // The raster module owns the field's geographic box; the overlay, the
    // border and the per-pixel longitudes all have to agree on it.
    return TDTrend.fieldBounds(demo.lat, demo.lon, TDProjection.MAX_LAT);
  }

  function rasterHeight(width, bounds) {
    const south = Math.max(-TDProjection.MAX_LAT, bounds[0][0]);
    const north = Math.min(TDProjection.MAX_LAT, bounds[1][0]);
    const ySpan = TDProjection.latToY(north) - TDProjection.latToY(south);
    const xSpan = ((bounds[1][1] - bounds[0][1]) * Math.PI) / 180;
    if (!(xSpan > 0) || !(ySpan > 0)) return Math.max(32, width);
    return Math.max(32, Math.round((width * ySpan) / xSpan));
  }

  function rasterRowsFor(bounds) {
    const south = Math.max(-TDProjection.MAX_LAT, bounds[0][0]);
    const north = Math.min(TDProjection.MAX_LAT, bounds[1][0]);
    const yTop = TDProjection.latToY(north);
    const yBottom = TDProjection.latToY(south);
    return function (latGrid, height) {
      const rows = new Array(height);
      for (let y = 0; y < height; y++) {
        const t = height === 1 ? 0 : y / (height - 1);
        rows[y] = TDProjection.nearestRow(
          latGrid,
          TDProjection.yToLat(yTop + (yBottom - yTop) * t)
        );
      }
      return rows;
    };
  }

  function rasterDataUrl(raster) {
    const canvas = document.createElement("canvas");
    canvas.width = raster.width;
    canvas.height = raster.height;
    const context = canvas.getContext("2d");
    const image = context.createImageData(raster.width, raster.height);
    image.data.set(raster.data);
    context.putImageData(image, 0, 0);
    return canvas.toDataURL("image/png");
  }

  function buildRasterSet(demo) {
    const width = demo.lon.length * TDTrend.CELL_PX;
    const height = rasterHeight(width, state.bounds);
    const rows = rasterRowsFor(state.bounds);
    const rasters = {};
    RASTER_MODES.forEach((mode) => {
      const raster = TDTrend.buildRaster(demo, mode, height, rows, state.bounds);
      rasters[mode] = {
        url: rasterDataUrl(raster),
        min: raster.min,
        max: raster.max,
        empty: raster.empty,
        count: raster.count,
      };
    });
    return rasters;
  }

  function clearRasterLayers() {
    if (!state.map) return;
    RASTER_MODES.forEach((mode) => {
      const layer = state.overlays[mode];
      if (layer) state.map.removeLayer(layer);
      state.overlays[mode] = null;
    });
    if (state.borderLayer) {
      state.map.removeLayer(state.borderLayer);
      state.borderLayer = null;
    }
  }

  function drawFieldBorder() {
    if (!state.map) return;
    state.borderLayer = L.rectangle(state.bounds, {
      pane: "field",
      className: "field-border",
      color: "rgba(242, 247, 247, 0.3)",
      weight: 1,
      fill: false,
      interactive: false,
    }).addTo(state.map);
  }

  function updateAmbient() {
    // The field is square; the stage is not. Blur a copy of the visible raster
    // into the wings so the plate reads as one lit surface, not a stamp on a
    // dark page.
    const node = el("stage-ambient");
    if (!node) return;
    const entry = state.rasters ? state.rasters[state.mode] : null;
    node.style.backgroundImage = entry && !entry.empty ? `url("${entry.url}")` : "none";
  }

  function addRasterOverlays() {
    if (!state.map || !state.rasters) return;
    RASTER_MODES.forEach((mode) => {
      const entry = state.rasters[mode];
      const layer = L.imageOverlay(entry.url, state.bounds, {
        opacity: mode === state.mode ? 1 : 0,
        interactive: false,
        pane: "raster",
        className: `raster raster--${mode}`,
      }).addTo(state.map);
      state.overlays[mode] = layer;
    });
  }

  // --- switch, counts, legend ---------------------------------------------

  function applyRasterOpacity() {
    RASTER_MODES.forEach((mode) => {
      const layer = state.overlays[mode];
      if (!layer) return;
      const target = mode === state.mode ? 1 : 0;
      layer.setOpacity(target);
    });
  }

  function dissolveDropped() {
    const layer = state.overlays.dropped;
    const image = layer && layer.getElement && layer.getElement();
    if (!image) return;
    image.classList.remove("is-ghost");
    void image.offsetWidth;
    image.classList.add("is-ghost");

    const stage = el("stage");
    if (!stage) return;
    stage.classList.remove("is-correcting");
    void stage.offsetWidth;
    stage.classList.add("is-correcting");
    clearTimeout(state.correctTimer);
    state.correctTimer = window.setTimeout(() => {
      stage.classList.remove("is-correcting");
    }, 720);
  }

  function setMode(mode, options) {
    if (!MODES.includes(mode)) return;
    const previous = state.mode;
    state.mode = mode;
    const group = el("mode");
    if (group) group.dataset.mode = mode;
    el("mode-naive").setAttribute("aria-checked", String(mode === "naive"));
    el("mode-corrected").setAttribute(
      "aria-checked",
      String(mode === "corrected")
    );

    applyRasterOpacity();
    updateAmbient();
    if (state.demo) {
      updateLegend(mode);
      updateDelta();
    }
    writeUrl();

    if (
      options &&
      options.animate &&
      previous === "naive" &&
      mode === "corrected"
    ) {
      dissolveDropped();
    }
  }

  function updateCounts(demo) {
    el("count-naive").textContent = fmt(demo.n_naive, "px");
    el("count-corrected").textContent = fmt(demo.n_corrected, "px");
    el("count-naive").setAttribute(
      "aria-label",
      `Naive, ${fmt(demo.n_naive, "pixels")}`
    );
    el("count-corrected").setAttribute(
      "aria-label",
      `Corrected, ${fmt(demo.n_corrected, "pixels")}`
    );
  }

  function updateDelta() {
    const demo = state.demo;
    if (!demo) return;
    const removed = demo.n_naive - demo.n_corrected;
    const node = el("delta");
    let text;
    let status;

    if (state.mode === "corrected") {
      if (demo.n_corrected === 0) {
        text = "0 pixels flagged";
        status = "none";
      } else {
        text = `${fmt(
          demo.n_corrected,
          demo.n_corrected === 1 ? "pixel" : "pixels"
        )} flagged after correction`;
        status = "kept";
      }
    } else if (demo.n_naive === 0) {
      text = "0 pixels flagged";
      status = "none";
    } else if (removed > 0) {
      text = `${fmt(removed, removed === 1 ? "pixel" : "pixels")} dropped by the correction`;
      status = "dropped";
    } else if (removed < 0) {
      text = `${fmt(-removed, "pixels")} added by the correction`;
      status = "added";
    } else {
      text = "No pixels dropped";
      status = "kept";
    }

    node.textContent = text;
    node.dataset.state = status;
  }

  function updateLegend(mode) {
    const demo = state.demo;
    const units = demo ? demo.units || "" : "";
    const entry = state.rasters ? state.rasters[mode] : null;
    const legend = el("legend");

    el("legend-unit").textContent = units;
    el("legend-zero").textContent = fmt(0, units);

    if (!entry || entry.empty) {
      el("legend-min").textContent = "—";
      el("legend-max").textContent = "—";
      if (legend) legend.classList.add("is-empty");
    } else {
      el("legend-min").textContent = legendEndpoint(entry.min, units);
      el("legend-max").textContent = legendEndpoint(entry.max, units);
      if (legend) legend.classList.remove("is-empty");
    }

    const dashed = el("legend-key-dashed");
    if (dashed) dashed.hidden = mode !== "naive";
  }

  function legendEndpoint(slope, units) {
    // "Trend per decade" is already the legend's caption; repeating it at both
    // ends wraps the ramp and crowds the panel.
    const parts = trendParts(slope, units);
    if (!parts) return "—";
    return units ? `${parts.number} ${units}` : parts.number;
  }

  function updateFacts(demo) {
    el("facts").textContent = [
      `Cutoff p ≤ ${fmt(demo.threshold, "", { significant: 3 })}`,
      `Coverage ${fmt(demo.valid_fraction * 100, "%", { decimals: 0 })}`,
      `${fmt(demo.n_naive, "pixels")} → ${fmt(demo.n_corrected, "pixels")}`,
    ].join(" · ");
  }

  function updateMethod(demo) {
    const source = el("data-source");
    if (source) {
      source.textContent = demo.source
        ? `Data: ${demo.source}; monthly means 1980–2024.`
        : "";
    }
    el("method-cutoff").textContent =
      `p ≤ ${fmt(demo.threshold, "", { significant: 3 })}`;
    el("method-coverage").textContent = fmt(
      demo.valid_fraction * 100,
      "%",
      { decimals: 0 }
    );
    const morans = Number(demo.morans_i);
    el("method-morans").textContent =
      demo.morans_i === null || !Number.isFinite(morans)
        ? "n/a"
        : fmt(morans, "I", { decimals: 2 });
    el("method-grid").textContent =
      `${fmt(demo.lat.length, "rows")} × ` +
      `${fmt(demo.lon.length, "columns")}`;
  }

  // --- selection and readout ----------------------------------------------

  function renderReadout(cell) {
    const units = (state.demo && state.demo.units) || "";
    el("cell-empty").hidden = true;
    el("cell-detail").hidden = false;
    el("cell-coords").textContent = place(cell.lat, cell.lon);
    setTrendText(el("cell-trend"), cell.slope, units);
    el("cell-p").textContent =
      cell.p === null || cell.p === undefined || !isFinite(Number(cell.p))
        ? "p n/a"
        : `p ${fmt(cell.p, "", { significant: 3 })}`;

    const status = el("cell-status");
    status.textContent = CELL_STATUS[cell.status] || CELL_STATUS.none;
    status.dataset.status = cell.status;

    const naive = el("cell-naive");
    naive.textContent = cell.naive
      ? VERDICT_WORDS.naive.on
      : VERDICT_WORDS.naive.off;
    naive.classList.toggle("is-on", cell.naive);
    const corrected = el("cell-corrected");
    corrected.textContent = cell.corrected
      ? VERDICT_WORDS.corrected.on
      : VERDICT_WORDS.corrected.off;
    corrected.classList.toggle("is-on", cell.corrected);

    el("chart-hint").textContent = `${place(cell.lat, cell.lon)} · monthly`;
    el("fingerprint-toggle").disabled = false;
  }

  function drawSelection(cell) {
    if (!state.map || !state.demo) return;
    const bounds = TDTrend.cellBounds(cell.i, cell.j, state.demo.lat, state.demo.lon);
    if (state.cellLayer) state.map.removeLayer(state.cellLayer);
    const halo = L.rectangle(bounds, {
      pane: "selection",
      color: "#041416",
      weight: 3.4,
      opacity: 0.9,
      fill: false,
      interactive: false,
    });
    const stroke = L.rectangle(bounds, {
      pane: "selection",
      className: "cell-select",
      color: "#63E6BE",
      weight: 1.6,
      opacity: 1,
      fillColor: "#63E6BE",
      fillOpacity: 0.12,
      interactive: false,
    });
    state.cellLayer = L.layerGroup([halo, stroke]).addTo(state.map);
  }

  function selectCell(i, j, options) {
    if (!state.demo) return;
    const cell = TDTrend.describeCell(state.demo, i, j);
    cell.i = i;
    cell.j = j;
    state.selected = cell;
    drawSelection(cell);
    renderReadout(cell);
    loadSeries(cell);
    if (options && options.fly && state.map) {
      state.map.flyTo([cell.lat, cell.lon], Math.max(state.map.getZoom(), 3), {
        duration: 0.6,
      });
    } else if (options && options.pan && state.map) {
      state.map.panInside([cell.lat, cell.lon], { padding: [80, 80] });
    }
    hideLoupe();
    writeUrl();
    if (state.fingerprint.on) requestFingerprint(cell.lat, cell.lon);
  }

  function selectCellAt(lat, lon, options) {
    if (!state.demo) return;
    const cell = TDTrend.cellAt(
      lat,
      lon,
      state.demo.lat,
      state.demo.lon
    );
    selectCell(cell.i, cell.j, options);
  }

  function clearSelection() {
    state.seriesToken += 1;
    if (state.cellLayer && state.map) state.map.removeLayer(state.cellLayer);
    state.cellLayer = null;
    state.selected = null;
    el("cell-empty").hidden = false;
    el("cell-empty").textContent = "Click a cell to inspect it.";
    el("cell-detail").hidden = true;
    el("cell-trend").textContent = "";
    el("cell-p").textContent = "";
    el("cell-status").textContent = "";
    el("cell-naive").textContent = "";
    el("cell-corrected").textContent = "";
    el("cell-naive").classList.remove("is-on");
    el("cell-corrected").classList.remove("is-on");
    el("chart-hint").textContent = "Click the map to read a cell.";
    el("fingerprint-toggle").disabled = true;
    if (typeof Plotly !== "undefined" && el("chart").data) {
      Plotly.purge("chart");
    }
    el("chart").innerHTML = "";
    hideLoupe();
  }

  function defaultCell(demo) {
    // Prefer the centroid of the largest corrected patch: the biggest thing the
    // correction actually stands behind. A variable with no corrected patch
    // falls back to the largest naive patch, then the strongest finite slope,
    // then the grid centre, so the chart is never an empty box.
    const mask = demo.n_corrected > 0 ? demo.corrected_mask : demo.naive_mask;
    const ny = mask.length;
    const nx = ny ? mask[0].length : 0;
    const seen = mask.map((row) => row.map(() => false));
    let best = null;

    for (let i = 0; i < ny; i++) {
      for (let j = 0; j < nx; j++) {
        if (!mask[i][j] || seen[i][j]) continue;
        const stack = [[i, j]];
        seen[i][j] = true;
        const cells = [];
        while (stack.length) {
          const [r, c] = stack.pop();
          cells.push([r, c]);
          for (let dr = -1; dr <= 1; dr++) {
            for (let dc = -1; dc <= 1; dc++) {
              const rr = r + dr;
              const cc = c + dc;
              if (rr < 0 || cc < 0 || rr >= ny || cc >= nx) continue;
              if (!mask[rr][cc] || seen[rr][cc]) continue;
              seen[rr][cc] = true;
              stack.push([rr, cc]);
            }
          }
        }
        if (!best || cells.length > best.length) best = cells;
      }
    }

    if (best) {
      const meanI = best.reduce((sum, cell) => sum + cell[0], 0) / best.length;
      const meanJ = best.reduce((sum, cell) => sum + cell[1], 0) / best.length;
      let pick = best[0];
      let pickD = Infinity;
      best.forEach(([i, j]) => {
        const d = (i - meanI) ** 2 + (j - meanJ) ** 2;
        if (d < pickD) {
          pickD = d;
          pick = [i, j];
        }
      });
      return { i: pick[0], j: pick[1] };
    }

    let br = -1;
    let bc = -1;
    let bv = -Infinity;
    for (let i = 0; i < ny; i++) {
      for (let j = 0; j < nx; j++) {
        if (!mask[i][j]) continue;
        const value = demo.slope[i][j];
        if (value !== null && isFinite(value) && Math.abs(value) > bv) {
          bv = Math.abs(value);
          br = i;
          bc = j;
        }
      }
    }
    if (br >= 0) return { i: br, j: bc };

    const mi = Math.floor(ny / 2);
    const mj = Math.floor(nx / 2);
    return { i: mi, j: mj };
  }

  // --- timeseries and chart ------------------------------------------------

  async function loadSeries(cell) {
    const units = (state.demo && state.demo.units) || "";
    const trendMonthly =
      cell.slope === null || cell.slope === undefined
        ? null
        : Number(cell.slope);
    const token = ++state.seriesToken;
    try {
      const response = await fetch(
        `/timeseries/${state.variable}?lat=${cell.lat}&lon=${cell.lon}`
      );
      if (!response.ok) {
        throw new Error(`request failed (${response.status})`);
      }
      const body = await response.json();
      if (token !== state.seriesToken) return;
      drawChart(body, units, trendMonthly);
      el("chart").setAttribute(
        "aria-label",
        `Monthly series for ${place(cell.lat, cell.lon)} in ${units || "the variable's units"}`
      );
    } catch (error) {
      if (token !== state.seriesToken) return;
      el("chart-hint").textContent = `Could not load the series: ${error.message}.`;
    }
  }

  function drawChart(body, units, trendMonthly) {
    if (typeof Plotly === "undefined") {
      el("chart").textContent = "Chart library unavailable.";
      return;
    }
    const calendar = body.time_kind === "calendar";
    const hasTrend =
      trendMonthly !== null &&
      trendMonthly !== undefined &&
      isFinite(Number(trendMonthly));
    Plotly.react("chart",
      TDChart.traces(body, units, hasTrend ? Number(trendMonthly) : null),
      TDChart.layout(units, hasTrend, calendar),
      TDChart.CONFIG
    );
  }

  function initChartReset() {
    const reset = el("chart-reset");
    if (!reset) return;
    reset.addEventListener("click", () => {
      if (typeof Plotly === "undefined") return;
      const chart = el("chart");
      if (!chart || !chart.data) return;
      Plotly.relayout(chart, {
        "xaxis.autorange": true,
        "yaxis.autorange": true,
      });
    });
  }

  // --- fingerprints --------------------------------------------------------

  function clearFingerprintLayers() {
    if (!state.map) return;
    const group = state.overlays.fingerprint;
    if (group) state.map.removeLayer(group);
    state.overlays.fingerprint = null;
  }

  function nearestRegion(lat, lon) {
    const data = state.fingerprint.data;
    if (!data || !Array.isArray(data.regions) || !data.regions.length) {
      return null;
    }
    let best = null;
    let bestDistance = Infinity;
    data.regions.forEach((region) => {
      const dLat = region.lat - lat;
      const dLon = ((region.lon - lon + 540) % 360) - 180;
      const distance = dLat * dLat + dLon * dLon;
      if (distance < bestDistance) {
        bestDistance = distance;
        best = region;
      }
    });
    return best;
  }

  function vectorText(vector) {
    const parts = [];
    Object.entries(vector || {}).forEach(([name, value]) => {
      if (value === null || value === undefined || !isFinite(Number(value))) {
        return;
      }
      const variable = state.catalogByKey[name];
      const label = variable ? variable.label : name;
      const units = variable ? variable.units : "";
      parts.push(`${label}: ${trendLabel(value, units)}`);
    });
    return parts.join(" · ");
  }

  function drawFingerprintRegion(group, region, style, label, rank) {
    const tooltip = rank
      ? `Match ${rank}${vectorText(region.vector) ? ` · ${vectorText(region.vector)}` : ""}`
      : `Reference region${vectorText(region.vector) ? ` · ${vectorText(region.vector)}` : ""}`;

    (region.rings || []).forEach((ring) => {
      if (!Array.isArray(ring) || ring.length < 3) return;
      L.polygon(ring, {
        pane: "selection",
        className: `fp-region ${style.className}`,
        color: style.color,
        weight: style.weight,
        dashArray: style.dashArray,
        fill: false,
        bubblingMouseEvents: false,
      })
        .bindTooltip(tooltip, { sticky: true, className: "fp-tip" })
        .on("click", () => selectCellAt(region.lat, region.lon, { fly: true }))
        .addTo(group);
    });

    const marker = L.marker([region.lat, region.lon], {
      icon: L.divIcon({
        className: `fp-label ${style.className}`,
        html: label,
        iconSize: [22, 22],
      }),
      interactive: false,
      keyboard: false,
    });
    marker.addTo(group);
  }

  function renderFingerprintList(matches) {
    const list = el("fingerprint-list");
    if (!list) return;
    list.innerHTML = "";
    matches.forEach((match, index) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "fingerprint__chip";
      const rank = document.createElement("span");
      rank.className = "fingerprint__rank";
      rank.textContent = String(index + 1);
      const where = document.createElement("span");
      where.className = "fingerprint__where";
      where.textContent = place(match.lat, match.lon);
      button.appendChild(rank);
      button.appendChild(where);
      if (match.distance !== undefined && isFinite(Number(match.distance))) {
        const distance = document.createElement("span");
        distance.className = "fingerprint__distance";
        distance.textContent = `distance ${fmt(match.distance, "", {
          decimals: 2,
        })}`;
        button.appendChild(distance);
      }
      button.addEventListener("click", () => {
        selectCellAt(match.lat, match.lon, { fly: true });
      });
      item.appendChild(button);
      list.appendChild(item);
    });
  }

  function drawFingerprint(body) {
    clearFingerprintLayers();
    const reference = body.reference;
    const matches = body.matches || [];

    if (!reference && !matches.length) {
      renderFingerprintList([]);
      fingerprintStatus(
        body.reason || "No corrected regions to compare for this variable."
      );
      return;
    }

    const group = L.layerGroup();
    if (reference) {
      drawFingerprintRegion(
        group,
        reference,
        {
          color: "#F2F7F7",
          weight: 2,
          dashArray: "5 4",
          className: "fp-region--ref",
        },
        "R",
        0
      );
    }
    matches.forEach((match, index) => {
      drawFingerprintRegion(
        group,
        match,
        {
          color: "#63E6BE",
          weight: 1.6,
          dashArray: null,
          className: "fp-region--match",
        },
        String(index + 1),
        index + 1
      );
    });
    group.addTo(state.map);
    state.overlays.fingerprint = group;

    const kept = (body.kept || [])
      .map((key) => DRIVER_NAMES[key] || key)
      .join(", ");
    const summary =
      matches.length === 1 ? "region resembles" : "regions resemble";
    fingerprintStatus(
      `${fmt(matches.length, "")} ${summary} this cell.` +
        (kept ? ` Drivers: ${kept}.` : "")
    );
    renderFingerprintList(matches);
  }

  async function requestFingerprint(lat, lon) {
    if (!state.fingerprint.on || !state.variable) return;
    const token = ++state.fingerprint.token;
    clearTimeout(state.fingerprint.timer);
    state.fingerprint.timer = null;
    clearFingerprintLayers();
    state.fingerprint.data = null;
    renderFingerprintList([]);
    fingerprintStatus("Preparing…");

    try {
      const response = await fetch(
        `/fingerprint/${state.variable}?lat=${lat}&lon=${lon}&k=5`
      );
      if (!response.ok) {
        throw new Error(`request failed (${response.status})`);
      }
      const body = await response.json();
      if (token !== state.fingerprint.token || !state.fingerprint.on) return;
      state.fingerprint.data = body;

      if (body.status === "preparing") {
        const done = body.progress ? body.progress.done : 0;
        const total = body.progress ? body.progress.total : 0;
        fingerprintStatus(
          `Preparing… ${fmt(done, "")} of ${fmt(total, "variables")} analysed.`
        );
        state.fingerprint.timer = window.setTimeout(
          () => requestFingerprint(lat, lon),
          2500
        );
        return;
      }
      if (body.status === "unavailable") {
        fingerprintStatus(
          body.reason || "Fingerprints are unavailable for this variable."
        );
        return;
      }
      drawFingerprint(body);
    } catch (error) {
      if (token !== state.fingerprint.token) return;
      fingerprintStatus(`Could not prepare fingerprints: ${error.message}.`);
    }
  }

  function resetFingerprint() {
    state.fingerprint.on = false;
    state.fingerprint.data = null;
    state.fingerprint.token += 1;
    clearTimeout(state.fingerprint.timer);
    state.fingerprint.timer = null;
    clearFingerprintLayers();
    const button = el("fingerprint-toggle");
    if (button) {
      button.setAttribute("aria-pressed", "false");
      button.textContent = "Find similar regions";
      button.disabled = true;
    }
    const panel = el("fingerprint-panel");
    if (panel) panel.hidden = true;
    fingerprintStatus("");
    renderFingerprintList([]);
  }

  function initFingerprint() {
    const button = el("fingerprint-toggle");
    if (!button) return;
    button.addEventListener("click", () => {
      state.fingerprint.on = !state.fingerprint.on;
      button.setAttribute("aria-pressed", String(state.fingerprint.on));
      button.textContent = state.fingerprint.on
        ? "Hide matches"
        : "Find similar regions";
      const panel = el("fingerprint-panel");
      if (!state.fingerprint.on) {
        state.fingerprint.token += 1;
        clearTimeout(state.fingerprint.timer);
        state.fingerprint.timer = null;
        clearFingerprintLayers();
        state.fingerprint.data = null;
        renderFingerprintList([]);
        if (panel) panel.hidden = true;
        return;
      }
      if (panel) panel.hidden = false;
      if (state.selected) {
        requestFingerprint(state.selected.lat, state.selected.lon);
      }
    });
  }

  // --- stripes -------------------------------------------------------------

  function hexMix(a, b, t) {
    const parse = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    const from = parse(a);
    const to = parse(b);
    const mix = from.map((value, index) =>
      Math.round(value + (to[index] - value) * t)
    );
    return `rgb(${mix[0]}, ${mix[1]}, ${mix[2]})`;
  }

  function renderStripes(demo) {
    const band = el("stripe-band");
    if (!band) return;
    const values = (demo && demo.annual_anomaly) || [];
    const finite = values.filter((value) => value !== null && isFinite(value));
    band.innerHTML = "";

    if (!finite.length) {
      const rule = document.createElement("span");
      rule.className = "stripes__rule";
      band.appendChild(rule);
      band.title = "The annual anomaly band appears with the first analysis.";
      return;
    }

    const scale = Math.max(...finite.map((value) => Math.abs(value))) || 1;
    const COOL_NEAR = "#17323F";
    const COOL_FAR = "#8ED8FF";
    const WARM_NEAR = "#4A241C";
    const WARM_FAR = "#FF6B45";

    values.forEach((value) => {
      const cell = document.createElement("span");
      cell.className = "stripes__year";
      if (value === null || !isFinite(value)) {
        cell.style.background = "#1C2E33";
      } else {
        const t = Math.min(1, Math.abs(value) / scale);
        cell.style.background =
          value >= 0
            ? hexMix(WARM_NEAR, WARM_FAR, t)
            : hexMix(COOL_NEAR, COOL_FAR, t);
      }
      band.appendChild(cell);
    });
    band.title =
      "Global annual mean anomaly for the selected variable: oldest year at " +
      "the left. Warm colours are above the record mean, cool colours below.";
  }

  // --- catalog, load, error -----------------------------------------------

  function setBusy(on, message) {
    const busy = el("busy");
    if (!busy) return;
    busy.hidden = !on;
    if (message) el("busy-line").textContent = message;
    el("busy-note").hidden = true;
    clearTimeout(state.busyTimer);
    if (on) {
      // A warm cache answers in under a second. Only a genuinely slow request
      // gets the cold-run explanation, so it never feels like a wall of text.
      state.busyTimer = window.setTimeout(() => {
        if (el("busy") && !el("busy").hidden) el("busy-note").hidden = false;
      }, 5000);
    }
    const control = el("variable");
    if (control) {
      control.setAttribute("aria-busy", on ? "true" : "false");
      control.querySelectorAll("button").forEach((button) => {
        button.disabled = on;
      });
    }
  }

  function renderError(message) {
    setBusy(false);
    clearRasterLayers();
    clearSelection();
    state.demo = null;
    const ambient = el("stage-ambient");
    if (ambient) ambient.style.backgroundImage = "none";
    const delta = el("delta");
    delta.textContent = "No result.";
    delta.dataset.state = "error";
    el("facts").textContent = message;
    el("legend").classList.add("is-empty");
    el("cell-empty").hidden = false;
    el("cell-empty").textContent = "The field did not load.";
  }

  function renderDemo(demo, initialCell) {
    state.demo = demo;
    state.bounds = gridBounds(demo);
    state.rasters = buildRasterSet(demo);

    clearRasterLayers();
    addRasterOverlays();

    updateCounts(demo);
    updateFacts(demo);
    updateMethod(demo);
    updateLegend(state.mode);
    updateDelta();
    renderStripes(demo);

    if (state.map) {
      state.map.fitBounds(state.bounds, { padding: [0, 0], animate: false });
    }
    drawFieldBorder();
    updateAmbient();

    const asked =
      initialCell &&
      initialCell.lat !== null &&
      initialCell.lon !== null &&
      isFinite(initialCell.lat) &&
      isFinite(initialCell.lon)
        ? TDTrend.cellAt(
            initialCell.lat,
            initialCell.lon,
            demo.lat,
            demo.lon
          )
        : null;
    const start = asked || defaultCell(demo);
    selectCell(start.i, start.j, { pan: false });

    const stage = el("stage");
    if (stage) {
      stage.classList.remove("is-live");
      void stage.offsetWidth;
      stage.classList.add("is-live");
    }
  }

  async function loadDemo(variable, initialCell) {
    const token = ++state.demoToken;
    setBusy(true, "Loading the field…");
    try {
      const response = await fetch(`/demo/${variable}`);
      if (!response.ok) {
        throw new Error(`request failed (${response.status})`);
      }
      const body = await response.json();
      if (token !== state.demoToken) return;
      renderDemo(body, initialCell);
    } catch (error) {
      if (token !== state.demoToken) return;
      renderError(`Could not load the analysis: ${error.message}.`);
    } finally {
      if (token === state.demoToken) setBusy(false);
    }
  }

  function selectVariable(key, button, options) {
    state.variable = key;
    resetFingerprint();
    clearSelection();
    clearRasterLayers();
    state.rasters = null;
    const ambient = el("stage-ambient");
    if (ambient) ambient.style.backgroundImage = "none";

    document.querySelectorAll(".variable__btn").forEach((node) => {
      node.setAttribute("aria-checked", "false");
    });
    if (button) button.setAttribute("aria-checked", "true");

    const variable = state.catalogByKey[key];
    if (variable) {
      el("stage-name").textContent = variable.label;
      el("stage-source").textContent = variable.source;
    }
    writeUrl();
    loadDemo(key, options && options.cell);
  }

  async function loadCatalog() {
    const container = el("variable");
    try {
      const response = await fetch("/catalog");
      if (!response.ok) {
        throw new Error(`request failed (${response.status})`);
      }
      const list = await response.json();
      state.catalogByKey = {};
      container.innerHTML = "";
      const buttons = {};
      list.forEach((variable) => {
        state.catalogByKey[variable.key] = variable;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "variable__btn";
        button.setAttribute("role", "radio");
        button.setAttribute("aria-checked", "false");
        button.setAttribute("aria-label", variable.label);
        button.title = `${variable.label} (${variable.units})`;
        const code = document.createElement("span");
        code.className = "variable__code";
        code.textContent = SHORT_NAMES[variable.key] || variable.label;
        const unit = document.createElement("span");
        unit.className = "variable__unit";
        unit.textContent = variable.units;
        button.appendChild(code);
        button.appendChild(unit);
        button.addEventListener("click", () => {
          selectVariable(variable.key, button);
        });
        container.appendChild(button);
        buttons[variable.key] = button;
      });

      const wanted = state.initial && state.initial.variable;
      const start = (wanted && list.find((v) => v.key === wanted)) || list[0];
      if (!start) {
        renderError("No variables are available from this source.");
        return;
      }
      const initialCell =
        state.initial &&
        state.initial.lat !== null &&
        state.initial.lon !== null &&
        (!wanted || wanted === start.key)
          ? { lat: state.initial.lat, lon: state.initial.lon }
          : null;
      state.initial = null;
      selectVariable(start.key, buttons[start.key], { cell: initialCell });
      container.setAttribute("aria-busy", "false");
    } catch (error) {
      container.setAttribute("aria-busy", "false");
      container.innerHTML = "";
      const message = document.createElement("p");
      message.className = "variable__error";
      message.textContent = `Catalog failed: ${error.message}`;
      container.appendChild(message);
      renderError("The catalog did not load.");
    }
  }

  // --- URL state -----------------------------------------------------------

  function writeUrl() {
    if (!state.variable) return;
    const params = new URLSearchParams();
    params.set("variable", state.variable);
    params.set("mode", state.mode);
    if (state.selected) {
      params.set("lat", state.selected.lat.toFixed(2));
      params.set("lon", state.selected.lon.toFixed(2));
    }
    history.replaceState(null, "", `${location.pathname}?${params.toString()}`);
  }

  function readUrl() {
    const params = new URLSearchParams(location.search);
    const rawLat = params.get("lat");
    const rawLon = params.get("lon");
    const lat = rawLat === null ? null : Number(rawLat);
    const lon = rawLon === null ? null : Number(rawLon);
    return {
      variable: params.get("variable") || null,
      lat: lat !== null && Number.isFinite(lat) ? lat : null,
      lon: lon !== null && Number.isFinite(lon) ? lon : null,
      mode: params.get("mode") === "corrected" ? "corrected" : "naive",
    };
  }

  // --- keyboard ------------------------------------------------------------

  function initModeKeys() {
    const group = el("mode");
    if (!group) return;
    group.addEventListener("keydown", (event) => {
      if (
        event.key !== "ArrowLeft" &&
        event.key !== "ArrowRight" &&
        event.key !== "ArrowUp" &&
        event.key !== "ArrowDown"
      ) {
        return;
      }
      event.preventDefault();
      const next = state.mode === "naive" ? "corrected" : "naive";
      setMode(next, { animate: true });
      const button = el(`mode-${next}`);
      if (button) button.focus();
    });
    el("mode-naive").addEventListener("click", () =>
      setMode("naive", { animate: true })
    );
    el("mode-corrected").addEventListener("click", () =>
      setMode("corrected", { animate: true })
    );
  }

  const MOVES = {
    ArrowUp: [-1, 0],
    ArrowDown: [1, 0],
    ArrowLeft: [0, -1],
    ArrowRight: [0, 1],
  };

  function initMapKeys() {
    const node = el("map");
    if (!node) return;
    node.addEventListener("keydown", (event) => {
      if (!state.demo || !state.map) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        event.stopPropagation();
        const centre = state.map.getCenter();
        selectCellAt(centre.lat, centre.lng, { pan: false });
        return;
      }
      if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        state.map.zoomIn();
        return;
      }
      if (event.key === "-" || event.key === "_") {
        event.preventDefault();
        state.map.zoomOut();
        return;
      }
      const move = MOVES[event.key];
      if (!move) return;
      event.preventDefault();
      event.stopPropagation();
      const demo = state.demo;
      const base =
        state.selected ||
        TDTrend.cellAt(
          state.map.getCenter().lat,
          state.map.getCenter().lng,
          demo.lat,
          demo.lon
        );
      const i = Math.min(
        demo.lat.length - 1,
        Math.max(0, base.i + move[0])
      );
      const j =
        (base.j + move[1] + demo.lon.length) % demo.lon.length;
      selectCell(i, j, { pan: true });
    });
  }

  // --- method dialog -------------------------------------------------------

  function initMethod() {
    const dialog = el("method");
    const open = el("method-open");
    if (!dialog || !open) return;
    open.addEventListener("click", () => {
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  }

  // --- boot ----------------------------------------------------------------

  function init() {
    if (
      typeof L === "undefined" ||
      typeof TDTrend === "undefined" ||
      typeof TDChart === "undefined" ||
      typeof TDProjection === "undefined" ||
      typeof TDFormat === "undefined"
    ) {
      renderError("A page library did not load.");
      return;
    }
    state.initial = readUrl();
    state.mode = state.initial.mode;
    const map = makeMap();
    drawGraticule(map);
    initModeKeys();
    initChartReset();
    initFingerprint();
    initMethod();
    initMapKeys();
    setMode(state.mode, { animate: false });
    loadCatalog();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
