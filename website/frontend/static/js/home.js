// Homepage behaviour: reveal animations, mobile menu, contact + login + register forms.
(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };

  // scroll reveal
  var items = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, { threshold: 0.12 });
    items.forEach(function (el, i) { el.style.transitionDelay = (i % 4) * 70 + 'ms'; io.observe(el); });
  } else { items.forEach(function (el) { el.classList.add('in'); }); }

  // mobile menu
  var nav = $('#nav'), burger = $('#burger');
  burger.addEventListener('click', function () {
    var open = nav.classList.toggle('open');
    burger.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  document.querySelectorAll('#menu a').forEach(function (a) { a.addEventListener('click', function () { nav.classList.remove('open'); }); });

  function api(path, body) {
    return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, data: j }; }); });
  }
  function formData(form) { var o = {}; new FormData(form).forEach(function (v, k) { o[k] = v; }); return o; }
  function show(el, text, kind) { el.textContent = text; el.className = 'msg ' + kind; el.hidden = false; }

  // contact form
  $('#contactForm').addEventListener('submit', function (e) {
    e.preventDefault();
    var box = $('#contactMsg'), btn = e.target.querySelector('button');
    btn.disabled = true;
    api('/api/contact', formData(e.target)).then(function (r) {
      btn.disabled = false;
      show(box, r.ok ? r.data.message : (r.data.detail || 'Something went wrong.'), r.ok ? 'ok' : 'err');
      if (r.ok) e.target.reset();
    });
  });

  // tabs
  function tab(which) {
    ['login', 'register'].forEach(function (t) {
      $('#tab-' + t).setAttribute('aria-selected', t === which ? 'true' : 'false');
      document.querySelector('[data-pane="' + t + '"]').hidden = t !== which;
    });
  }
  document.querySelectorAll('[data-tab]').forEach(function (b) {
    b.addEventListener('click', function () { tab(b.getAttribute('data-tab')); });
  });
  if (location.hash === '#register') tab('register');

  // sign in
  $('#loginForm').addEventListener('submit', function (e) {
    e.preventDefault();
    var box = $('#loginMsg'), btn = e.target.querySelector('button.btn');
    btn.disabled = true;
    api('/api/auth/login', formData(e.target)).then(function (r) {
      btn.disabled = false;
      if (r.ok) { location.href = '/app'; return; }
      show(box, r.data.detail || 'Could not sign in.', 'err');
    });
  });
  $('#forgotBtn').addEventListener('click', function () { location.href = '/app#/forgot'; });

  // register
  $('#registerForm').addEventListener('submit', function (e) {
    e.preventDefault();
    var box = $('#registerMsg'), btn = e.target.querySelector('button.btn');
    btn.disabled = true;
    api('/api/auth/register', formData(e.target)).then(function (r) {
      btn.disabled = false;
      if (!r.ok) { show(box, r.data.detail || 'Could not create the account.', 'err'); return; }
      var t = r.data.message;
      if (r.data.dev_token) t += ' (Development mode: open /app#/verify?token=' + r.data.dev_token + ')';
      show(box, t, 'ok');
      e.target.reset();
    });
  });
})();
