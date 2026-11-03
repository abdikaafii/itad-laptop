// ITAD Seal platform: single-page app (vanilla JS, no build step).
// All dynamic text goes through esc(); there are no inline event handlers (CSP-safe).
(function () {
  'use strict';
  var S = { user: null, csrf: null, env: 'production', cache: {} };
  var root = document.getElementById('root');

  // ------------------------------------------------------------ helpers
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function slug(s) { return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); }
  function chip(t, cls) { return '<span class="chip ' + (cls || slug(t)) + '">' + esc(t) + '</span>'; }
  function ico(n) { return '<svg class="ico"><use href="/static/img/sprite.svg#i-' + n + '"/></svg>'; }
  function qs(o) { var p = []; Object.keys(o || {}).forEach(function (k) { if (o[k] !== '' && o[k] != null) p.push(encodeURIComponent(k) + '=' + encodeURIComponent(o[k])); }); return p.length ? '?' + p.join('&') : ''; }
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  var isAdmin = function () { return S.user && (S.user.role === 'super_admin' || S.user.role === 'company_admin'); };
  var isStaff = function () { return S.user && S.user.role !== 'customer'; };
  var isSuper = function () { return S.user && S.user.role === 'super_admin'; };

  function api(method, path, body) {
    var opt = { method: method, credentials: 'same-origin', headers: {} };
    if (method !== 'GET') { opt.headers['Content-Type'] = 'application/json'; opt.headers['X-CSRF-Token'] = S.csrf || ''; opt.body = JSON.stringify(body || {}); }
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 401 && S.user) { S.user = null; go('#/login'); }
        if (!r.ok) { var e = new Error(j.detail || j.reason || 'Request failed (' + r.status + ')'); e.status = r.status; e.data = j; throw e; }
        return j;
      });
    });
  }
  function toast(text, kind) {
    var d = document.createElement('div');
    d.className = 'msg ' + (kind || 'ok'); d.setAttribute('role', 'status');
    d.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:200;box-shadow:0 10px 30px rgba(0,0,0,.25);max-width:360px';
    d.textContent = text; document.body.appendChild(d); setTimeout(function () { d.remove(); }, 4200);
  }
  function go(h) { if (location.hash === h) route(); else location.hash = h; }

  // modal form
  function modal(title, html, onSubmit, okText, wide) {
    var sc = document.createElement('div'); sc.className = 'scrim';
    sc.innerHTML = '<form class="modal" role="dialog" aria-modal="true" aria-label="' + esc(title) + '"' + (wide ? ' style="width:min(760px,100%)"' : '') + '><h2>' + esc(title) + '</h2>' + html +
      '<div class="msg err" hidden></div><div class="foot"><button type="button" class="btn ghost" data-close style="color:#33507f">Cancel</button>' + (onSubmit ? '<button class="btn" type="submit">' + esc(okText || 'Save') + '</button>' : '') + '</div></form>';
    document.body.appendChild(sc);
    var f = $('form', sc), err = $('.msg.err', f);
    function close() { sc.remove(); }
    sc.addEventListener('click', function (e) { if (e.target === sc || e.target.hasAttribute('data-close')) close(); });
    sc.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
    var first = $('input,select,textarea', f); if (first) first.focus();
    f.addEventListener('submit', function (e) {
      e.preventDefault();
      if (!onSubmit) return;
      var d = {}; new FormData(f).forEach(function (v, k) { d[k] = v; });
      $$('input[type=checkbox]', f).forEach(function (c) { d[c.name] = c.checked; });
      var b = $('button[type=submit]', f); b.disabled = true; err.hidden = true;
      Promise.resolve(onSubmit(d, close)).then(function () { }, function (x) { err.textContent = x.message; err.hidden = false; }).then(function () { b.disabled = false; });
    });
    return { close: close, el: sc };
  }
  function fld(label, name, o) {
    o = o || {}; var id = 'f_' + name, v = o.value == null ? '' : o.value;
    var inner;
    if (o.options) inner = '<select id="' + id + '" name="' + name + '"' + (o.required ? ' required' : '') + '>' + o.options.map(function (x) { return '<option value="' + esc(x[0]) + '"' + (String(x[0]) === String(v) ? ' selected' : '') + '>' + esc(x[1]) + '</option>'; }).join('') + '</select>';
    else if (o.area) inner = '<textarea id="' + id + '" name="' + name + '" rows="3">' + esc(v) + '</textarea>';
    else inner = '<input id="' + id + '" name="' + name + '" type="' + (o.type || 'text') + '" value="' + esc(v) + '"' + (o.required ? ' required' : '') + (o.ph ? ' placeholder="' + esc(o.ph) + '"' : '') + (o.max ? ' maxlength="' + o.max + '"' : '') + ' autocomplete="off">';
    return '<div class="field"><label for="' + id + '">' + esc(label) + '</label>' + inner + '</div>';
  }
  function table(heads, rows, empty, clickIdx) {
    if (!rows.length) return '<div class="empty">' + esc(empty || 'Nothing here yet.') + '</div>';
    return '<div class="tw"><table class="t"><thead><tr>' + heads.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      rows.map(function (r) { return '<tr' + (r.href ? ' class="click" data-href="' + esc(r.href) + '"' : '') + '>' + r.cells.map(function (c) { return '<td>' + c + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table></div>';
  }
  function head(title, sub, actions) {
    return '<div class="top"><div><button class="btn ghost sm menu-btn" data-act="menu" style="color:#33507f" aria-label="Menu">Menu</button> <h1 style="display:inline">' + esc(title) + '</h1>' + (sub ? '<div class="sub">' + esc(sub) + '</div>' : '') + '</div><div class="actions">' + (actions || '') + '</div></div>';
  }
  function download(url) { window.open(url, '_blank', 'noopener'); }

  // ------------------------------------------------------------ shell
  var NAV = [
    ['#/dashboard', 'chart', 'Dashboard', 'all'], ['#/jobs', 'file', 'Jobs', 'all'], ['#/assets', 'laptop', 'Assets', 'all'],
    ['#/certificates', 'shield', 'Certificates', 'all'], ['#/reports', 'file', 'Reports', 'all'], ['#/customers', 'users', 'Customers', 'staff'],
    ['#/submissions', 'cycle', 'Engine inbox', 'staff'], ['#/team', 'users', 'Team', 'admin'], ['#/settings', 'bios', 'Settings', 'admin'],
    ['#/companies', 'bios', 'Companies', 'super'], ['#/audit', 'clock', 'Audit log', 'admin']
  ];
  function shell(active, content) {
    var items = NAV.filter(function (n) { return n[3] === 'all' || (n[3] === 'staff' && isStaff()) || (n[3] === 'admin' && isAdmin()) || (n[3] === 'super' && isSuper()); });
    var label = { super_admin: 'Super administrator', company_admin: 'Company administrator', technician: 'Technician', customer: 'Customer' }[S.user.role];
    var jobsLabel = S.user.role === 'customer' ? 'Job progress' : 'Jobs';
    root.innerHTML = '<div class="shell"><nav class="side" id="side" aria-label="Main"><a class="brand" href="/" style="color:#fff">' + ico('shield') + 'ITAD <b>Seal</b></a>' +
      items.map(function (n) { return '<a class="nl' + (active === n[0] ? ' on' : '') + '" href="' + n[0] + '">' + ico(n[1]) + esc(n[0] === '#/jobs' ? jobsLabel : n[2]) + '</a>'; }).join('') +
      '<div class="me"><b>' + esc(S.user.name) + '</b><span>' + esc(label) + (S.user.company_name ? ' · ' + esc(S.user.company_name) : '') + '</span><div class="actions" style="margin-top:8px"><a class="btn ghost sm" href="#/profile" style="color:#cfe0ff">Profile</a><button class="btn ghost sm" data-act="logout" style="color:#cfe0ff">Sign out</button></div></div></nav>' +
      '<main class="main" id="view">' + content + '</main></div>';
  }
  function demoBanner() { return S.env !== 'production' ? '<div class="banner">Development mode. Demonstration data is clearly labelled and never valid as evidence.</div>' : ''; }

  // ------------------------------------------------------------ auth screens
  function authScreen(title, body) {
    root.innerHTML = '<div class="authwrap"><div class="authbox"><a class="brand" href="/" style="color:#fff">' + ico('shield') + 'ITAD <b>Seal</b></a><h1 style="font-size:1.4rem;margin:.1em 0 .8em">' + esc(title) + '</h1>' + body + '</div></div>';
  }
  function viewLogin() {
    authScreen('Sign in', '<form id="f"><div class="field"><label for="e">E-mail</label><input id="e" name="email" type="email" autocomplete="username" required></div><div class="field"><label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required></div><div class="msg err" id="m" hidden></div><button class="btn" type="submit">Sign in</button></form><p style="margin-top:1rem;font-size:.9rem"><a href="#/forgot">Forgot your password?</a> · <a href="/#login">Create a company account</a></p>');
    $('#f').addEventListener('submit', function (e) {
      e.preventDefault(); var d = {}; new FormData(e.target).forEach(function (v, k) { d[k] = v; });
      fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d), credentials: 'same-origin' }).then(function (r) { return r.json().then(function (j) { return [r, j]; }); }).then(function (x) {
        if (!x[0].ok) { var m = $('#m'); m.textContent = x[1].detail || 'Could not sign in.'; m.hidden = false; return; }
        S.user = x[1].user; S.csrf = x[1].csrf; go('#/dashboard');
      });
    });
  }
  function viewForgot() {
    authScreen('Reset your password', '<form id="f"><div class="field"><label for="e">E-mail</label><input id="e" name="email" type="email" required></div><div class="msg info" id="m" hidden></div><button class="btn" type="submit">Send reset link</button></form><p style="margin-top:1rem"><a href="#/login">Back to sign in</a></p>');
    $('#f').addEventListener('submit', function (e) {
      e.preventDefault();
      fetch('/api/auth/forgot', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: e.target.email.value }) }).then(function (r) { return r.json(); }).then(function (j) {
        var m = $('#m'); m.textContent = j.message + (j.dev_token ? ' (Development: #/reset?token=' + j.dev_token + ')' : ''); m.hidden = false;
      });
    });
  }
  function tokenFromHash() { var m = location.hash.match(/token=([^&]+)/); return m ? decodeURIComponent(m[1]) : ''; }
  function viewReset() {
    authScreen('Choose a password', '<form id="f"><div class="field"><label for="p">New password (10+ characters, 3 kinds)</label><input id="p" name="password" type="password" autocomplete="new-password" required minlength="10"></div><div class="msg err" id="m" hidden></div><button class="btn" type="submit">Save password</button></form>');
    $('#f').addEventListener('submit', function (e) {
      e.preventDefault();
      fetch('/api/auth/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: tokenFromHash(), password: e.target.password.value }) }).then(function (r) { return r.json().then(function (j) { return [r, j]; }); }).then(function (x) {
        if (x[0].ok) { toast('Password saved. You can sign in now.'); go('#/login'); } else { var m = $('#m'); m.textContent = x[1].detail; m.hidden = false; }
      });
    });
  }
  function viewVerifyEmail() {
    authScreen('Verifying your e-mail', '<div id="m" class="msg info">Checking the link…</div><p><a href="#/login">Go to sign in</a></p>');
    fetch('/api/auth/verify-email', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: tokenFromHash() }) }).then(function (r) { return r.json().then(function (j) { return [r, j]; }); }).then(function (x) {
      var m = $('#m'); m.className = 'msg ' + (x[0].ok ? 'ok' : 'err'); m.textContent = x[0].ok ? 'E-mail verified. You can sign in now.' : x[1].detail;
    });
  }

  // ------------------------------------------------------------ charts
  function barChart(data, w, h) {
    if (!data.length) return '<div class="empty">No assets registered in this period.</div>';
    var max = Math.max.apply(null, data.map(function (d) { return d.n; })) || 1, bw = (w - 40) / data.length, out = '<svg class="chart" viewBox="0 0 ' + w + ' ' + h + '" role="img" aria-label="Assets registered per day">';
    var steps = max < 4 ? max : 4; for (var g = 0; g <= steps; g++) { var y = 14 + (h - 40) * g / steps; out += '<line x1="34" x2="' + w + '" y1="' + y + '" y2="' + y + '" stroke="#e3eaf5"/><text x="28" y="' + (y + 3) + '" text-anchor="end">' + Math.round(max * (steps - g) / steps) + '</text>'; }
    data.forEach(function (d, i) {
      var bh = (h - 40) * d.n / max, x = 38 + i * bw;
      out += '<rect x="' + x + '" y="' + (h - 26 - bh) + '" width="' + Math.max(3, bw - 6) + '" height="' + bh + '" rx="3" fill="#2e9bff"><title>' + esc(d.day) + ': ' + d.n + '</title></rect>';
      if (data.length <= 14 || i % Math.ceil(data.length / 8) === 0) out += '<text x="' + (x + (bw - 6) / 2) + '" y="' + (h - 10) + '" text-anchor="middle">' + esc(d.day.slice(5)) + '</text>';
    });
    return out + '</svg>';
  }
  var COL = { completed: '#1fa971', assessed: '#2e9bff', tested: '#38bdf8', erased: '#7c8cff', inspected: '#9db7e0', registered: '#c4d0e4', failed: '#c62f2f', blocked: '#8a1c1c' };
  function donut(by) {
    var keys = Object.keys(by), tot = keys.reduce(function (a, k) { return a + by[k]; }, 0);
    if (!tot) return '<div class="empty">No assets yet.</div>';
    var r = 52, c = 2 * Math.PI * r, off = 0, arcs = '';
    keys.forEach(function (k) { var len = c * by[k] / tot; arcs += '<circle r="' + r + '" cx="70" cy="70" fill="none" stroke="' + (COL[k] || '#999') + '" stroke-width="22" stroke-dasharray="' + len + ' ' + (c - len) + '" stroke-dashoffset="' + (-off) + '" transform="rotate(-90 70 70)"><title>' + esc(k) + ': ' + by[k] + '</title></circle>'; off += len; });
    return '<div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap"><svg viewBox="0 0 140 140" width="140" height="140" role="img" aria-label="Assets by status">' + arcs + '<text x="70" y="76" text-anchor="middle" style="font-size:22px;font-weight:800;fill:#0b1f40">' + tot + '</text></svg><div class="legend" style="flex-direction:column">' +
      keys.map(function (k) { return '<span><i style="background:' + (COL[k] || '#999') + '"></i>' + esc(k) + ' · ' + by[k] + '</span>'; }).join('') + '</div></div>';
  }

  // ------------------------------------------------------------ views
  var V = {};
  V.dashboard = function () {
    return api('GET', '/api/dashboard').then(function (d) {
      var range = 30;
      function chart() { var data = d.assets_per_day.slice(-range); $('#bars').innerHTML = barChart(data, 560, 190); }
      shell('#/dashboard', demoBanner() + head('Dashboard', S.user.role === 'customer' ? 'Your assets at a glance' : 'Processing at a glance',
        (isStaff() ? '<a class="btn sm" href="#/assets?new=1">Register laptop</a><a class="btn sm ghost" style="color:#33507f" href="#/reports">Reports</a>' : '')) +
        '<div class="grid k4"><div class="box stat"><small>Total assets</small><b>' + d.total_assets + '</b></div><div class="box stat"><small>Successfully erased</small><b class="g">' + d.erased_assets + '</b></div>' +
        '<div class="box stat"><small>Failed or blocked</small><b class="r">' + d.failed_assets + '</b></div><div class="box stat"><small>Awaiting testing</small><b class="a">' + d.awaiting_testing + '</b></div></div>' +
        '<div class="grid k3" style="margin-top:16px"><div class="box stat"><small>Certificates generated</small><b>' + d.certificates + '</b></div><div class="box stat"><small>Jobs completed</small><b class="g">' + d.jobs_completed + '</b></div><div class="box stat"><small>Jobs open</small><b>' + d.jobs_open + '</b></div></div>' +
        '<div class="grid k2" style="margin-top:16px"><div class="box"><div class="top" style="margin:0 0 6px"><h2 style="margin:0">Assets registered per day</h2><select id="range" aria-label="Date range" style="padding:.3em .6em;border:1px solid var(--line);border-radius:8px"><option value="7">7 days</option><option value="14">14 days</option><option value="30" selected>30 days</option></select></div><div id="bars"></div></div>' +
        '<div class="box"><h2>Assets by status</h2>' + donut(d.by_status) + '</div></div>' +
        '<div class="grid k2e" style="margin-top:16px">' + (isStaff() ? '<div class="box"><h2>Technician activity</h2>' + (d.technician_activity.length ? d.technician_activity.map(function (t) { var m = d.technician_activity[0].n; return '<div class="hb"><span>' + esc(t.name) + '</span><div><i style="width:' + Math.round(100 * t.n / m) + '%"></i></div><b>' + t.n + '</b></div>'; }).join('') : '<div class="empty">No activity yet.</div>') + '</div>' +
          '<div class="box"><h2>Recent activity</h2>' + (d.recent_activity.length ? d.recent_activity.map(function (a) { return '<div style="padding:7px 0;border-bottom:1px solid #eef2f8;font-size:.86rem"><b>' + esc(a.action) + '</b> <span class="note">' + esc(a.user_name || 'system') + ' · ' + esc(a.created_at) + '</span></div>'; }).join('') : '<div class="empty">No activity yet.</div>') + '</div>' : '') + '</div>');
      chart(); var r = $('#range'); if (r) r.addEventListener('change', function () { range = +r.value; chart(); });
    });
  };

  V.jobs = function () {
    var params = hashParams();
    return api('GET', '/api/jobs' + qs({ q: params.q, status: params.status, limit: 100 })).then(function (rows) {
      shell('#/jobs', head(S.user.role === 'customer' ? 'Job progress' : 'Jobs', 'Batches of equipment from a customer', isStaff() ? '<button class="btn sm" data-act="new-job">New job</button>' : '') +
        '<div class="box"><div class="tools"><input class="grow" id="q" placeholder="Search job reference or title" value="' + esc(params.q || '') + '"><select id="st"><option value="">All statuses</option>' + ['open', 'in_progress', 'completed', 'cancelled'].map(function (s) { return '<option' + (params.status === s ? ' selected' : '') + '>' + s + '</option>'; }).join('') + '</select></div>' +
        table(['Job', 'Title', 'Customer', 'Technician', 'Assets', 'Progress', 'Status'], rows.map(function (j) { return { href: '#/jobs/' + j.id, cells: ['<b class="mono">' + esc(j.job_ref) + '</b>', esc(j.title), esc(j.customer_name || '-'), esc(j.technician_name || '-'), j.asset_count, j.passed_count + ' / ' + j.asset_count + ' passed', chip(j.status.replace('_', ' '), slug(j.status))] }; }), 'No jobs match.') + '</div>');
      bindFilters('/jobs');
    });
  };
  V.job = function (id) {
    return api('GET', '/api/jobs/' + id).then(function (d) {
      var j = d.job;
      shell('#/jobs', head(j.job_ref, j.title, isStaff() ? ['open', 'in_progress', 'completed', 'cancelled'].filter(function (s) { return s !== j.status; }).map(function (s) { return '<button class="btn sm ghost" style="color:#33507f" data-act="job-status" data-id="' + j.id + '" data-s="' + s + '">Mark ' + s.replace('_', ' ') + '</button>'; }).join('') : '') +
        '<div class="box"><div class="kv"><div><small>Status</small><b>' + chip(j.status.replace('_', ' '), slug(j.status)) + '</b></div><div><small>Due</small><b>' + esc(j.due_date || '-') + '</b></div><div><small>Notes</small><b>' + esc(j.notes || '-') + '</b></div></div>' +
        '<h3>Assets in this job</h3>' + table(['Asset ID', 'Serial', 'Model', 'Status', 'Result'], d.assets.map(function (a) { return { href: '#/assets/' + a.id, cells: ['<b class="mono">' + esc(a.asset_tag) + '</b>', '<span class="mono">' + esc(a.serial) + '</span>', esc(a.model || '-'), chip(a.status), chip(a.overall || 'INCOMPLETE')] }; }), 'No assets in this job yet.') + '</div>');
    });
  };

  var ASSET_STATUSES = ['registered', 'inspected', 'erased', 'tested', 'assessed', 'completed', 'failed', 'blocked'];
  V.assets = function () {
    var p = hashParams(), page = +(p.page || 0), LIM = 25;
    return api('GET', '/api/assets' + qs({ q: p.q, status: p.status, job_id: p.job_id, limit: LIM, offset: page * LIM })).then(function (d) {
      shell('#/assets', head('Assets', S.user.role === 'customer' ? 'Your equipment' : 'Every laptop on the platform', isStaff() ? '<button class="btn sm" data-act="new-asset">Register laptop</button>' : '') +
        '<div class="box"><div class="tools"><input class="grow" id="q" placeholder="Search asset ID, serial, make or model" value="' + esc(p.q || '') + '"><select id="st"><option value="">All statuses</option>' + ASSET_STATUSES.map(function (s) { return '<option' + (p.status === s ? ' selected' : '') + '>' + s + '</option>'; }).join('') + '</select></div>' +
        table(['Asset ID', 'Serial', 'Device', 'Customer', 'Job', 'Status', 'Result', 'Updated'], d.items.map(function (a) { return { href: '#/assets/' + a.id, cells: ['<b class="mono">' + esc(a.asset_tag) + '</b>' + (a.demo ? ' ' + chip('DEMO', 'demo') : ''), '<span class="mono">' + esc(a.serial) + '</span>', esc(((a.manufacturer || '') + ' ' + (a.model || '')).trim() || '-'), esc(a.customer_name || '-'), esc(a.job_ref || '-'), chip(a.status), chip(a.overall || 'INCOMPLETE'), '<span class="note">' + esc(a.updated_at) + '</span>'] }; }), 'No assets match.') +
        '<div class="tools" style="margin:14px 0 0;justify-content:space-between"><span class="note">' + d.total + ' assets</span><span class="actions">' + (page > 0 ? '<a class="btn sm ghost" style="color:#33507f" href="#/assets' + qs({ q: p.q, status: p.status, page: page - 1 }) + '">Previous</a>' : '') + ((page + 1) * LIM < d.total ? '<a class="btn sm ghost" style="color:#33507f" href="#/assets' + qs({ q: p.q, status: p.status, page: page + 1 }) + '">Next</a>' : '') + '</span></div></div>');
      bindFilters('/assets');
      if (p.new && isStaff()) newAsset();
    });
  };

  function stepsHtml(d) {
    var a = d.asset, ov = d.assessment.overall, er = d.erasures.some(function (e) { return e.verified; });
    var st = [['Identify', true], ['Inspect', !!d.inspection], ['Erase', er], ['Test', d.tests.some(function (t) { return t.status !== 'NOT TESTED'; })], ['Assess', ov === 'PASS' || ov === 'FAIL' || ov === 'BLOCKED'], ['Report', d.certificates.length > 0]];
    var firstOpen = st.findIndex(function (s) { return !s[1]; });
    return '<div class="steps">' + st.map(function (s, i) { var c = s[1] ? 'done' : (i === firstOpen ? 'now' : ''); if ((ov === 'FAIL' || ov === 'BLOCKED') && i === 4) c = 'bad'; return '<div class="s ' + c + '"><i>' + (s[1] ? '&#10003;' : (i + 1)) + '</i>' + s[0] + '</div>'; }).join('') + '</div>';
  }
  V.asset = function (id) {
    return api('GET', '/api/assets/' + id).then(function (d) {
      var a = d.asset, ov = d.assessment.overall, staff = isStaff(), admin = isAdmin();
      var ident = [['Manufacturer', a.manufacturer], ['Model', a.model], ['Serial number', a.serial], ['CPU', a.cpu], ['RAM (GB)', a.ram_gb], ['Storage', a.storage_summary], ['BIOS', a.bios], ['Operating system', a.os_info], ['Chassis', a.chassis]]
        .map(function (x) { return '<div><small>' + x[0] + '</small><b>' + esc(x[1] == null || x[1] === '' ? '-' : x[1]) + '</b></div>'; }).join('');
      var insp = d.inspection ? '<div class="kv"><div><small>Pre-wipe check</small><b>' + chip(d.inspection.check_result || 'NOT CHECKED', slug(d.inspection.check_result === 'CLEAR' ? 'pass' : d.inspection.check_result === 'BLOCKED' ? 'blocked' : 'warn')) + '</b></div><div><small>Absolute</small><b>' + esc(d.inspection.absolute_result || '-') + '</b></div><div><small>Enrolment</small><b>' + esc(d.inspection.enrolment_result || '-') + '</b></div><div><small>Cosmetic grade</small><b>' + esc(d.inspection.cosmetic_grade || '-') + '</b></div><div><small>Issues</small><b>' + esc((d.inspection.issues || []).join(', ') || 'none') + '</b></div><div><small>Notes</small><b>' + esc(d.inspection.notes || '-') + '</b></div></div>' : '<div class="empty">No inspection recorded yet.</div>';
      var erows = d.erasures.map(function (e) {
        var act = '';
        if (e.certificate_id) act = '<a class="btn sm" href="#/certificates/' + e.certificate_id + '">' + esc(e.certificate_number) + '</a>';
        else if (staff) act = (e.certificate_blockers.length ? '<div class="why-no">' + e.certificate_blockers.map(esc).join('<br>') + '</div>' : '') + '<button class="btn sm" data-act="issue" data-id="' + e.id + '"' + (e.certificate_blockers.length ? ' disabled' : '') + '>Issue certificate</button>';
        return { cells: ['<span class="mono">' + esc(e.device_path || '-') + '</span><br><span class="note">' + esc(e.model || '') + ' ' + esc(e.capacity_gb || '') + ' GB</span>', esc(e.method || '-') + '<br><span class="note">' + esc(e.standard || '') + '</span>', chip(e.status, e.status === 'Success' ? 'pass' : e.status === 'Failed' ? 'fail' : 'warn') + (e.demo ? ' ' + chip('DEMO', 'demo') : '') + (e.source === 'manual' ? ' ' + chip('MANUAL', 'warn') : ''), e.verified ? 'Yes' : 'No', '<span class="note">' + esc(e.finished || '-') + '</span>', act] };
      });
      var trows = d.tests.map(function (t) { return { cells: ['<b>' + esc(t.component) + '</b>', chip(t.status, slug(t.status === 'N/A' ? 'na' : t.status)), esc(t.notes || ''), '<span class="note">' + esc(t.tested_by || '') + (t.tested_at ? ' · ' + esc(t.tested_at) : '') + '</span>'] }; });
      var srows = (d.submissions || []).map(function (s) { return { cells: ['#' + s.id, '<span class="note">' + esc(s.received_at) + '</span>', chip(s.status), s.fingerprint_ok ? 'intact' : '<b style="color:var(--red-fg)">CHANGED</b>', s.signed ? (s.signature_ok ? (s.key_trusted ? chip('signed, trusted', 'pass') : chip('signed, key not trusted', 'warn')) : chip('bad signature', 'fail')) : chip('not signed', 'warn'), s.key_id ? '<span class="mono">' + esc(s.key_id) + '</span>' : '', (admin && s.signature_ok && !s.key_trusted ? '<button class="btn sm ghost" style="color:#33507f" data-act="trust" data-id="' + s.id + '">Trust this key</button>' : '') + (s.demo ? ' ' + chip('DEMO', 'demo') : '')] }; });
      shell('#/assets', (a.demo ? '<div class="banner">DEMONSTRATION asset. Results on this page are not real evidence.</div>' : '') +
        head(a.asset_tag, ((a.manufacturer || '') + ' ' + (a.model || '')).trim() + ' · ' + a.serial, '<button class="btn sm ghost" style="color:#33507f" data-act="print">Print</button>' + (staff ? '<button class="btn sm ghost" style="color:#33507f" data-act="report" data-f="pdf" data-id="' + a.id + '">Report PDF</button><button class="btn sm ghost" style="color:#33507f" data-act="report" data-f="csv" data-id="' + a.id + '">CSV</button>' : '')) +
        stepsHtml(d) +
        '<div class="result ' + ov.toLowerCase() + '"><div><small style="font-weight:700;letter-spacing:.1em">OVERALL RESULT (CALCULATED)</small><br><b>' + ov + '</b></div><div style="flex:1">' + (d.assessment.reasons.length ? '<ul>' + d.assessment.reasons.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ul>' : 'Every check passed. Wipe verified. Every fitted part tested.') + '</div>' + (staff ? '<button class="btn sm ghost" style="color:inherit" data-act="assess" data-id="' + a.id + '">Recalculate</button>' : '') + '</div>' +
        '<div class="box"><h2>Asset</h2><div class="kv">' + ident + '<div><small>Customer</small><b>' + esc(d.customer ? d.customer.name : '-') + '</b></div><div><small>Job</small><b>' + (d.job ? '<a href="#/jobs/' + d.job.id + '">' + esc(d.job.job_ref) + '</a>' : '-') + '</b></div><div><small>Technician</small><b>' + esc(d.technician_name || '-') + '</b></div></div></div>' +
        '<div class="box" style="margin-top:16px"><div class="top" style="margin:0 0 8px"><h2 style="margin:0">Step 2 · Initial inspection</h2>' + (staff ? '<button class="btn sm" data-act="inspection" data-id="' + a.id + '">Record inspection</button>' : '') + '</div>' + insp + '</div>' +
        '<div class="box" style="margin-top:16px"><div class="top" style="margin:0 0 8px"><h2 style="margin:0">Step 3 · Data erasure</h2><span class="actions">' + (staff && S.env !== 'production' ? '<button class="btn sm ghost" style="color:#33507f" data-act="simulate" data-id="' + a.id + '">Simulate engine (dev)</button>' : '') + (staff ? '<button class="btn sm ghost" style="color:#33507f" data-act="manual-erasure" data-id="' + a.id + '">Note an outside wipe</button>' : '') + '</span></div>' +
        table(['Drive', 'Method', 'Result', 'Verified', 'Finished (Dublin)', 'Certificate'], erows, 'No erasure recorded. Erasure results arrive from the engine; they cannot be typed in as verified.') +
        '<div class="note">Only an intact engine report can create a verified erasure. A manually noted wipe is stored as NOT verified and can never receive a certificate.</div></div>' +
        '<div class="box" style="margin-top:16px"><div class="top" style="margin:0 0 8px"><h2 style="margin:0">Step 4 · Hardware tests</h2>' + (staff ? '<button class="btn sm" data-act="test" data-id="' + a.id + '">Record test result</button>' : '') + '</div>' + table(['Component', 'Result', 'Notes', 'Tested'], trows) + '</div>' +
        (staff ? '<div class="box" style="margin-top:16px"><h2>Engine reports received for this asset</h2>' + table(['#', 'Received (Dublin)', 'Status', 'Content', 'Signature', 'Key', ''], srows, 'No engine report yet.') + '</div>' : ''));
    });
  };

  V.certificates = function () {
    var p = hashParams();
    return api('GET', '/api/certificates' + qs({ q: p.q, limit: 100 })).then(function (rows) {
      shell('#/certificates', head('Certificates', 'Signed, locked and publicly verifiable') + '<div class="box"><div class="tools"><input class="grow" id="q" placeholder="Search by certificate number, serial or customer" value="' + esc(p.q || '') + '"></div>' +
        table(['Number', 'Asset', 'Device', 'Customer', 'Method', 'Issued (Dublin)', 'State', ''], rows.map(function (c) { return { cells: ['<b class="mono">' + esc(c.number) + '</b>', '<a href="#/assets/' + c.asset_id + '">' + esc(c.asset_tag) + '</a>', esc(c.device), esc(c.customer || '-'), esc(c.method), '<span class="note">' + esc(c.issued_at) + '</span>', chip(c.state), '<span class="actions"><a class="btn sm" target="_blank" rel="noopener" href="/api/certificates/' + c.id + '/pdf">PDF</a><button class="btn sm ghost" style="color:#33507f" data-act="print-cert" data-id="' + c.id + '">Print</button><a class="btn sm ghost" style="color:#33507f" target="_blank" rel="noopener" href="/verify/' + encodeURIComponent(c.number) + '">Verify</a>' + (isAdmin() && c.state !== 'REVOKED' ? '<button class="btn sm danger" data-act="revoke" data-id="' + c.id + '">Revoke</button>' : '') + '</span>'] }; }), 'No certificates yet. A certificate is issued from a verified erasure on an asset page.') + '</div>');
      bindFilters('/certificates');
    });
  };
  V.certificate = function (id) {
    return api('GET', '/api/certificates/' + id).then(function (c) {
      var p = c.payload;
      shell('#/certificates', head(c.number, 'Certificate of data erasure', '<a class="btn sm" target="_blank" rel="noopener" href="/api/certificates/' + c.id + '/pdf">Open PDF</a><button class="btn sm ghost" style="color:#33507f" data-act="print-cert" data-id="' + c.id + '">Print</button>') +
        '<div class="result ' + (c.state === 'VALID' ? 'pass' : c.state === 'DEMONSTRATION' ? 'incomplete' : 'fail') + '"><b>' + esc(c.state) + '</b><div>Re-checked just now: content unchanged and signed by this platform.</div></div>' +
        '<div class="box"><div class="kv"><div><small>Customer</small><b>' + esc(p.customer || '-') + '</b></div><div><small>Job</small><b>' + esc(p.job_ref) + '</b></div><div><small>Issued (Dublin)</small><b>' + esc(p.issued_at_dublin) + '</b></div><div><small>Device</small><b>' + esc(c.device) + '</b></div><div><small>Serial</small><b>' + esc(p.asset.serial) + '</b></div><div><small>Asset ID</small><b>' + esc(p.asset.tag) + '</b></div><div><small>Drive</small><b>' + esc((p.storage.model || '') + ' ' + (p.storage.device || '')) + '</b></div><div><small>Method</small><b>' + esc(p.erasure.method) + '</b></div><div><small>Verification</small><b>' + esc(p.erasure.status) + '</b></div></div>' +
        '<h3>Signature</h3><p class="mono" style="word-break:break-all">key ' + esc(c.key_id) + '<br>content SHA-256 ' + esc(c.payload_sha256) + '<br>signature ' + esc(c.signature) + '</p><p class="note">Public link: <a href="/verify/' + encodeURIComponent(c.number) + '" target="_blank" rel="noopener">' + esc(location.origin + '/verify/' + c.number) + '</a></p></div>');
    });
  };

  V.reports = function () {
    return Promise.all([api('GET', '/api/report-types'), api('GET', '/api/reports?limit=50')]).then(function (x) {
      var types = x[0], hist = x[1];
      shell('#/reports', head('Reports', 'Generate, export and keep every report') +
        '<div class="grid k3">' + types.map(function (t) { return '<div class="box"><h2>' + esc(t.title) + '</h2><p class="note" style="margin:0 0 12px">' + esc(t.desc) + '</p>' + (t.type === 'laptop_report' ? '<a class="btn sm" href="#/assets">Choose an asset</a>' : '<div class="actions"><button class="btn sm" data-act="gen" data-t="' + t.type + '" data-f="pdf">PDF</button><button class="btn sm ghost" style="color:#33507f" data-act="gen" data-t="' + t.type + '" data-f="csv">CSV</button></div>') + '</div>'; }).join('') + '</div>' +
        '<div class="box" style="margin-top:16px"><h2>Report history</h2><div class="tools"><input class="grow" id="q" placeholder="Search reports" value="' + esc(hashParams().q || '') + '"></div>' +
        table(['Report', 'File', 'By', 'Created (Dublin)', 'SHA-256', ''], hist.map(function (r) { return { cells: ['<b>' + esc(r.title) + '</b>', '<span class="mono">' + esc(r.file_name) + '</span>', esc(r.generated_by || ''), '<span class="note">' + esc(r.created_at) + '</span>', '<span class="mono note">' + esc(r.sha256.slice(0, 12)) + '…</span>', '<a class="btn sm" target="_blank" rel="noopener" href="/api/reports/' + r.id + '/download">Download</a>'] }; }), 'No reports generated yet.') + '</div>');
      bindFilters('/reports');
    });
  };

  V.customers = function () {
    return api('GET', '/api/customers').then(function (rows) {
      shell('#/customers', head('Customers', 'People and companies whose equipment you process', '<button class="btn sm" data-act="new-customer">New customer</button>') + '<div class="box">' +
        table(['Customer', 'Contact', 'E-mail', 'Phone', 'Assets', ''], rows.map(function (c) { return { cells: ['<b>' + esc(c.name) + '</b>', esc(c.contact_name || '-'), esc(c.email || '-'), esc(c.phone || '-'), '<a href="#/assets?customer=' + c.id + '">' + c.asset_count + '</a>', isAdmin() ? '<button class="btn sm ghost" style="color:#33507f" data-act="edit-customer" data-id="' + c.id + '">Edit</button>' : ''] }; }), 'No customers yet.') + '</div>');
      S.cache.customers = rows;
    });
  };

  V.team = function () {
    return Promise.all([api('GET', '/api/users'), api('GET', '/api/customers').catch(function () { return []; })]).then(function (x) {
      S.cache.customers = x[1];
      shell('#/team', head('Team and logins', 'Technicians, administrators and customer logins', '<button class="btn sm" data-act="invite">Invite user</button>') + '<div class="box">' +
        table(['Name', 'E-mail', 'Role', 'Company', 'Last sign-in', 'Status', ''], x[0].map(function (u) { return { cells: ['<b>' + esc(u.name) + '</b>', esc(u.email), esc(u.role.replace('_', ' ')), esc(u.company_name || '-'), '<span class="note">' + esc(u.last_login || 'never') + '</span>', u.active ? (u.email_verified ? chip('active', 'pass') : chip('invited', 'warn')) : chip('disabled', 'fail'), u.id === S.user.id ? '' : '<button class="btn sm ghost" style="color:#33507f" data-act="toggle-user" data-id="' + u.id + '" data-a="' + (u.active ? 0 : 1) + '">' + (u.active ? 'Disable' : 'Enable') + '</button>'] }; }), 'No users.') + '</div>');
    });
  };

  V.settings = function () {
    var cid = hashParams().company || '';
    return api('GET', '/api/companies').then(function (cos) {
      var co = cos.filter(function (c) { return String(c.id) === String(cid); })[0] || cos[0];
      if (!co) { shell('#/settings', head('Settings') + '<div class="box"><div class="empty">Create a company first.</div></div>'); return; }
      var q = '?company_id=' + co.id;
      return Promise.all([api('GET', '/api/stations'), api('GET', '/api/keys')]).then(function (y) {
        var stations = y[0].filter(function (s) { return s.company_id === co.id; }), keys = y[1].filter(function (k) { return k.company_id === co.id; });
        S.cache.co = co;
        shell('#/settings', head('Settings', co.name) + (isSuper() && cos.length > 1 ? '<div class="tools"><select id="coSel">' + cos.map(function (c) { return '<option value="' + c.id + '"' + (c.id === co.id ? ' selected' : '') + '>' + esc(c.name) + '</option>'; }).join('') + '</select></div>' : '') +
          '<div class="box"><h2>Company details and branding</h2><form id="coForm"><div class="two">' + fld('Company name', 'name', { value: co.name, required: true }) + fld('Certificate prefix (2 to 8 letters)', 'cert_prefix', { value: co.cert_prefix, max: 8 }) + fld('Address', 'address', { value: co.address }) + fld('E-mail', 'email', { value: co.email }) + fld('Phone', 'phone', { value: co.phone }) + fld('Website', 'website', { value: co.website }) + fld('Accent colour', 'accent_color', { value: co.accent_color, type: 'color' }) +
          '<div class="field"><label for="logo">Logo (PNG or JPEG, under 150 KB)</label><input id="logo" type="file" accept="image/png,image/jpeg"></div></div>' + (co.logo_data ? '<p><img src="' + esc(co.logo_data) + '" alt="Company logo" style="max-height:56px;background:#07142b;padding:6px;border-radius:8px"> <button type="button" class="btn sm ghost" style="color:#33507f" id="rmLogo">Remove logo</button></p>' : '') +
          '<label style="display:flex;gap:.6rem;align-items:flex-start;margin:6px 0 14px"><input type="checkbox" name="require_signed"' + (co.require_signed ? ' checked' : '') + ' style="margin-top:.3rem"><span><b>Require a trusted signature for certificates</b><br><span class="note">Recommended. When on, a certificate needs an engine report signed by a key listed below.</span></span></label><div class="msg err" id="coMsg" hidden></div><button class="btn" type="submit">Save settings</button></form></div>' +
          '<div class="grid k2e" style="margin-top:16px"><div class="box"><div class="top" style="margin:0 0 8px"><h2 style="margin:0">Stations (test benches)</h2><button class="btn sm" data-act="new-station">New station</button></div><p class="note" style="margin-top:0">A station pushes finished engine reports with its own secret token.</p>' +
          table(['Name', 'Token', 'Last seen', ''], stations.map(function (s) { return { cells: [esc(s.name), '<span class="mono">' + esc(s.token_prefix) + '…</span>', '<span class="note">' + esc(s.last_seen || 'never') + '</span>', s.active ? '<button class="btn sm danger" data-act="off-station" data-id="' + s.id + '">Disable</button>' : chip('disabled', 'fail')] }; }), 'No stations yet.') + '</div>' +
          '<div class="box"><div class="top" style="margin:0 0 8px"><h2 style="margin:0">Trusted signing keys</h2><button class="btn sm" data-act="new-key">Add key</button></div><p class="note" style="margin-top:0">Public keys from <span class="mono">itad_signing_public.pem</span> on your ITAD_DATA sticks.</p>' +
          table(['Key ID', 'Label', 'Added', ''], keys.map(function (k) { return { cells: ['<span class="mono">' + esc(k.key_id) + '</span>', esc(k.label || ''), '<span class="note">' + esc(k.created_at) + '</span>', k.active ? '<button class="btn sm danger" data-act="off-key" data-id="' + k.id + '">Remove</button>' : chip('removed', 'fail')] }; }), 'No trusted keys yet.') + '</div></div>');
        var sel = $('#coSel'); if (sel) sel.addEventListener('change', function () { go('#/settings?company=' + sel.value); });
        var logoData;
        $('#logo').addEventListener('change', function (e) { var f = e.target.files[0]; if (!f) return; if (f.size > 150000) { toast('Logo is too large (max 150 KB).', 'err'); e.target.value = ''; return; } var r = new FileReader(); r.onload = function () { logoData = r.result; }; r.readAsDataURL(f); });
        var rm = $('#rmLogo'); if (rm) rm.addEventListener('click', function () { api('PUT', '/api/companies/' + co.id, { logo_data: '' }).then(function () { toast('Logo removed.'); route(); }); });
        $('#coForm').addEventListener('submit', function (e) {
          e.preventDefault(); var d = {}; new FormData(e.target).forEach(function (v, k) { d[k] = v; }); d.require_signed = e.target.require_signed.checked; if (logoData) d.logo_data = logoData;
          api('PUT', '/api/companies/' + co.id, d).then(function () { toast('Settings saved.'); route(); }, function (x) { var m = $('#coMsg'); m.textContent = x.message; m.hidden = false; });
        });
      });
    });
  };

  V.companies = function () {
    return api('GET', '/api/companies').then(function (rows) {
      shell('#/companies', head('Companies', 'Every company on the platform', '<button class="btn sm" data-act="new-company">New company</button>') + '<div class="box">' + table(['Company', 'Prefix', 'Created', 'Status', ''], rows.map(function (c) { return { cells: ['<b>' + esc(c.name) + '</b>', '<span class="mono">' + esc(c.cert_prefix) + '</span>', '<span class="note">' + esc(c.created_at.slice(0, 10)) + '</span>', c.active ? chip('active', 'pass') : chip('disabled', 'fail'), '<span class="actions"><a class="btn sm ghost" style="color:#33507f" href="#/settings?company=' + c.id + '">Settings</a><button class="btn sm ghost" style="color:#33507f" data-act="toggle-company" data-id="' + c.id + '" data-a="' + (c.active ? 0 : 1) + '">' + (c.active ? 'Disable' : 'Enable') + '</button></span>'] }; }), 'No companies.') + '</div>');
    });
  };

  V.submissions = function () {
    return api('GET', '/api/submissions?limit=100').then(function (rows) {
      shell('#/submissions', head('Engine inbox', 'Reports received from test benches (kept as evidence)', '<button class="btn sm" data-act="upload-report">Upload a report file</button>') + '<div class="box">' +
        table(['#', 'Received (Dublin)', 'Job', 'Asset', 'Serial', 'Station', 'Status', 'Content', 'Signature', 'Reason'], rows.map(function (s) { return { cells: ['#' + s.id, '<span class="note">' + esc(s.received_at) + '</span>', esc(s.job_ref), esc(s.asset_tag), '<span class="mono">' + esc(s.serial) + '</span>', esc(s.station || 'upload'), chip(s.status), s.fingerprint_ok ? 'intact' : '<b style="color:var(--red-fg)">CHANGED</b>', s.signed ? (s.signature_ok ? (s.key_trusted ? chip('trusted', 'pass') : chip('key not trusted', 'warn')) : chip('bad', 'fail')) : chip('unsigned', 'warn'), '<span class="note">' + esc(s.reason || '') + '</span>'] }; }), 'No reports received yet.') + '</div>');
    });
  };

  V.audit = function () {
    return api('GET', '/api/audit?limit=200').then(function (rows) {
      shell('#/audit', head('Audit log', 'Append-only and hash-chained', '<button class="btn sm" data-act="verify-audit">Verify chain</button>') + '<div class="box">' +
        table(['When (Dublin)', 'User', 'Action', 'Entity', 'Detail', 'IP'], rows.map(function (r) { return { cells: ['<span class="note">' + esc(r.created_at) + '</span>', esc(r.user_name || 'system'), '<b>' + esc(r.action) + '</b>', esc((r.entity || '') + ' ' + (r.entity_id || '')), '<span class="mono note">' + esc(JSON.stringify(r.detail)).slice(0, 160) + '</span>', '<span class="note">' + esc(r.ip || '') + '</span>'] }; }), 'Nothing logged yet.') + '</div>');
    });
  };

  V.profile = function () {
    shell('#/profile', head('Your profile') + '<div class="box" style="max-width:520px"><form id="pf">' + fld('Name', 'name', { value: S.user.name, required: true }) + '<div class="field"><label>E-mail</label><input value="' + esc(S.user.email) + '" disabled></div><h3>Change password</h3>' + fld('Current password', 'current_password', { type: 'password' }) + fld('New password', 'new_password', { type: 'password' }) + '<div class="msg err" id="pm" hidden></div><button class="btn" type="submit">Save</button></form></div>');
    $('#pf').addEventListener('submit', function (e) {
      e.preventDefault(); var d = {}; new FormData(e.target).forEach(function (v, k) { if (v) d[k] = v; });
      api('PUT', '/api/auth/profile', d).then(function (j) { S.user = j.user; toast('Profile saved.'); route(); }, function (x) { var m = $('#pm'); m.textContent = x.message; m.hidden = false; });
    });
    return Promise.resolve();
  };

  // ------------------------------------------------------------ forms
  function hashParams() { var o = {}, q = (location.hash.split('?')[1] || ''); q.split('&').forEach(function (p) { if (p) { var kv = p.split('='); o[kv[0]] = decodeURIComponent(kv[1] || ''); } }); return o; }
  function bindFilters(base) {
    var q = $('#q'), st = $('#st');
    function apply() { var p = hashParams(); p.q = q ? q.value : ''; if (st) p.status = st.value; delete p.page; go('#' + base + qs(p)); }
    var t; if (q) q.addEventListener('input', function () { clearTimeout(t); t = setTimeout(apply, 350); });
    if (st) st.addEventListener('change', apply);
    if (q && document.activeElement !== q && hashParams().q) { /* keep focus out */ }
  }
  function options(rows, label, empty) { return [['', empty || '(none)']].concat(rows.map(function (r) { return [r.id, label(r)]; })); }
  function newJob() {
    Promise.all([api('GET', '/api/customers'), api('GET', '/api/technicians')]).then(function (x) {
      modal('New job', '<div class="two">' + fld('Title', 'title', { required: true, max: 200 }) + fld('Job reference (optional)', 'job_ref', { ph: 'auto', max: 60 }) + fld('Customer', 'customer_id', { options: options(x[0], function (c) { return c.name; }) }) + (S.user.role === 'technician' ? '' : fld('Technician', 'technician_id', { options: options(x[1], function (t) { return t.name; }) })) + fld('Due date', 'due_date', { type: 'date' }) + '</div>' + fld('Notes', 'notes', { area: true }), function (d, close) {
        if (isSuper()) d.company_id = (S.cache.co || {}).id;
        Object.keys(d).forEach(function (k) { if (d[k] === '') delete d[k]; });
        return api('POST', '/api/jobs', d).then(function (j) { close(); toast('Job ' + j.job_ref + ' created.'); go('#/jobs/' + j.id); });
      }, 'Create job');
    });
  }
  function newAsset() {
    api('GET', '/api/jobs?limit=200&all=1').then(function (jobs) {
      modal('Register a laptop', '<div class="two">' + fld('Asset ID (tag)', 'asset_tag', { required: true, max: 60 }) + fld('Serial number', 'serial', { required: true, max: 80 }) + fld('Job', 'job_id', { options: options(jobs, function (j) { return j.job_ref + ' · ' + j.title; }) }) + fld('Manufacturer', 'manufacturer') + fld('Model', 'model') + fld('CPU', 'cpu') + fld('RAM (GB)', 'ram_gb', { type: 'number' }) + fld('Storage', 'storage_summary', { ph: 'e.g. 256 GB NVMe SSD' }) + fld('BIOS', 'bios') + fld('Operating system', 'os_info') + '</div><p class="note">Normally the engine reads these from the laptop and registers it for you. Typing them in is for laptops registered before testing.</p>', function (d, close) {
        Object.keys(d).forEach(function (k) { if (d[k] === '') delete d[k]; });
        return api('POST', '/api/assets', d).then(function (a) { close(); toast(a.warning || 'Laptop registered.', a.warning ? 'info' : 'ok'); go('#/assets/' + a.id); });
      }, 'Register', true);
    });
  }
  var COMPONENTS = ['CPU', 'RAM', 'Drive Health', 'Battery', 'Wi-Fi', 'Ethernet', 'Bluetooth', 'Webcam', 'USB', 'Screen', 'Keyboard', 'Touchpad', 'Touchscreen', 'Speakers', 'Microphone'];
  var ACTIONS = {
    logout: function () { api('POST', '/api/auth/logout').then(function () { S.user = null; go('#/login'); }); },
    menu: function () { $('#side').classList.toggle('open'); },
    print: function () { window.print(); },
    'new-job': newJob, 'new-asset': newAsset,
    'job-status': function (b) { api('PUT', '/api/jobs/' + b.dataset.id, { status: b.dataset.s }).then(function () { toast('Job updated.'); route(); }, function (e) { toast(e.message, 'err'); }); },
    assess: function (b) { api('POST', '/api/assets/' + b.dataset.id + '/assess').then(function () { toast('Result recalculated.'); route(); }); },
    issue: function (b) { api('POST', '/api/erasures/' + b.dataset.id + '/certificate').then(function (c) { toast('Certificate ' + c.number + ' issued.'); route(); }, function (e) { toast(e.message, 'err'); }); },
    report: function (b) { api('POST', '/api/reports', { type: 'laptop_report', format: b.dataset.f, params: { asset_id: +b.dataset.id } }).then(function (r) { download('/api/reports/' + r.id + '/download'); toast('Report created.'); }, function (e) { toast(e.message, 'err'); }); },
    simulate: function (b) {
      modal('Simulate the engine (development only)', '<p class="note">Sends a clearly labelled DEMONSTRATION report through the same import path a real station uses.</p>' + fld('Scenario', 'scenario', { options: [['pass', 'Everything passes'], ['fail', 'Battery fails'], ['incomplete', 'Some tests not done'], ['blocked', 'Absolute lock found (blocked)']] }), function (d, close) {
        d.asset_id = b.dataset.id; return api('POST', '/api/dev/simulate-engine', d).then(function () { close(); toast('Simulated report imported.'); route(); });
      }, 'Run simulation');
    },
    inspection: function (b) {
      modal('Record inspection', '<p class="note">Check results (CLEAR) come from the engine. A person can only mark a laptop BLOCKED.</p><div class="two">' + fld('Mark as blocked?', 'check_result', { options: [['', 'No, leave as is'], ['BLOCKED', 'Yes: lock found, BLOCKED']] }) + fld('Cosmetic grade', 'cosmetic_grade', { options: [['', '-'], ['A', 'A: like new'], ['B', 'B: light wear'], ['C', 'C: visible wear'], ['D', 'D: heavy wear']] }) + '</div>' + fld('Issues (comma separated)', 'issues', { ph: 'Light scratches on the lid, sticker residue' }) + fld('Notes', 'notes', { area: true }), function (d, close) {
        d.issues = (d.issues || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean); Object.keys(d).forEach(function (k) { if (d[k] === '') delete d[k]; });
        return api('POST', '/api/assets/' + b.dataset.id + '/inspection', d).then(function () { close(); toast('Inspection recorded.'); route(); });
      });
    },
    test: function (b) {
      modal('Record a test result', '<div class="two">' + fld('Component', 'component', { options: COMPONENTS.map(function (c) { return [c, c]; }) }) + fld('Result', 'status', { options: [['PASS', 'PASS'], ['FAIL', 'FAIL'], ['WARN', 'WARN (needs checking)'], ['NOT TESTED', 'NOT TESTED'], ['N/A', 'N/A (part not fitted)']] }) + '</div>' + fld('Notes', 'notes', { max: 500 }), function (d, close) {
        return api('POST', '/api/assets/' + b.dataset.id + '/tests', d).then(function () { close(); toast('Test recorded.'); route(); });
      });
    },
    'manual-erasure': function (b) {
      modal('Note an outside wipe', '<div class="msg info">This records that someone says a wipe was done elsewhere. It is stored as <b>NOT verified</b> and can never receive a certificate.</div>' + fld('Method used', 'method', { required: true, max: 200 }) + '<div class="two">' + fld('Drive', 'device_path') + fld('Drive serial', 'serial') + '</div>', function (d, close) {
        return api('POST', '/api/assets/' + b.dataset.id + '/erasures', d).then(function () { close(); toast('Noted as not verified.', 'info'); route(); });
      }, 'Note it');
    },
    trust: function (b) {
      modal('Trust this signing key?', '<p>You are saying that reports signed with this key come from one of your own test benches. Certificates for those reports can then be issued.</p>' + fld('Label', 'label', { ph: 'e.g. Bench 1 USB' }), function (d, close) {
        return api('POST', '/api/keys/from-submission/' + b.dataset.id, d).then(function () { close(); toast('Key trusted.'); route(); });
      }, 'Trust key');
    },
    'print-cert': function (b) { var w = window.open('/api/certificates/' + b.dataset.id + '/pdf', '_blank', 'noopener'); if (!w) toast('Allow pop-ups to print.', 'info'); },
    revoke: function (b) {
      modal('Revoke certificate', '<div class="msg info">The certificate stays on record but shows REVOKED on the verification page.</div>' + fld('Reason', 'reason', { required: true, max: 300 }), function (d, close) {
        return api('POST', '/api/certificates/' + b.dataset.id + '/revoke', d).then(function () { close(); toast('Certificate revoked.'); route(); });
      }, 'Revoke');
    },
    gen: function (b) {
      var t = b.dataset.t, f = b.dataset.f, extra = '';
      if (t === 'monthly_processing') extra = fld('Month', 'month', { type: 'month', required: true, value: new Date().toISOString().slice(0, 7) });
      else extra = '<div class="two">' + fld('From (optional)', 'from', { type: 'date' }) + fld('To (optional)', 'to', { type: 'date' }) + '</div>';
      modal('Generate report (' + f.toUpperCase() + ')', extra + (isSuper() ? fld('Company ID', 'company_id', { type: 'number', required: true }) : ''), function (d, close) {
        Object.keys(d).forEach(function (k) { if (d[k] === '') delete d[k]; });
        return api('POST', '/api/reports', { type: t, format: f, params: d }).then(function (r) { close(); download('/api/reports/' + r.id + '/download'); toast('Report created.'); route(); });
      }, 'Generate');
    },
    'new-customer': function () {
      modal('New customer', fld('Name', 'name', { required: true }) + '<div class="two">' + fld('Contact', 'contact_name') + fld('E-mail', 'email') + fld('Phone', 'phone') + fld('Address', 'address') + '</div>', function (d, close) {
        if (isSuper()) d.company_id = (S.cache.co || {}).id; return api('POST', '/api/customers', d).then(function () { close(); toast('Customer created.'); route(); });
      });
    },
    'edit-customer': function (b) {
      var c = (S.cache.customers || []).filter(function (x) { return String(x.id) === b.dataset.id; })[0] || {};
      modal('Edit customer', fld('Name', 'name', { required: true, value: c.name }) + '<div class="two">' + fld('Contact', 'contact_name', { value: c.contact_name }) + fld('E-mail', 'email', { value: c.email }) + fld('Phone', 'phone', { value: c.phone }) + fld('Address', 'address', { value: c.address }) + '</div>', function (d, close) {
        return api('PUT', '/api/customers/' + c.id, d).then(function () { close(); toast('Saved.'); route(); });
      });
    },
    invite: function () {
      var roles = [['technician', 'Technician'], ['company_admin', 'Company administrator'], ['customer', 'Customer (read-only portal)']].concat(isSuper() ? [['super_admin', 'Super administrator']] : []);
      modal('Invite a user', '<div class="two">' + fld('Name', 'name', { required: true }) + fld('E-mail', 'email', { type: 'email', required: true }) + fld('Role', 'role', { options: roles }) + fld('Customer (for customer logins)', 'customer_id', { options: options(S.cache.customers || [], function (c) { return c.name; }) }) + (isSuper() ? fld('Company ID (not for super admins)', 'company_id', { type: 'number' }) : '') + '</div><p class="note">The user receives an e-mail link to choose their own password.</p>', function (d, close) {
        Object.keys(d).forEach(function (k) { if (d[k] === '') delete d[k]; });
        return api('POST', '/api/users', d).then(function (r) { close(); toast('Invitation sent' + (r.dev_token ? ' (dev link: #/reset?token=' + r.dev_token + ')' : '') + '.'); route(); });
      }, 'Send invitation');
    },
    'toggle-user': function (b) { api('PUT', '/api/users/' + b.dataset.id, { active: b.dataset.a === '1' }).then(function () { route(); }, function (e) { toast(e.message, 'err'); }); },
    'new-station': function () {
      modal('New station', fld('Name', 'name', { required: true, ph: 'Bench 1' }), function (d, close) {
        d.company_id = (S.cache.co || {}).id; return api('POST', '/api/stations', d).then(function (r) {
          close(); modal('Station token', '<div class="msg ok">Copy this token now. It is shown only once.</div><p class="mono" style="word-break:break-all;background:#f3f6fb;padding:10px;border-radius:8px">' + esc(r.token) + '</p><p class="note">On the station set <span class="mono">ITAD_PLATFORM_TOKEN</span> to this value (see integrations/README.md).</p>', null); route();
        });
      }, 'Create');
    },
    'off-station': function (b) { api('DELETE', '/api/stations/' + b.dataset.id).then(function () { route(); }); },
    'new-key': function () {
      modal('Add a trusted signing key', '<p class="note">Paste the contents of <span class="mono">itad_signing_public.pem</span> from your ITAD_DATA stick (or the 64-character hex key).</p>' + fld('Public key', 'public_key', { area: true, required: true }) + fld('Label', 'label', { ph: 'Bench 1 USB' }), function (d, close) {
        d.company_id = (S.cache.co || {}).id; return api('POST', '/api/keys', d).then(function () { close(); toast('Key trusted.'); route(); });
      }, 'Trust key');
    },
    'off-key': function (b) { api('DELETE', '/api/keys/' + b.dataset.id).then(function () { route(); }); },
    'new-company': function () { modal('New company', fld('Company name', 'name', { required: true }), function (d, close) { return api('POST', '/api/companies', d).then(function () { close(); toast('Company created.'); route(); }); }, 'Create'); },
    'toggle-company': function (b) { api('PUT', '/api/companies/' + b.dataset.id, { active: b.dataset.a === '1' }).then(function () { route(); }); },
    'verify-audit': function () { api('GET', '/api/audit/verify').then(function (r) { toast(r.intact ? 'Audit chain is intact.' : 'AUDIT CHAIN BROKEN at row ' + r.first_bad_row, r.intact ? 'ok' : 'err'); }); },
    'upload-report': function () {
      modal('Upload an engine report', '<p class="note">Choose the signed <span class="mono">.json</span> report from the USB stick.</p><div class="field"><label for="rf">Report file</label><input id="rf" type="file" accept=".json,application/json" required></div>' + (isSuper() ? fld('Company ID', 'company_id', { type: 'number', required: true }) : ''), function (d, close) {
        var f = $('#rf').files[0]; if (!f) throw new Error('Choose a file.');
        return f.text().then(function (t) { var rep; try { rep = JSON.parse(t); } catch (e) { throw new Error('That file is not valid JSON.'); } var body = { report: rep }; if (d.company_id) body.company_id = d.company_id; return api('POST', '/api/submissions', body); }).then(function (r) { close(); toast(r.ok ? 'Report accepted (' + r.status + ').' : 'Report rejected: ' + r.reason, r.ok ? 'ok' : 'err'); route(); }, function (x) { if (x.data && x.data.reason) { close(); toast('Report rejected: ' + x.data.reason, 'err'); route(); return; } throw x; });
      }, 'Upload');
    }
  };

  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-act]');
    if (b && ACTIONS[b.dataset.act]) { e.preventDefault(); ACTIONS[b.dataset.act](b); return; }
    var tr = e.target.closest('tr[data-href]');
    if (tr && !e.target.closest('a,button')) go(tr.dataset.href);
  });

  // ------------------------------------------------------------ router
  function route() {
    var h = location.hash || '#/dashboard', path = h.split('?')[0], m;
    if (path === '#/forgot') return viewForgot();
    if (path === '#/reset') return viewReset();
    if (path === '#/verify') return viewVerifyEmail();
    if (!S.user) return path === '#/login' || !S.checked ? viewLogin() : viewLogin();
    if (path === '#/login') return go('#/dashboard');
    var run;
    if (path === '#/dashboard') run = V.dashboard();
    else if (path === '#/jobs') run = V.jobs();
    else if ((m = path.match(/^#\/jobs\/(\d+)$/))) run = V.job(m[1]);
    else if (path === '#/assets') run = V.assets();
    else if ((m = path.match(/^#\/assets\/(\d+)$/))) run = V.asset(m[1]);
    else if (path === '#/certificates') run = V.certificates();
    else if ((m = path.match(/^#\/certificates\/(\d+)$/))) run = V.certificate(m[1]);
    else if (path === '#/reports') run = V.reports();
    else if (path === '#/customers' && isStaff()) run = V.customers();
    else if (path === '#/team' && isAdmin()) run = V.team();
    else if (path === '#/settings' && isAdmin()) run = V.settings();
    else if (path === '#/companies' && isSuper()) run = V.companies();
    else if (path === '#/submissions' && isStaff()) run = V.submissions();
    else if (path === '#/audit' && isAdmin()) run = V.audit();
    else if (path === '#/profile') run = V.profile();
    else return go('#/dashboard');
    Promise.resolve(run).catch(function (e) {
      if (e.status === 401) return;
      shell(path, '<div class="box"><div class="msg err">' + esc(e.message) + '</div><a class="btn" href="#/dashboard">Back to the dashboard</a></div>');
    });
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', route);

  // customer filter on assets list uses ?customer= (kept simple: map to customer_id)
  var origAssets = V.assets;
  V.assets = function () { var p = hashParams(); if (p.customer && !p.customer_id) { location.replace('#/assets' + qs({ customer_id: p.customer })); } return origAssets(); };

  // ------------------------------------------------------------ start
  fetch('/api/health').then(function (r) { return r.json(); }).then(function (h) { S.env = h.env; }).catch(function () { })
    .then(function () { return fetch('/api/auth/me', { credentials: 'same-origin' }).then(function (r) { return r.json(); }); })
    .then(function (j) { S.user = j.user; S.csrf = j.csrf; S.checked = true; if (!S.user && !/^#\/(forgot|reset|verify)/.test(location.hash)) location.hash = '#/login'; route(); });
})();
