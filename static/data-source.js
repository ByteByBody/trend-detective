/* Data source: the one seam between the frontend and where the numbers live.

   Two modes behind one interface. "live" is the FastAPI process: /catalog,
   /demo, /timeseries and /fingerprint answer per request. "static" is the
   export published to GitHub Pages, where Python cannot run: the same four
   answers come from precomputed JSON and one int16 series cube per variable.

   Shared by the browser (window.TDData) and the Python tests (required through
   node). No DOM here, and the fetch/parse maths stay pure so they can be
   exercised without a browser. */

(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.TDData = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  //: The code the export writes for a missing month. int16 is enough for every
  //: variable's range at a precision far below the noise floor.
  const NAN_CODE = -32768;

  function nearestIndex(grid, value) {
    let best = 0;
    let bestDistance = Infinity;
    for (let i = 0; i < grid.length; i++) {
      const distance = Math.abs(grid[i] - value);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = i;
      }
    }
    return best;
  }

  function wrapDelta(delta) {
    return (((delta + 180) % 360) + 360) % 360 - 180;
  }

  function wrappedIndex(grid, value) {
    let best = 0;
    let bestDistance = Infinity;
    for (let i = 0; i < grid.length; i++) {
      const distance = Math.abs(wrapDelta(grid[i] - value));
      if (distance < bestDistance) {
        bestDistance = distance;
        best = i;
      }
    }
    return best;
  }

  /* The API snaps to the nearest grid cell with a plain absolute difference on
     each axis, so the static build mirrors that exactly. */
  function seriesIndex(meta, lat, lon) {
    return { i: nearestIndex(meta.lat, lat), j: nearestIndex(meta.lon, lon) };
  }

  /* One cell's monthly record, decoded from the quantized cube. */
  function seriesValue(meta, codes, i, j) {
    const steps = meta.shape[0];
    const ny = meta.shape[1];
    const nx = meta.shape[2];
    const out = new Array(steps);
    for (let k = 0; k < steps; k++) {
      const code = codes[(k * ny + i) * nx + j];
      out[k] = code === NAN_CODE ? null : meta.offset + code * meta.scale;
    }
    return out;
  }

  /* The region under a click, or the nearest centroid.

     The same two-step rule the API uses: a click inside a patch is answered by
     that patch's label, and a click just outside is snapped to whatever region
     is closest. Longitudes compare the short way round so the dateline does
     not read as half a world away. */
  function pickRegion(doc, lat, lon) {
    const regions = doc.regions || [];
    if (!regions.length) return null;

    const latGrid = doc.lat || [];
    const lonGrid = doc.lon || [];
    if (isFinite(lat) && isFinite(lon) && latGrid.length && lonGrid.length) {
      const i = nearestIndex(latGrid, lat);
      const j = wrappedIndex(lonGrid, lon);
      const row = doc.labels && doc.labels[i];
      const label = row ? row[j] : 0;
      const hit = regions.find((region) => region.region_id === label);
      if (hit) return hit;
    }

    let best = regions[0];
    let bestDistance = Infinity;
    for (let i = 0; i < regions.length; i++) {
      const dLat = regions[i].lat - lat;
      const dLon = wrapDelta(regions[i].lon - lon);
      const distance = dLat * dLat + dLon * dLon;
      if (distance < bestDistance) {
        bestDistance = distance;
        best = regions[i];
      }
    }
    return best;
  }

  /* The ready fingerprint body for a click, from the precomputed document. */
  function fingerprintPayload(doc, lat, lon, k) {
    const variables = doc.variables || [];
    const regions = doc.regions || [];
    const base = {
      progress: { done: variables.length, total: variables.length },
      reference: null,
      matches: [],
      variables: variables,
    };

    if (doc.status === "unavailable") {
      return Object.assign({}, base, {
        status: "unavailable",
        reason: doc.reason || null,
      });
    }

    const region = pickRegion(doc, lat, lon);
    if (!region) {
      return Object.assign({}, base, {
        status: "ready",
        reason: doc.reason || null,
        regions: [],
      });
    }

    return Object.assign({}, base, {
      status: "ready",
      reason: null,
      reference: region.reference,
      matches: (region.matches || []).slice(0, k),
      kept: doc.kept || [],
      regions: regions.map((item) => ({
        region_id: item.region_id,
        lat: item.lat,
        lon: item.lon,
      })),
    });
  }

  async function fetchJson(url) {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`request failed (${response.status})`);
    }
    return response.json();
  }

  async function fetchBuffer(url) {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`request failed (${response.status})`);
    }
    return response.arrayBuffer();
  }

  /* Bind the interface to one mode. ``fetchJson``/``fetchBuffer`` are injectable
     so the tests drive the real code without a browser. */
  function create(options) {
    const settings = options || {};
    const mode = settings.mode === "static" ? "static" : "live";
    const root = settings.root || "data";
    const readJson = settings.fetchJson || fetchJson;
    const readBuffer = settings.fetchBuffer || fetchBuffer;
    const seriesCache = {};
    const fingerprintCache = {};

    function catalog() {
      return readJson(mode === "static" ? `${root}/catalog.json` : "/catalog");
    }

    function demo(variable) {
      return readJson(
        mode === "static" ? `${root}/demo/${variable}.json` : `/demo/${variable}`
      );
    }

    function series(variable) {
      if (!seriesCache[variable]) {
        seriesCache[variable] = (async () => {
          const meta = await readJson(`${root}/series/${variable}.json`);
          const buffer = await readBuffer(`${root}/series/${variable}.bin`);
          return { meta: meta, codes: new Int16Array(buffer) };
        })();
      }
      return seriesCache[variable];
    }

    async function timeseries(variable, lat, lon) {
      if (mode !== "static") {
        return readJson(`/timeseries/${variable}?lat=${lat}&lon=${lon}`);
      }
      const loaded = await series(variable);
      const at = seriesIndex(loaded.meta, lat, lon);
      return {
        variable: variable,
        lat: loaded.meta.lat[at.i],
        lon: loaded.meta.lon[at.j],
        time: loaded.meta.time,
        time_kind: loaded.meta.time_kind,
        time_labels: loaded.meta.time_labels,
        value: seriesValue(loaded.meta, loaded.codes, at.i, at.j),
      };
    }

    async function fingerprint(variable, lat, lon, k) {
      if (mode !== "static") {
        return readJson(`/fingerprint/${variable}?lat=${lat}&lon=${lon}&k=${k}`);
      }
      if (!fingerprintCache[variable]) {
        fingerprintCache[variable] = readJson(`${root}/fingerprint/${variable}.json`);
      }
      const doc = await fingerprintCache[variable];
      return fingerprintPayload(doc, lat, lon, k === undefined ? 5 : k);
    }

    return {
      mode: mode,
      catalog: catalog,
      demo: demo,
      timeseries: timeseries,
      fingerprint: fingerprint,
    };
  }

  return {
    NAN_CODE: NAN_CODE,
    nearestIndex: nearestIndex,
    wrappedIndex: wrappedIndex,
    seriesIndex: seriesIndex,
    seriesValue: seriesValue,
    pickRegion: pickRegion,
    fingerprintPayload: fingerprintPayload,
    create: create,
  };
});
