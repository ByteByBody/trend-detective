/* Trend Detective — frontend.
   Fetches the catalog + a per-variable cry-wolf result, draws the naive and
   corrected maps over a world basemap, and charts a clicked cell's history. */

(() => {
  "use strict";

  const DEFAULT_BOUNDS = [[-90, -180], [90, 180]];
  // NASA GIBS Blue Marble: keyless, and the imagery matches the subject.
  // CARTO's public basemaps now return a 2 KB "API KEY REQUIRED" watermark.
  const TILE_URL =
    "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/" +
    "BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/" +
    "{z}/{y}/{x}.jpeg";
  const TILE_ATTR =
    'Imagery &copy; <a href="https://earthdata.nasa.gov/gibs">NASA EOSDIS GIBS</a>';
  const TILE_MAX_NATIVE_ZOOM = 8;

  // Flagged cells are tinted, not painted over: the basemap has to stay
  // legible underneath, otherwise the panel reads as a colour block.
  const MASK_ALPHA = 165;

  // Viridis stops for the slope ramp.
  const VIRIDIS = [
    [0.0, [68, 1, 84]],
    [0.25, [59, 82, 139]],
    [0.5, [33, 145, 140]],
    [0.75, [94, 201, 98]],
    [1.0, [253, 231, 37]],
  ];

  const state = {
    variable: null,
    demo: null,
    bounds: DEFAULT_BOUNDS,
    maps: { naive: null, corrected: null },
    overlays: { naive: null, corrected: null, marker: null },
    colors: { naive: "#d6453d", corrected: "#0f7a6b" },
  };

  const el = (id) => document.getElementById(id);

  function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function viridis(t) {
    t = Math.min(1, Math.max(0, t));
    for (let i = 1; i < VIRIDIS.length; i++) {
      const [t1, c1] = VIRIDIS[i];
      const [t0, c0] = VIRIDIS[i - 1];
      if (t <= t1) {
        const f = (t - t0) / (t1 - t0 || 1);
        return [
          Math.round(c0[0] + f * (c1[0] - c0[0])),
          Math.round(c0[1] + f * (c1[1] - c0[1])),
          Math.round(c0[2] + f * (c1[2] - c0[2])),
        ];
      }
    }
    return VIRIDIS[VIRIDIS.length - 1][1];
  }

  // --- image builders -------------------------------------------------------

  function maskToDataURL(mask, hex) {
    const ny = mask.length;
    const nx = mask[0].length;
    const [r, g, b] = hexToRgb(hex);
    const canvas = document.createElement("canvas");
    canvas.width = nx;
    canvas.height = ny;
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(nx, ny);
    for (let i = 0; i < ny; i++) {
      for (let j = 0; j < nx; j++) {
        const k = (i * nx + j) * 4;
        if (mask[i][j]) {
          img.data[k] = r;
          img.data[k + 1] = g;
          img.data[k + 2] = b;
          img.data[k + 3] = MASK_ALPHA;
        }
      }
    }
    ctx.putImageData(img, 0, 0);
    return canvas.toDataURL();
  }

  function slopeToDataURL(slope) {
    const ny = slope.length;
    const nx = slope[0].length;
    let min = Infinity;
    let max = -Infinity;
    for (let i = 0; i < ny; i++) {
      for (let j = 0; j < nx; j++) {
        const v = slope[i][j];
        if (v === null || !isFinite(v)) continue;
        if (v < min) min = v;
        if (v > max) max = v;
      }
    }
    if (!isFinite(min) || max === min) {
      min = 0;
      max = 1;
    }
    const span = max - min || 1;
    const canvas = document.createElement("canvas");
    canvas.width = nx;
    canvas.height = ny;
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(nx, ny);
    for (let i = 0; i < ny; i++) {
      for (let j = 0; j < nx; j++) {
        const v = slope[i][j];
        const k = (i * nx + j) * 4;
        if (v === null || !isFinite(v)) continue;
        const [r, g, b] = viridis((v - min) / span);
        img.data[k] = r;
        img.data[k + 1] = g;
        img.data[k + 2] = b;
        img.data[k + 3] = 225;
      }
    }
    ctx.putImageData(img, 0, 0);
    return { url: canvas.toDataURL(), min, max };
  }

  // --- maps -----------------------------------------------------------------

  function makeMap(id) {
    const map = L.map(id, {
      center: [12, 0],
      zoom: 1,
      minZoom: 0,
      maxZoom: 12,
      // Whole-number zoom rounds a fitted view outward and leaves a margin
      // around the raster. Fractional zoom makes the world fill the panel.
      zoomSnap: 0,
      worldCopyJump: false,
      attributionControl: true,
      zoomControl: true,
    });
    L.tileLayer(TILE_URL, {
      attribution: TILE_ATTR,
      noWrap: true,
      maxNativeZoom: TILE_MAX_NATIVE_ZOOM,
    }).addTo(map);
    map.setMaxBounds(DEFAULT_BOUNDS);
    return map;
  }

  function drawOverlay(key, dataUrl, opacity) {
    const map = state.maps[key];
    if (!map) return;
    if (state.overlays[key]) map.removeLayer(state.overlays[key]);
    state.overlays[key] = L.imageOverlay(dataUrl, state.bounds, {
      opacity: opacity,
      interactive: false,
    }).addTo(map);
  }

  function drawSlopeOverlays(shapeUrl) {
    // Slope sits faintly beneath both masks so pattern and magnitude are visible.
    ["naive", "corrected"].forEach((key) => {
      const map = state.maps[key];
      if (state.overlays[key + "Slope"]) map.removeLayer(state.overlays[key + "Slope"]);
      state.overlays[key + "Slope"] = L.imageOverlay(shapeUrl, state.bounds, {
        opacity: 0.55,
        interactive: false,
      }).addTo(map);
      if (state.overlays[key]) state.overlays[key].bringToFront();
    });
  }

  function gridBounds(demo) {
    // Cell-edge extent: half a cell beyond the outermost cell centres, so the
    // raster lines up with the basemap instead of being scaled to the poles.
    const lat = demo.lat;
    const lon = demo.lon;
    const dLat = lat.length > 1 ? Math.abs(lat[0] - lat[1]) / 2 : 0;
    const dLon = lon.length > 1 ? Math.abs(lon[1] - lon[0]) / 2 : 0;
    const minLat = Math.max(-90, Math.min(...lat) - dLat);
    const maxLat = Math.min(90, Math.max(...lat) + dLat);
    const minLon = Math.max(-180, Math.min(...lon) - dLon);
    const maxLon = Math.min(180, Math.max(...lon) + dLon);
    return [[minLat, minLon], [maxLat, maxLon]];
  }

  // --- rendering ------------------------------------------------------------

  function fmtInt(n) {
    return Number(n).toLocaleString("en-US");
  }

  function renderDemo(demo) {
    state.demo = demo;
    state.bounds = gridBounds(demo);
    Object.values(state.maps).forEach((map) => {
      // Fit the view to the data, but leave the world reachable: a regional
      // source covers a box, and clamping the map to it hides everything else.
      map.fitBounds(state.bounds, { padding: [0, 0] });
    });

    const slopeImg = slopeToDataURL(demo.slope);
    drawSlopeOverlays(slopeImg.url);
    drawOverlay("naive", maskToDataURL(demo.naive_mask, state.colors.naive), 0.95);
    drawOverlay("corrected", maskToDataURL(demo.corrected_mask, state.colors.corrected), 0.95);

    el("tally-naive").textContent = `${fmtInt(demo.n_naive)} pixels flagged`;
    el("tally-corrected").textContent = `${fmtInt(demo.n_corrected)} pixels flagged`;

    const removed = demo.n_naive - demo.n_corrected;
    el("verdict-line").textContent =
      removed >= 0
        ? `Cried wolf ${fmtInt(removed)} ${removed === 1 ? "time" : "times"}.`
        : `Kept ${fmtInt(-removed)} more pixels.`;
    el("verdict-note").textContent =
      "The corrected test keeps only connected patches, so isolated pixels vanish.";

    el("stat-naive").textContent = fmtInt(demo.n_naive);
    el("stat-corrected").textContent = fmtInt(demo.n_corrected);
    el("stat-removed").textContent = `${removed >= 0 ? "-" : "+"}${fmtInt(Math.abs(removed))}`;
    el("stat-kept").textContent = fmtInt(demo.n_corrected);
    el("stat-threshold").textContent = demo.threshold.toFixed(4);
    el("stat-insufficient").textContent = fmtInt(demo.n_insufficient);
    el("stat-morans").textContent =
      demo.morans_i === null || !Number.isFinite(Number(demo.morans_i))
        ? "n/a"
        : Number(demo.morans_i).toFixed(2);
    el("stat-valid").textContent = `${Math.round(demo.valid_fraction * 100)}%`;

    const unit = demo.units ? ` ${demo.units}/mo` : " per month";
    el("slope-min").textContent = slopeImg.min.toExponential(1);
    el("slope-max").textContent = slopeImg.max.toExponential(1) + unit;

    const source = el("data-source");
    if (source) {
      source.textContent = demo.source
        ? `Data: ${demo.source}, monthly means over 1980–2024, 5° grid.`
        : "";
    }
  }

  function renderError(message) {
    el("verdict-line").textContent = "No result.";
    el("verdict-note").textContent = message;
    el("tally-naive").textContent = "—";
    el("tally-corrected").textContent = "—";
  }

  // --- timeseries -----------------------------------------------------------

  function nearest(lat, lon) {
    const demo = state.demo;
    let bi = 0;
    let bj = 0;
    let best = Infinity;
    for (let i = 0; i < demo.lat.length; i++) {
      for (let j = 0; j < demo.lon.length; j++) {
        const d = (demo.lat[i] - lat) ** 2 + (demo.lon[j] - lon) ** 2;
        if (d < best) {
          best = d;
          bi = i;
          bj = j;
        }
      }
    }
    return { lat: demo.lat[bi], lon: demo.lon[bj] };
  }

  async function loadSeries(lat, lon) {
    const cell = nearest(lat, lon);
    // Drop a marker on both maps at the chosen cell.
    ["naive", "corrected"].forEach((key) => {
      const map = state.maps[key];
      if (!map) return;
      const marker = L.circleMarker([cell.lat, cell.lon], {
        radius: 5,
        color: "#131a2a",
        weight: 2,
        fillColor: "#fff",
        fillOpacity: 1,
      }).addTo(map);
      if (state.overlays[key + "Marker"]) map.removeLayer(state.overlays[key + "Marker"]);
      state.overlays[key + "Marker"] = marker;
    });

    el("series-hint").textContent =
      `Cell ${cell.lat.toFixed(1)}°, ${cell.lon.toFixed(1)}°`;

    try {
      const r = await fetch(
        `/timeseries/${state.variable}?lat=${cell.lat}&lon=${cell.lon}`
      );
      if (!r.ok) throw new Error(`request failed (${r.status})`);
      const body = await r.json();
      drawChart(body);
    } catch (err) {
      el("series-hint").textContent = `Could not load the series: ${err.message}.`;
    }
  }

  function drawChart(body) {
    if (typeof Plotly === "undefined") {
      el("chart").textContent = "Chart library unavailable.";
      return;
    }
    const calendar = body.time_kind === "calendar";
    const trace = {
      x: body.time_labels || body.time,
      y: body.value,
      mode: "lines",
      line: { color: "#0b3d91", width: 1.6 },
      hovertemplate: calendar
        ? "%{x|%b %Y}<br>%{y:.3f}<extra></extra>"
        : "month %{x}<br>%{y:.3f}<extra></extra>",
    };
    const layout = {
      margin: { l: 46, r: 12, t: 10, b: 36 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: "Space Grotesk, system-ui, sans-serif", size: 11, color: "#131a2a" },
      xaxis: {
        title: { text: calendar ? "date" : "month index" },
        type: calendar ? "date" : "-",
        gridcolor: "#e4e8ee",
        zeroline: false,
      },
      yaxis: { title: { text: "value" }, gridcolor: "#e4e8ee", zeroline: false },
    };
    Plotly.react("chart", [trace], layout, { displayModeBar: false, responsive: true });
  }

  // --- catalog --------------------------------------------------------------

  function selectVariable(key, button) {
    state.variable = key;
    document.querySelectorAll(".seg").forEach((b) => b.setAttribute("aria-checked", "false"));
    if (button) button.setAttribute("aria-checked", "true");
    const v = state.catalogByKey[key];
    el("variable-note").textContent = v
      ? `${v.label} — ${v.description} · Source: ${v.source}`
      : "";
    loadDemo(key);
  }

  async function loadCatalog() {
    const container = el("variable");
    try {
      const r = await fetch("/catalog");
      if (!r.ok) throw new Error(`request failed (${r.status})`);
      const list = await r.json();
      state.catalogByKey = {};
      container.innerHTML = "";
      list.forEach((v, idx) => {
        state.catalogByKey[v.key] = v;
        const b = document.createElement("button");
        b.type = "button";
        b.className = "seg";
        b.setAttribute("role", "radio");
        b.setAttribute("aria-checked", "false");
        b.textContent = v.label;
        b.addEventListener("click", () => selectVariable(v.key, b));
        container.appendChild(b);
        if (idx === 0) selectVariable(v.key, b);
      });
      container.setAttribute("aria-busy", "false");
    } catch (err) {
      container.setAttribute("aria-busy", "false");
      el("variable-note").textContent = `Could not load the catalog: ${err.message}.`;
      renderError("The catalog did not load.");
    }
  }

  async function loadDemo(variable) {
    el("verdict-line").textContent = "Reading the field…";
    el("verdict-note").textContent = "\u00a0";
    try {
      const r = await fetch(`/demo/${variable}`);
      if (!r.ok) throw new Error(`request failed (${r.status})`);
      renderDemo(await r.json());
    } catch (err) {
      renderError(`Could not load the analysis: ${err.message}.`);
    }
  }

  function init() {
    state.maps.naive = makeMap("map-naive");
    state.maps.corrected = makeMap("map-corrected");
    ["naive", "corrected"].forEach((key) => {
      const map = state.maps[key];
      map.on("click", (e) => loadSeries(e.latlng.lat, e.latlng.lng));
      // Keyboard path for the same action: read the cell at the map centre.
      const node = el("map-" + key);
      if (node) {
        node.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            const c = map.getCenter();
            loadSeries(c.lat, c.lng);
          }
        });
      }
    });
    loadCatalog();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
