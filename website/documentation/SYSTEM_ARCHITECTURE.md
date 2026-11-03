# System architecture

```
 Browser (homepage, /app, /verify)          Test bench (boot USB)
        |  HTTPS + session cookie                 |  itad_push.py, station token
        v                                         v
   +--------------------------- ITAD Seal web service (Starlette / uvicorn) --------------------------+
   |  main.py  security headers, static files       api.py  routes + role / tenant checks               |
   |  security.py  passwords, sessions, tokens, audit chain, rate limits                                |
   |  integrity.py  SHA-256 fingerprint + Ed25519 check (same rule as the engine's verify_report.py)    |
   |  services.py  import report, calculate result, certificate rules, dashboard figures                |
   |  pdfgen.py / reports.py  certificate, laptop report, table reports (PDF + CSV)                     |
   +-------------------------------------------+--------------------------------------------------------+
                                               v
                                  SQLite (dev) or PostgreSQL (production)
```

## Why this stack (and how it differs from the suggestion)
The brief suggested React/Next.js and FastAPI. The build machine could not download packages from PyPI or npm, so those
could not be installed or tested. The brief allows alternatives, so the platform uses what could be built **and tested**:

| Layer | Used | Why |
|---|---|---|
| Web framework | Starlette + uvicorn (FastAPI is built on Starlette) | Already available; same style of routes |
| Front end | Plain HTML/CSS + vanilla JavaScript single-page app, no build step | Nothing to compile; strict Content-Security-Policy (no inline scripts) |
| Charts | Hand-made SVG | No chart library could be installed |
| Database | SQLite for dev/tests; PostgreSQL code path for production | See limits below |
| Passwords | scrypt (Python standard library) | bcrypt/argon2 not installable |
| PDF | ReportLab (including its QR code drawing) | Installed |
| Signatures | `cryptography` Ed25519 | Same algorithm as the engine |

Moving the front end to React/Next.js later is possible without changing the API.

## Main flows
1. **Erasure evidence in.** The engine (laptop_window.py) writes a signed JSON report. A station (or an upload) sends it unchanged. The platform checks the fingerprint and signature, stores the report as evidence, builds the erasure and test records, and calculates the result itself.
2. **Result.** PASS needs: check not blocked, a verified erasure, and every fitted component PASS. Any FAIL gives FAIL. Lock found gives BLOCKED. Anything missing gives INCOMPLETE. "Not fitted" (N/A) does not stop a PASS.
3. **Certificate.** Only for a verified erasure, on a PASS asset, from a signed report whose key the company trusts (unless the company switches that off). The certificate content is canonicalised, hashed, signed with the platform key and locked. Anyone can check it at `/verify/<number>`.
4. **Reports.** Generated on demand, stored with a SHA-256, listed in history.

## Roles
| Role | Can do |
|---|---|
| Super administrator | Everything, across companies |
| Company administrator | Their company: users, customers, jobs, assets, certificates (including revoke), settings, keys, stations, audit |
| Technician | Jobs, assets, tests, inspections, erasure imports, certificates for their company |
| Customer | Read-only view of **their own** jobs, assets, certificates and reports |

## Security measures
- Passwords: scrypt, policy (10+ characters, 3 kinds), lock after 5 failures for 15 minutes.
- Sessions: HttpOnly, SameSite=Lax (Secure in production) cookie; server stores only a hash; CSRF header on every change.
- No user enumeration on register/forgot; e-mail verification; one-time hashed tokens.
- Per-IP rate limits; request size limits; parameterised SQL everywhere; output escaped in the browser.
- CSP `default-src 'self'`, no inline scripts, frame-ancestors none; other security headers.
- Hash-chained audit log; evidence tables locked by database rules.
- No secrets in code. Everything comes from environment variables; production refuses to start without a secret key.

## Safety model (physical devices)
The website is passive. It does not run wipes and has no code path that talks to a drive.
`/api/engine/assets/{tag}` gives advice before a wipe (is the Asset ID known, does the serial match, is the job open),
but the station still enforces live USB, OS-disk, removable-disk, serial, Asset/Job ID, dry-run and verified-sanitisation checks itself.

## Limitations (honest list)
- PostgreSQL support is written but could **not be run** on the build machine. Run the tests against your PostgreSQL before relying on it.
- No real mail server was available: e-mails are written to the server log unless SMTP is configured.
- No real hardware was attached: the "Simulate engine" button (development only) imports a clearly labelled DEMONSTRATION report through the real import path.
- Not independently certified or audited.
