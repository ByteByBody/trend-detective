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

  // Only the contested pixels get a dot: the claims the corrected test drops.
  const CONTESTED_RADIUS = 4.5;
  // The slope raster sits beneath the dots as a faint wash of magnitude.
  const SLOPE_OPACITY = 0.22;

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
    renderers: { naive: null, corrected: null },
    // The mask each panel will snap a click to; null means no dots on it.
    dots: { naive: null, corrected: null },
    colors: { naive: "#d6453d", corrected: "#0f7a6b" },
    // The cell currently charted, so the URL can carry it across a reload.
    selected: null,
    // Variable and cell asked for by the URL, applied once the catalog lands.
    initial: null,
  };

  const el = (id) => document.getElementById(id);

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

  function contestedMask(demo) {
    // The pixels the raw test flags and the corrected test drops: the claims
    // actually being argued about, and the only ones worth marking.
    return demo.naive_mask.map((row, i) =>
      row.map((on, j) => Boolean(on) && !demo.corrected_mask[i][j])
    );
  }

  function clearDots(key) {
    const map = state.maps[key];
    if (map && state.overlays[key + "Dots"]) {
      map.removeLayer(state.overlays[key + "Dots"]);
    }
    state.overlays[key + "Dots"] = null;
    state.dots[key] = null;
  }

  function drawDots(key, demo, mask, hex) {
    const map = state.maps[key];
    if (!map) return;
    clearDots(key);
    const renderer = state.renderers[key] || (state.renderers[key] = L.canvas({ padding: 0.4 }));
    const group = L.layerGroup();
    demo.lat.forEach((lat, i) => {
      demo.lon.forEach((lon, j) => {
        if (!mask[i][j]) return;
        const dot = L.circleMarker([lat, lon], {
          renderer: renderer,
          radius: CONTESTED_RADIUS,
          weight: 0,
          fillColor: hex,
          fillOpacity: 0.95,
          bubblingMouseEvents: false,
        });
        dot.on("click", () => loadSeries(lat, lon));
        // A dot is a claim under dispute, so it answers on hover with the
        // numbers behind the verdict: where it is, the trend per decade, and
        // the p-value the field-wide cutoff rejected.
        const slope = demo.slope[i][j];
        const p = demo.p_value ? demo.p_value[i][j] : null;
        const slopeText =
          slope === null || slope === undefined || !isFinite(slope)
            ? "trend n/a"
            : `trend ${fmt(slope * MONTHS_PER_DECADE,
                demo.units ? `${demo.units} per decade` : "per decade",
                { decimals: 3 })}`;
        dot.bindTooltip(
          `<strong>lat ${fmt(lat, "°", { decimals: 1 })}, ` +
            `lon ${fmt(lon, "°", { decimals: 1 })}</strong><br>` +
            `${slopeText}<br>` +
            `p-value ${fmt(p, "", { decimals: 4 })}<br>` +
            "dropped: the field-wide test will not stand behind it",
          { direction: "top", sticky: true, className: "dot-tip" }
        );
        group.addLayer(dot);
      });
    });
    group.addTo(map);
    state.overlays[key + "Dots"] = group;
    state.dots[key] = mask;
  }

  function drawSlopeOverlays(shapeUrl) {
    // Slope sits faintly beneath the dots so magnitude is still visible.
    ["naive", "corrected"].forEach((key) => {
      const map = state.maps[key];
      if (state.overlays[key + "Slope"]) map.removeLayer(state.overlays[key + "Slope"]);
      state.overlays[key + "Slope"] = L.imageOverlay(shapeUrl, state.bounds, {
        opacity: SLOPE_OPACITY,
        interactive: false,
      }).addTo(map);
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

  function defaultCell(demo) {
    // Prefer the centroid of the largest corrected patch: the biggest thing the
    // correction actually stands behind. Fall back to the strongest corrected
    // slope, then the grid centre, so the chart is never an empty box on load.
    const mask = demo.corrected_mask;
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
      const r = Math.round(best.reduce((s, p) => s + p[0], 0) / best.length);
      const c = Math.round(best.reduce((s, p) => s + p[1], 0) / best.length);
      return { lat: demo.lat[r], lon: demo.lon[c] };
    }

    let br = -1;
    let bc = -1;
    let bv = -Infinity;
    for (let i = 0; i < ny; i++) {
      for (let j = 0; j < nx; j++) {
        if (!mask[i][j]) continue;
        const v = demo.slope[i][j];
        if (v !== null && isFinite(v) && Math.abs(v) > bv) {
          bv = Math.abs(v);
          br = i;
          bc = j;
        }
      }
    }
    if (br >= 0) return { lat: demo.lat[br], lon: demo.lon[bc] };

    const mi = Math.floor(ny / 2);
    const mj = Math.floor(nx / 2);
    return { lat: demo.lat[mi] || 0, lon: demo.lon[mj] || 0 };
  }

  // --- rendering ------------------------------------------------------------

  const fmt = (value, unit, opts) => TDFormat.formatNumber(value, unit, opts);
  const MONTHS_PER_DECADE = TDFormat.MONTHS_PER_DECADE;

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
    // Dots mark only the contested claims, and only on the panel that makes
    // them. The corrected panel has none left to argue about.
    drawDots("naive", demo, contestedMask(demo), state.colors.naive);
    clearDots("corrected");

    const units = demo.units || "";
    const removed = demo.n_naive - demo.n_corrected;

    // The naive panel draws one dot per contested pixel, so its tally has to
    // read as "these of those", not as the raw flag count.
    el("tally-naive").textContent =
      `${fmt(removed, "pixels")} of ${fmt(demo.n_naive, "pixels")} marked`;
    el("tally-corrected").textContent = `${fmt(demo.n_corrected, "pixels")} flagged`;

    el("verdict-line").textContent =
      removed >= 0
        ? `Cried wolf ${fmt(removed, removed === 1 ? "time" : "times")}.`
        : `Kept ${fmt(-removed, "pixels")} more.`;
    el("verdict-note").textContent =
      "A false alarm is a pixel the raw per-pixel test calls a trend and the " +
      "corrected test will not stand behind: it fails the field-wide cutoff " +
      `(p \u2264 ${fmt(demo.threshold, "p", { decimals: 4 })}) or sits alone, ` +
      "outside any patch of five.";

    el("stat-naive").textContent = fmt(demo.n_naive, "pixels");
    el("stat-corrected").textContent = fmt(demo.n_corrected, "pixels");
    el("stat-removed").textContent = fmt(-removed, "pixels");
    el("stat-kept").textContent = fmt(demo.n_corrected, "pixels");
    el("stat-threshold").textContent = fmt(demo.threshold, "p", { decimals: 4 });
    el("stat-insufficient").textContent = fmt(demo.n_insufficient, "pixels");
    el("stat-morans").textContent =
      demo.morans_i === null || !Number.isFinite(Number(demo.morans_i))
        ? "n/a"
        : fmt(Number(demo.morans_i), "I", { decimals: 2 });
    el("stat-valid").textContent = fmt(demo.valid_fraction * 100, "%", {
      decimals: 0,
    });

    // The legend shows the slope the climate way: per decade, not per month.
    const perDecade = (v) =>
      fmt(v * MONTHS_PER_DECADE, units ? `${units} per decade` : "per decade", {
        decimals: 3,
      });
    el("slope-min").textContent = perDecade(slopeImg.min);
    el("slope-max").textContent = perDecade(slopeImg.max);

    const source = el("data-source");
    if (source) {
      source.textContent = demo.source
        ? `Data: ${demo.source}, monthly means over 1980–2024, 5° grid.`
        : "";
    }

    // Seed the chart with the biggest thing the correction stands behind, so
    // the panel is never an empty box on first load — unless the URL already
    // named the cell to inspect.
    const asked = state.initial;
    state.initial = null;
    const cell =
      asked && asked.lat !== null && asked.lon !== null
        ? { lat: asked.lat, lon: asked.lon }
        : defaultCell(demo);
    loadSeries(cell.lat, cell.lon);
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
    return { lat: demo.lat[bi], lon: demo.lon[bj], i: bi, j: bj };
  }

  function nearestDot(key, latlng) {
    // The closest *flagged* cell to the cursor, so a click between dots still
    // selects a dot rather than an unflagged cell under the pointer.
    const demo = state.demo;
    const mask = state.dots[key];
    if (!demo || !mask) return null;
    const scale = Math.cos((latlng.lat * Math.PI) / 180);
    let best = null;
    let bestD = Infinity;
    demo.lat.forEach((lat, i) => {
      demo.lon.forEach((lon, j) => {
        if (!mask[i][j]) return;
        const dLat = lat - latlng.lat;
        const dLon = (lon - latlng.lng) * scale;
        const d = dLat * dLat + dLon * dLon;
        if (d < bestD) {
          bestD = d;
          best = { lat: lat, lon: lon };
        }
      });
    });
    return best;
  }

  async function loadSeries(lat, lon) {
    const cell = nearest(lat, lon);
    state.selected = { lat: cell.lat, lon: cell.lon };
    writeUrl();
    const trendMonthly = state.demo ? state.demo.slope[cell.i][cell.j] : null;
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
      drawChart(body, (state.demo && state.demo.units) || "", trendMonthly);
    } catch (err) {
      el("series-hint").textContent = `Could not load the series: ${err.message}.`;
    }
  }

  function drawChart(body, units, trendMonthly) {
    if (typeof Plotly === "undefined") {
      el("chart").textContent = "Chart library unavailable.";
      return;
    }
    const calendar = body.time_kind === "calendar";
    const xs = body.time_labels || body.time;
    const series = {
      x: xs,
      y: body.value,
      mode: "lines",
      name: "monthly",
      line: { color: "#1B3A6B", width: 1.6 },
      hovertemplate: calendar
        ? "%{x|%b %Y}<br>%{y:.3f}<extra></extra>"
        : "month %{x}<br>%{y:.3f}<extra></extra>",
    };

    const traces = [series];
    let trendText = "";
    if (trendMonthly !== null && isFinite(trendMonthly)) {
      // Theil-Sen line: the fitted monthly slope from the analysis, anchored on
      // the series mean, so the statistic is visible and not just quoted.
      const values = body.value;
      const n = values.length;
      let sum = 0;
      let count = 0;
      values.forEach((v) => {
        if (v !== null && isFinite(v)) {
          sum += v;
          count += 1;
        }
      });
      const meanY = count ? sum / count : 0;
      const intercept = meanY - trendMonthly * ((n - 1) / 2);
      traces.push({
        x: xs,
        y: values.map((_, k) => intercept + trendMonthly * k),
        mode: "lines",
        name: "trend",
        line: { color: "#D33B30", width: 1.4, dash: "dash" },
        hoverinfo: "skip",
      });
      const unit = units ? `${units} per decade` : "per decade";
      trendText = `Trend ${fmt(trendMonthly * MONTHS_PER_DECADE, unit, {
        decimals: 3,
      })}`;
    }

    const layout = {
      margin: { l: 52, r: 12, t: trendText ? 26 : 10, b: 36 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: "Instrument Sans, system-ui, sans-serif", size: 11, color: "#0E1524" },
      title: trendText
        ? { text: trendText, x: 0, xanchor: "left", font: { size: 11 } }
        : undefined,
      showlegend: false,
      xaxis: {
        title: { text: calendar ? "date" : "month index" },
        type: calendar ? "date" : "-",
        gridcolor: "#E4E8EE",
        zeroline: false,
      },
      yaxis: {
        title: { text: units || "value" },
        gridcolor: "#E4E8EE",
        zeroline: false,
      },
    };
    Plotly.react("chart", traces, layout, { displayModeBar: false, responsive: true });
  }

  // --- url state ------------------------------------------------------------

  function writeUrl() {
    // A dashboard you cannot link to is a dashboard only you can show. Keep the
    // variable and the charted cell in the URL, written without adding history
    // entries on every map click.
    if (!state.variable) return;
    const params = new URLSearchParams();
    params.set("variable", state.variable);
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
    const variable = params.get("variable");
    if (!variable && !isFinite(lat) && !isFinite(lon)) return null;
    return {
      variable: variable || null,
      lat: isFinite(lat) && lat !== null ? lat : null,
      lon: isFinite(lon) && lon !== null ? lon : null,
    };
  }

  // --- catalog --------------------------------------------------------------

  function selectVariable(key, button) {
    state.variable = key;
    document.querySelectorAll(".seg").forEach((b) => b.setAttribute("aria-checked", "false"));
    if (button) button.setAttribute("aria-checked", "true");
    const v = state.catalogByKey[key];
    el("variable-note").textContent = v ? `${v.label} — ${v.description}` : "";
    writeUrl();
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
      const buttons = {};
      list.forEach((v) => {
        state.catalogByKey[v.key] = v;
        const b = document.createElement("button");
        b.type = "button";
        b.className = "seg";
        b.setAttribute("role", "radio");
        b.setAttribute("aria-checked", "false");
        b.textContent = v.label;
        b.addEventListener("click", () => selectVariable(v.key, b));
        container.appendChild(b);
        buttons[v.key] = b;
      });
      // Honour `?variable=` if it names something the source serves; otherwise
      // fall back to the first variable, the same as a cold visit.
      const wanted = state.initial && state.initial.variable;
      const start = (wanted && list.find((v) => v.key === wanted)) || list[0];
      if (start) selectVariable(start.key, buttons[start.key]);
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

  function initChartResize() {
    const chart = el("chart");
    const grip = el("chart-grip");
    if (!chart || !grip) return;

    const stored = Number(localStorage.getItem("td.chartHeight"));
    if (stored > 0) chart.style.height = `${stored}px`;

    const apply = (height) => {
      const h = Math.max(180, Math.round(height));
      chart.style.height = `${h}px`;
      if (typeof Plotly !== "undefined" && Plotly.Plots) Plotly.Plots.resize(chart);
      localStorage.setItem("td.chartHeight", String(h));
    };

    let startY = 0;
    let startH = 0;
    const onMove = (e) => apply(startH + (e.clientY - startY));
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    grip.addEventListener("pointerdown", (e) => {
      startY = e.clientY;
      startH = chart.getBoundingClientRect().height;
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    });
    // The same control from the keyboard.
    grip.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
      e.preventDefault();
      const cur = chart.getBoundingClientRect().height;
      apply(cur + (e.key === "ArrowDown" ? 24 : -24));
    });

    window.addEventListener("resize", () => {
      if (typeof Plotly !== "undefined" && Plotly.Plots) Plotly.Plots.resize(chart);
    });
  }

  function init() {
    state.initial = readUrl();
    state.maps.naive = makeMap("map-naive");
    initChartResize();
    state.maps.corrected = makeMap("map-corrected");
    ["naive", "corrected"].forEach((key) => {
      const map = state.maps[key];
      map.on("click", (e) => {
        const dot = nearestDot(key, e.latlng);
        if (dot) {
          loadSeries(dot.lat, dot.lon);
        } else {
          // A panel with nothing flagged still answers, at the nearest cell.
          loadSeries(e.latlng.lat, e.latlng.lng);
        }
      });
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
