/* Small behaviours. No inline scripts anywhere (the site's Content-Security-Policy forbids them). */
(function () {
  "use strict";
  function on(sel, evt, fn) { document.querySelectorAll(sel).forEach(function (n) { n.addEventListener(evt, fn); }); }

  on("[data-toggle-nav]", "click", function () {
    var t = document.getElementById("navlinks"); if (t) t.classList.toggle("open");
  });
  on("[data-toggle-side]", "click", function () {
    var t = document.getElementById("side"); if (t) t.classList.toggle("open");
  });
  on("[data-print]", "click", function () { window.print(); });
  on("[data-autosubmit]", "change", function (e) { e.target.form.submit(); });
  on("form[data-confirm]", "submit", function (e) {
    if (!window.confirm(e.target.getAttribute("data-confirm"))) e.preventDefault();
  });

  document.querySelectorAll("[data-pct]").forEach(function (n) {
    var pct = Math.max(0, Math.min(100, parseFloat(n.getAttribute("data-pct")) || 0));
    if (n.firstElementChild) n.firstElementChild.style.width = pct + "%";
  });

  var items = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } });
    }, { threshold: 0.12 });
    items.forEach(function (n) { io.observe(n); });
  } else {
    items.forEach(function (n) { n.classList.add("in"); });
  }
})();
