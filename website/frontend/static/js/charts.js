/* Dashboard charts: plain SVG, no libraries, no network. Reads /api/v1/dashboard/stats. */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var COLORS = { total: "#3b82f6", passed: "#15803d", failed: "#b91c1c", grid: "#e3e9f5", text: "#5b6b86" };
  var STATUS = [["passed", "Passed", "#15803d"], ["failed", "Failed", "#b91c1c"], ["erased", "Erased, awaiting tests", "#d99a1f"],
    ["incomplete", "Incomplete", "#f59e0b"], ["inspected", "Inspected", "#60a5fa"], ["registered", "Registered", "#94a3b8"]];

  function el(name, attrs, text) {
    var n = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function svg(box, w, h, label) {
    box.textContent = "";
    var s = el("svg", { viewBox: "0 0 " + w + " " + h, "class": "chart", role: "img", "aria-label": label });
    box.appendChild(s); return s;
  }

  function lineChart(box, series) {
    var W = 640, H = 230, L = 34, R = 10, T = 12, B = 26;
    var s = svg(box, W, H, "Laptops registered per day");
    var max = Math.max(4, Math.max.apply(null, series.map(function (d) { return d.total; })));
    max = Math.ceil(max / 4) * 4;
    for (var i = 0; i <= 4; i++) {
      var y = T + (H - T - B) * i / 4;
      s.appendChild(el("line", { x1: L, x2: W - R, y1: y, y2: y, stroke: COLORS.grid }));
      s.appendChild(el("text", { x: L - 6, y: y + 4, "text-anchor": "end", "font-size": 10, fill: COLORS.text }, String(Math.round(max - max * i / 4))));
    }
    var n = series.length, bw = (W - L - R) / n;
    series.forEach(function (d, i) {
      var hTot = (H - T - B) * d.total / max, x = L + i * bw + bw * 0.15, w = bw * 0.7;
      var bar = el("rect", { x: x, y: H - B - hTot, width: w, height: Math.max(hTot, 0), rx: 2, fill: COLORS.total });
      bar.appendChild(el("title", {}, d.date + ": " + d.total + " registered, " + d.passed + " passed, " + d.failed + " failed"));
      s.appendChild(bar);
      if (d.failed) {
        var hf = (H - T - B) * d.failed / max;
        s.appendChild(el("rect", { x: x, y: H - B - hf, width: w, height: hf, rx: 2, fill: COLORS.failed }));
      }
      if (i % Math.ceil(n / 8) === 0) s.appendChild(el("text", { x: x + w / 2, y: H - 8, "text-anchor": "middle", "font-size": 10, fill: COLORS.text }, d.date.slice(5)));
    });
  }

  function donut(box, statuses) {
    var rows = STATUS.filter(function (r) { return statuses[r[0]]; });
    var total = rows.reduce(function (a, r) { return a + statuses[r[0]]; }, 0);
    var s = svg(box, 220, 220, "Laptops by status");
    var cx = 110, cy = 110, r = 78, sw = 30;
    if (!total) { s.appendChild(el("circle", { cx: cx, cy: cy, r: r, fill: "none", stroke: COLORS.grid, "stroke-width": sw })); }
    var C = 2 * Math.PI * r, off = 0;
    rows.forEach(function (row) {
      var len = C * statuses[row[0]] / total;
      var c = el("circle", { cx: cx, cy: cy, r: r, fill: "none", stroke: row[2], "stroke-width": sw,
        "stroke-dasharray": len + " " + (C - len), "stroke-dashoffset": -off, transform: "rotate(-90 110 110)" });
      c.appendChild(el("title", {}, row[1] + ": " + statuses[row[0]]));
      s.appendChild(c); off += len;
    });
    s.appendChild(el("text", { x: cx, y: cy + 6, "text-anchor": "middle", "font-size": 26, "font-weight": 700, fill: "#0f172a" }, String(total)));
    s.appendChild(el("text", { x: cx, y: cy + 24, "text-anchor": "middle", "font-size": 11, fill: COLORS.text }, "laptops"));
    var lg = document.createElement("div"); lg.className = "legend";
    rows.forEach(function (row) {
      var i = document.createElement("span"), sw2 = document.createElement("i");
      sw2.style.background = row[2]; i.appendChild(sw2); i.appendChild(document.createTextNode(row[1] + " " + statuses[row[0]])); lg.appendChild(i);
    });
    box.appendChild(lg);
  }

  function hbars(box, items) {
    var W = 420, rowH = 30, H = Math.max(rowH * items.length + 10, 40);
    var s = svg(box, W, H, "Laptops by manufacturer");
    var max = Math.max(1, Math.max.apply(null, items.map(function (i) { return i.count; })));
    items.forEach(function (it, i) {
      var y = 6 + i * rowH, w = (W - 130) * it.count / max;
      s.appendChild(el("text", { x: 0, y: y + 16, "font-size": 12, fill: "#0f172a" }, it.name.slice(0, 16)));
      var b = el("rect", { x: 100, y: y + 3, width: Math.max(w, 2), height: 18, rx: 4, fill: COLORS.total });
      b.appendChild(el("title", {}, it.name + ": " + it.count));
      s.appendChild(b);
      s.appendChild(el("text", { x: 106 + w, y: y + 17, "font-size": 12, fill: COLORS.text }, String(it.count)));
    });
  }

  var root = document.getElementById("dash-charts");
  if (!root) return;
  fetch("/api/v1/dashboard/stats" + window.location.search, { credentials: "same-origin", headers: { Accept: "application/json" } })
    .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(function (d) {
      lineChart(document.getElementById("chart-days"), d.series);
      donut(document.getElementById("chart-status"), d.statuses);
      var m = document.getElementById("chart-makers");
      if (m) { if (d.manufacturers.length) hbars(m, d.manufacturers); else m.textContent = "No data yet."; }
    })
    .catch(function () { root.insertAdjacentHTML("afterbegin", '<p class="flash error">Charts could not be loaded. The numbers above are still correct.</p>'); });
})();
