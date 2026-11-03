// Public certificate verification page. Shows only what is safe to show to anyone.
(function () {
  'use strict';
  var out = document.getElementById('out'), input = document.getElementById('num');
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function row(k, v) { return '<div><small>' + esc(k) + '</small><b>' + esc(v || '-') + '</b></div>'; }
  function check(num) {
    num = (num || '').trim();
    if (!num) return;
    out.innerHTML = '<p style="color:#9db7e0">Checking…</p>';
    fetch('/api/public/verify/' + encodeURIComponent(num), { credentials: 'omit' }).then(function (r) { return r.json().then(function (j) { return { s: r.status, j: j }; }); }).then(function (r) {
      var j = r.j;
      if (!j.found) { out.innerHTML = '<div class="msg err">No certificate with that number was found. Check the number and try again.</div>'; return; }
      var cls = { VALID: 'ok', DEMONSTRATION: 'info', REVOKED: 'err', INVALID: 'err' }[j.state] || 'err';
      var text = { VALID: 'This certificate is genuine and unchanged.', DEMONSTRATION: 'Demonstration certificate. It is not a record of a real erasure.', REVOKED: 'This certificate was revoked: ' + (j.revoked_reason || ''), INVALID: 'WARNING: this certificate failed its integrity check.' }[j.state];
      out.innerHTML = '<div class="msg ' + cls + '" style="font-weight:700">' + esc(j.state) + ': ' + esc(text) + '</div><div class="kv" style="grid-template-columns:1fr 1fr;color:#0b1f40">' +
        row('Certificate', j.number) + row('Issued (Dublin)', j.issued) + row('Issued by', j.company) + row('Device', j.device) + row('Serial', j.serial_masked) +
        row('Method', j.method) + row('Standard', j.standard) + row('Verified', j.verified ? 'Yes, by read-back' : 'No') + '</div>' +
        '<p class="mono" style="color:#8fa6cc;word-break:break-all">Content hash ' + esc(j.content_sha256) + '</p>';
    }).catch(function () { out.innerHTML = '<div class="msg err">Could not reach the server.</div>'; });
  }
  document.getElementById('vf').addEventListener('submit', function (e) { e.preventDefault(); history.replaceState(null, '', '/verify/' + encodeURIComponent(input.value.trim())); check(input.value); });
  var m = location.pathname.match(/\/verify\/([^\/]+)/);
  if (m) { input.value = decodeURIComponent(m[1]); check(input.value); }
})();
