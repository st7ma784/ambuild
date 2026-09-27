// Charts drawn from JSON embedded in the page, with uPlot. Markup:
//   <figure class="chart" data-source="ID"><div class="chart-canvas"></div>...</figure>
//   <script type="application/json" id="ID">
//     {"xlabel": "step", "ylabel": "atoms",
//      "series": [{"label": "run a", "x": [...], "y": [...]}, ...]}
//   </script>
// Series may have different x values (runs of different lengths); uPlot.join aligns them.
// A table beside each chart holds the same numbers, so the page works without JavaScript.
(function () {
  "use strict";
  var PALETTE = ["--accent", "--series2", "--series3", "--series4", "--series5", "--series6"];

  function cssVar(name, fallback) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function render(figure) {
    var source = document.getElementById(figure.dataset.source);
    var canvas = figure.querySelector(".chart-canvas");
    if (!source || !canvas || typeof uPlot === "undefined") return;
    var spec = JSON.parse(source.textContent);
    var series = spec.series.filter(function (s) { return s.x && s.x.length; });
    if (!series.length) return;
    var ink = cssVar("--muted", "#5d676c");
    var rule = cssVar("--rule", "#d8ddda");
    var height = parseInt(figure.dataset.height || "220", 10);
    var axis = function (label) {
      return {label: label, stroke: ink, grid: {stroke: rule, width: 1}, ticks: {stroke: rule, width: 1}};
    };
    var options = {
      width: canvas.clientWidth || 480,
      height: height,
      scales: {x: {time: false}},
      axes: [axis(spec.xlabel), axis(spec.ylabel)],
      series: [{label: spec.xlabel}].concat(series.map(function (s, i) {
        var colour = cssVar(PALETTE[i % PALETTE.length], "#0b6f7f");
        if (s.scatter) {  // points only
          return {label: s.label, stroke: colour, fill: colour, paths: function () { return null; },
                  points: {show: true, size: 7, fill: colour}};
        }
        return {label: s.label, stroke: colour, width: 2, spanGaps: true, points: {show: s.x.length < 60}};
      })),
      legend: {show: series.length > 1 || !!spec.legend},
    };
    var data = uPlot.join(series.map(function (s) { return [s.x, s.y]; }));
    canvas.innerHTML = "";
    var plot = new uPlot(options, data, canvas);
    if (window.ResizeObserver) {
      new ResizeObserver(function () {
        plot.setSize({width: canvas.clientWidth, height: height});
      }).observe(canvas);
    }
  }

  function renderAll(root) {
    (root || document).querySelectorAll("figure.chart").forEach(render);
  }

  document.addEventListener("DOMContentLoaded", function () { renderAll(document); });
  // After any htmx swap (including out-of-band ones, as on a live run page), draw the
  // charts that arrived empty
  document.addEventListener("htmx:afterSettle", function () {
    document.querySelectorAll("figure.chart").forEach(function (figure) {
      var canvas = figure.querySelector(".chart-canvas");
      if (canvas && !canvas.firstChild) render(figure);
    });
  });
})();
