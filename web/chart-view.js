/* Chart view: Plotly traces, layout, and the zoom behaviour.

   Desmos logic lives in CONFIG: wheel zooms around the cursor, drag pans, and
   double-click resets. The module is pure so the config can be pinned by a
   node test; app.js only calls Plotly.react. */

(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.TDChart = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const CONFIG = {
    scrollZoom: true,
    displayModeBar: false,
    doubleClick: "reset",
    responsive: true,
  };

  function traces(body, units, trendMonthly) {
    const calendar = body.time_kind === "calendar";
    const xs = body.time_labels || body.time;
    const out = [
      {
        x: xs,
        y: body.value,
        mode: "lines",
        name: "monthly",
        line: { color: "#8ED8FF", width: 1.5 },
        hovertemplate: calendar
          ? "%{x|%b %Y}<br>%{y:.3f}<extra></extra>"
          : "month %{x}<br>%{y:.3f}<extra></extra>",
      },
    ];

    if (trendMonthly === null || trendMonthly === undefined || !isFinite(trendMonthly)) {
      return out;
    }

    const values = body.value;
    const n = values.length;
    let sum = 0;
    let count = 0;
    values.forEach(function (v) {
      if (v !== null && isFinite(v)) {
        sum += v;
        count += 1;
      }
    });
    const mean = count ? sum / count : 0;
    const intercept = mean - trendMonthly * ((n - 1) / 2);

    out.push({
      x: xs,
      y: values.map(function (_, k) {
        return intercept + trendMonthly * k;
      }),
      mode: "lines",
      name: "trend",
      line: { color: "#FF6B45", width: 1.5, dash: "dash" },
      hoverinfo: "skip",
    });
    return out;
  }

  function layout(units, hasTrend, calendar) {
    return {
      dragmode: "pan",
      hovermode: "x",
      margin: { l: 52, r: 14, t: hasTrend ? 18 : 8, b: 34 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: "Azeret Mono, ui-monospace, monospace", size: 10, color: "#A9BFC6" },
      showlegend: false,
      xaxis: {
        title: { text: calendar ? "date" : "month index", font: { size: 9 } },
        type: calendar ? "date" : "linear",
        color: "#8FA6AD",
        gridcolor: "rgba(242,247,247,0.08)",
        zeroline: false,
        fixedrange: false,
      },
      yaxis: {
        title: { text: units || "value", font: { size: 9 } },
        color: "#8FA6AD",
        gridcolor: "rgba(242,247,247,0.08)",
        zeroline: false,
        fixedrange: false,
      },
    };
  }

  return { CONFIG: CONFIG, traces: traces, layout: layout };
});
