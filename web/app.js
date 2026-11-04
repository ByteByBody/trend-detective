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
    // Position in the p-sorted contested-cell walk (see the arrow keys).
    stepIndex: -1,
    // Fingerprint mode: off by default, one poll timer, last response.
    fingerprint: { on: false, data: null, timer: null },
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

  function slopeToDataURL(slope, lat) {
    const ny = slope.length;
    const nx = slope[0].length;
    const flat = [];
    for (let i = 0; i < ny; i++) {
      for (let j = 0; j < nx; j++) {
        const v = slope[i][j];
        if (v !== null && isFinite(v)) flat.push(v);
      }
    }
    // Scale to the 2nd-98th percentile: one extreme pixel would otherwise
    // flatten the whole ramp, and the legend would quote that outlier.
    const { min, max } = TDFormat.slopeRange(flat);
    const span = max - min || 1;
    // Rows are evenly spaced in latitude; the map is Mercator. Sample through
    // the projection so every row lands where it belongs instead of drifting
    // poleward. A taller canvas keeps the equatorial rows from going blocky.
    const rows =
      lat && lat.length
        ? TDProjection.mercatorRows(lat, Math.max(ny, 256))
        : Array.from({ length: ny }, (_, i) => i);
    const canvas = document.createElement("canvas");
    canvas.width = nx;
    canvas.height = rows.length;
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(nx, rows.length);
    for (let h = 0; h < rows.length; h++) {
      const src = slope[rows[h]];
      for (let j = 0; j < nx; j++) {
        const v = src[j];
        const k = (h * nx + j) * 4;
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
                { significant: 2 })}`;
        dot.bindTooltip(
          `<strong>lat ${fmt(lat, "°", { decimals: 1 })}, ` +
            `lon ${fmt(lon, "°", { decimals: 1 })}</strong><br>` +
            `${slopeText}<br>` +
            `p-value ${fmt(p, "", { significant: 3 })}<br>` +
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
        // Nearest-neighbour for this one layer: bilinear upscaling of the
        // coarse raster blurred coastal colour into the ocean as a glow.
        className: "slope-layer",
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
    const minLat = Math.max(-TDProjection.MAX_LAT, Math.min(...lat) - dLat);
    const maxLat = Math.min(TDProjection.MAX_LAT, Math.max(...lat) + dLat);
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

  function topContested(demo, n) {
    // The five cells with the smallest p-values among those the naive test
    // flags: the claims the raw method most confidently makes and the
    // correction still drops. Those are the ones worth arguing about.
    const cells = [];
    if (!demo || !demo.naive_mask) return cells;
    demo.naive_mask.forEach((row, i) => {
      row.forEach((flagged, j) => {
        if (!flagged) return;
        cells.push({
          i,
          j,
          lat: demo.lat[i],
          lon: demo.lon[j],
          p: demo.p_value ? demo.p_value[i][j] : null,
          slope: demo.slope[i][j],
        });
      });
    });
    const pOf = (cell) =>
      cell.p === null || cell.p === undefined ? Infinity : cell.p;
    cells.sort((a, b) => pOf(a) - pOf(b));
    return cells.slice(0, n);
  }

  function renderContested(demo) {
    const list = el("contested-list");
    if (!list) return;
    list.innerHTML = "";
    const units = demo.units || "";
    topContested(demo, 5).forEach((cell) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "contested__item";

      const place = document.createElement("span");
      place.className = "contested__place";
      place.textContent =
        `${fmt(cell.lat, "°", { decimals: 1 })}, ` +
        `${fmt(cell.lon, "°", { decimals: 1 })}`;

      const readout = document.createElement("span");
      readout.className = "contested__readout";
      const pText =
        cell.p === null || cell.p === undefined
          ? "p n/a"
          : `p ${fmt(cell.p, "", { significant: 3 })}`;
      const slopeText =
        cell.slope === null || cell.slope === undefined || !isFinite(cell.slope)
          ? "trend n/a"
          : fmt(cell.slope * MONTHS_PER_DECADE,
              units ? `${units} per decade` : "per decade",
              { significant: 2 });
      readout.textContent = `${pText} · ${slopeText}`;

      b.appendChild(place);
      b.appendChild(readout);
      b.addEventListener("click", () => {
        loadSeries(cell.lat, cell.lon);
        ["naive", "corrected"].forEach((key) => {
          const map = state.maps[key];
          if (map) map.flyTo([cell.lat, cell.lon], Math.max(map.getZoom(), 3));
        });
      });
      li.appendChild(b);
      list.appendChild(li);
    });
  }

  // --- fingerprints ----------------------------------------------------------

  function fingerprintStatus(text) {
    const node = el("fingerprint-status");
    if (node) node.textContent = text;
  }

  function clearFingerprintLayers() {
    ["naive", "corrected"].forEach((key) => {
      const group = state.overlays["fingerprint-" + key];
      const map = state.maps[key];
      if (group && map) map.removeLayer(group);
      state.overlays["fingerprint-" + key] = null;
    });
  }

  function nearestRegion(lat, lon) {
    // Snap a click to the region centre, so a fingerprint request always lands
    // on a patch rather than on whatever pixel the cursor happened to hit.
    const data = state.fingerprint.data;
    if (!data || !Array.isArray(data.regions) || !data.regions.length) return null;
    let best = null;
    let bestD = Infinity;
    data.regions.forEach((region) => {
      const dLat = region.lat - lat;
      const dLon = ((region.lon - lon + 540) % 360) - 180;
      const d = dLat * dLat + dLon * dLon;
      if (d < bestD) {
        bestD = d;
        best = region;
      }
    });
    return best;
  }

  function renderFingerprintList(matches) {
    const list = el("fingerprint-list");
    if (!list) return;
    list.innerHTML = "";
    matches.forEach((match, idx) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "fingerprint__item";
      const where =
        `${fmt(match.lat, "°", { decimals: 1 })}, ` +
        `${fmt(match.lon, "°", { decimals: 1 })}`;
      const distance =
        match.distance === undefined
          ? ""
          : ` · distance ${fmt(match.distance, "", { decimals: 2 })}`;
      b.textContent = `${idx + 1}. ${where}${distance}`;
      b.addEventListener("click", () => {
        loadSeries(match.lat, match.lon);
        ["naive", "corrected"].forEach((key) => {
          const map = state.maps[key];
          if (map) map.flyTo([match.lat, match.lon], Math.max(map.getZoom(), 3));
        });
      });
      li.appendChild(b);
      list.appendChild(li);
    });
  }

  function drawFingerprint(body) {
    clearFingerprintLayers();
    const reference = body.reference;
    const matches = body.matches || [];

    if (!reference && !matches.length) {
      renderFingerprintList([]);
      fingerprintStatus(
        "No corrected regions to compare for this variable. Try another " +
          "variable or a different cell."
      );
      return;
    }

    ["naive", "corrected"].forEach((key) => {
      const map = state.maps[key];
      if (!map) return;
      const group = L.layerGroup();

      const drawRegion = (region, style, tooltip) => {
        (region.rings || []).forEach((ring) => {
          if (ring.length < 3) return;
          L.polygon(ring, style).bindTooltip(tooltip, { sticky: true }).addTo(group);
        });
        const label = L.marker([region.lat, region.lon], {
          icon: L.divIcon({
            className: "fp-label",
            html: style.label,
            iconSize: [18, 18],
          }),
          keyboard: false,
        });
        label.bindTooltip(tooltip, { direction: "top" });
        label.addTo(group);
      };

      if (reference) {
        drawRegion(
          reference,
          { color: "#1B3A6B", weight: 3, dashArray: "5 3", fill: false, label: "R" },
          "Reference region"
        );
      }
      matches.forEach((match, idx) => {
        const vector = Object.entries(match.vector || {})
          .filter(([, v]) => v !== null && v !== undefined)
          .map(([name, v]) => `${name} ${fmt(v, "", { significant: 2 })}`)
          .join(" · ");
        drawRegion(
          match,
          { color: "#0C7A66", weight: 2, fill: false, label: String(idx + 1) },
          `Match ${idx + 1}${vector ? " · " + vector : ""}`
        );
      });

      group.addTo(map);
      state.overlays["fingerprint-" + key] = group;
    });

    const where = reference
      ? `${fmt(reference.lat, "°", { decimals: 1 })}, ` +
        `${fmt(reference.lon, "°", { decimals: 1 })}`
      : "the selected cell";
    const kept = (body.kept || []).join(", ");
    fingerprintStatus(
      `${fmt(matches.length, matches.length === 1 ? "region" : "regions")} ` +
        `resemble ${where}.` +
        (kept ? ` Driving variables: ${kept}.` : "")
    );
    renderFingerprintList(matches);
  }

  async function requestFingerprint(lat, lon) {
    if (!state.fingerprint.on || !state.variable) return;
    clearTimeout(state.fingerprint.timer);
    state.fingerprint.timer = null;
    fingerprintStatus("Preparing fingerprints...");
    try {
      const r = await fetch(
        `/fingerprint/${state.variable}?lat=${lat}&lon=${lon}&k=5`
      );
      if (!r.ok) throw new Error(`request failed (${r.status})`);
      const body = await r.json();
      state.fingerprint.data = body;

      if (body.status === "preparing") {
        const done = body.progress ? body.progress.done : 0;
        const total = body.progress ? body.progress.total : 0;
        fingerprintStatus(
          `Preparing fingerprints: ${fmt(done, "")} of ` +
            `${fmt(total, "variables")} analysed. The first run downloads the rest.`
        );
        state.fingerprint.timer = setTimeout(
          () => requestFingerprint(lat, lon),
          2500
        );
        return;
      }
      if (body.status === "unavailable") {
        clearFingerprintLayers();
        renderFingerprintList([]);
        fingerprintStatus(
          body.reason || "Fingerprints are unavailable for this source."
        );
        return;
      }
      drawFingerprint(body);
    } catch (err) {
      fingerprintStatus(`Could not prepare fingerprints: ${err.message}.`);
    }
  }

  function initFingerprint() {
    const button = el("fingerprint-toggle");
    if (!button) return;
    button.addEventListener("click", () => {
      state.fingerprint.on = !state.fingerprint.on;
      button.setAttribute("aria-pressed", String(state.fingerprint.on));
      button.textContent = state.fingerprint.on
        ? "Hide matches"
        : "Find matching regions";
      if (!state.fingerprint.on) {
        clearTimeout(state.fingerprint.timer);
        state.fingerprint.timer = null;
        clearFingerprintLayers();
        state.fingerprint.data = null;
        renderFingerprintList([]);
        fingerprintStatus(
          "Off. Chart a cell, then find the regions whose trends resemble it."
        );
        return;
      }
      if (state.selected) requestFingerprint(state.selected.lat, state.selected.lon);
    });
  }

  // --- stripes --------------------------------------------------------------

  function hexMix(a, b, t) {
    const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
    const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
    const mix = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
    return `rgb(${mix[0]}, ${mix[1]}, ${mix[2]})`;
  }

  function renderStripes(demo) {
    // The canonical warming-stripe band, drawn from this variable's global
    // annual anomaly: one year per stripe, blue below the record mean, red
    // above. A plain ruled band stands in until the first analysis lands.
    const band = el("stripe-band");
    if (!band) return;
    const values = (demo && demo.annual_anomaly) || [];
    const finite = values.filter((v) => v !== null && isFinite(v));
    band.innerHTML = "";

    if (!finite.length) {
      const rule = document.createElement("span");
      rule.className = "stripe-band__rule";
      band.appendChild(rule);
      band.title = "The annual anomaly band appears with the first analysis.";
      return;
    }

    const scale = Math.max(...finite.map((v) => Math.abs(v))) || 1;
    const COOL_NEAR = "#C6DBEF";
    const COOL_FAR = "#08306B";
    const WARM_NEAR = "#FCBBA1";
    const WARM_FAR = "#67000D";

    values.forEach((v) => {
      const cell = document.createElement("span");
      cell.className = "stripe-band__year";
      if (v === null || !isFinite(v)) {
        cell.style.background = "#D5DBE3";
      } else {
        const t = Math.min(1, Math.abs(v) / scale);
        cell.style.background =
          v >= 0 ? hexMix(WARM_NEAR, WARM_FAR, t) : hexMix(COOL_NEAR, COOL_FAR, t);
      }
      band.appendChild(cell);
    });
    band.title =
      "Global annual mean anomaly for the selected variable: oldest year at " +
      "the left. Red is warmer than the record mean, blue cooler.";
  }

  function renderDemo(demo) {
    state.demo = demo;
    state.bounds = gridBounds(demo);
    Object.values(state.maps).forEach((map) => {
      // Fit the view to the data, but leave the world reachable: a regional
      // source covers a box, and clamping the map to it hides everything else.
      map.fitBounds(state.bounds, { padding: [0, 0] });
    });

    const slopeImg = slopeToDataURL(demo.slope, demo.lat);
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
      `(p \u2264 ${fmt(demo.threshold, "p", { significant: 3 })}) or sits alone, ` +
      "outside any patch of five.";

    el("stat-naive").textContent = fmt(demo.n_naive, "pixels");
    el("stat-corrected").textContent = fmt(demo.n_corrected, "pixels");
    el("stat-removed").textContent = fmt(-removed, "pixels");
    el("stat-kept").textContent = fmt(demo.n_corrected, "pixels");
    el("stat-threshold").textContent = fmt(demo.threshold, "p", { significant: 3 });
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
        significant: 2,
      });
    el("slope-min").textContent = perDecade(slopeImg.min);
    el("slope-max").textContent = perDecade(slopeImg.max);

    renderContested(demo);
    renderStripes(demo);

    // One orchestrated reveal when the analysis lands; nothing else animates.
    const verdict = document.querySelector(".verdict");
    if (verdict) {
      verdict.classList.remove("is-in");
      void verdict.offsetWidth;
      verdict.classList.add("is-in");
    }
    document.querySelectorAll(".panel__tally").forEach((node) => {
      node.classList.remove("is-in");
      void node.offsetWidth;
      node.classList.add("is-in");
    });

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

    if (state.fingerprint.on) requestFingerprint(cell.lat, cell.lon);

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
        significant: 2,
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
    if (!variable && lat === null && lon === null) return null;
    return {
      variable: variable || null,
      lat: isFinite(lat) && lat !== null ? lat : null,
      lon: isFinite(lon) && lon !== null ? lon : null,
    };
  }

  // --- catalog --------------------------------------------------------------

  function selectVariable(key, button) {
    state.variable = key;
    state.stepIndex = -1;
    clearFingerprintLayers();
    state.fingerprint.data = null;
    renderFingerprintList([]);
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

  function setBusy(on) {
    // A cold analysis can take minutes; a blank map reads as broken. Say what
    // is happening and stop the variable control from stacking requests.
    const busy = el("busy");
    if (busy) {
      busy.hidden = !on;
      if (on) {
        busy.textContent =
          "First run: downloading the field and computing the trend. This " +
          "can take a few minutes; later visits reuse the cached analysis.";
      }
    }
    const control = el("variable");
    if (control) {
      control.setAttribute("aria-busy", on ? "true" : "false");
      control.querySelectorAll("button").forEach((b) => {
        b.disabled = on;
      });
    }
  }

  async function loadDemo(variable) {
    setBusy(true);
    el("verdict-line").textContent = "Reading the field…";
    el("verdict-note").textContent = "\u00a0";
    try {
      const r = await fetch(`/demo/${variable}`);
      if (!r.ok) throw new Error(`request failed (${r.status})`);
      renderDemo(await r.json());
    } catch (err) {
      renderError(`Could not load the analysis: ${err.message}.`);
    } finally {
      setBusy(false);
    }
  }

  function initChartResize() {
    const chart = el("chart");
    const grip = el("chart-grip");
    if (!chart || !grip) return;

    const stored = Number(localStorage.getItem("td.chartHeight"));
    if (stored > 0) chart.style.height = `${stored}px`;

    // Keep the separator's ARIA value in step with the height, so assistive
    // tech hears the current chart size, not a fixed placeholder.
    const announce = (h) => {
      grip.setAttribute("aria-valuemin", "180");
      grip.setAttribute("aria-valuemax", String(Math.max(1200, h)));
      grip.setAttribute("aria-valuenow", String(h));
      grip.setAttribute("aria-valuetext", `${h} pixels tall`);
    };

    const apply = (height) => {
      const h = Math.max(180, Math.round(height));
      chart.style.height = `${h}px`;
      if (typeof Plotly !== "undefined" && Plotly.Plots) Plotly.Plots.resize(chart);
      localStorage.setItem("td.chartHeight", String(h));
      announce(h);
    };

    announce(
      Math.round(
        stored > 0 ? stored : chart.getBoundingClientRect().height || 320
      )
    );

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
    initFingerprint();
    state.maps.corrected = makeMap("map-corrected");
    ["naive", "corrected"].forEach((key) => {
      const map = state.maps[key];
      map.on("click", (e) => {
        // In fingerprint mode the unit of interest is the patch, not the pixel.
        if (state.fingerprint.on) {
          const region = nearestRegion(e.latlng.lat, e.latlng.lng);
          if (region) {
            loadSeries(region.lat, region.lon);
            return;
          }
        }
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
            return;
          }
          // Left and right walk the contested cells in p-value order, the same
          // order the list uses, so the keyboard hears the same story.
          if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
            const cells = state.demo ? topContested(state.demo, Infinity) : [];
            if (!cells.length) return;
            e.preventDefault();
            const step = e.key === "ArrowRight" ? 1 : -1;
            state.stepIndex =
              (state.stepIndex + step + cells.length) % cells.length;
            const cell = cells[state.stepIndex];
            map.panTo([cell.lat, cell.lon]);
            loadSeries(cell.lat, cell.lon);
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
