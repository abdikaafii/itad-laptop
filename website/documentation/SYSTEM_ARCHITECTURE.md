# System architecture

## Overview

```
 Boot USB station (existing engine)          ITAD Platform website (this folder)
 ┌──────────────────────────────┐            ┌────────────────────────────────────────┐
 │ laptop_check / wipe / test    │  signed    │ /api/v1/engine/reports  (Bearer token) │
 │ safety checks stay HERE       │ ─────────► │   verify SHA-256 + Ed25519 signature   │
 │ writes final signed report    │  report    │   cross-check job / asset / serial     │
 └──────────────────────────────┘  (one way)  │   store → assets, erasures, tests      │
                                              │ Certificate service (manual or auto)   │
 Browser (staff, customers) ───────────────►  │ Dashboard, reports, PDFs, verification │
                                              └───────────────┬────────────────────────┘
                                                              │ SQLite (PostgreSQL-ready SQL)
```

**The website never controls the hardware.** The engine on the boot stick keeps every physical safety check
(live USB, OS disk, removable disk protection, serial verification, Asset/Job cross-check, dry-run unless
authorised, wipe verification). The website only *receives the finished report* and decides what it may
claim from it. There is no endpoint that starts a wipe, edits a test result, or issues a certificate on a
station's say-so.

## Technology (what is actually built)

| Layer | Used | Brief suggested | Why different |
|---|---|---|---|
| Backend | Flask 3 (app factory, blueprints) | FastAPI | PyPI was unreachable in the build environment, so only already-installed packages could be used and tested |
| Frontend | Server-rendered Jinja, vanilla JS, inline-SVG charts | React/Next.js, Tailwind | same reason; also gives a strict CSP with no inline scripts |
| Database | SQLite (WAL, foreign keys), plain SQL migrations | PostgreSQL | same reason. SQL is kept portable; see roadmap |
| PDFs | ReportLab (+ QR) | any | |
| Signatures | `cryptography` Ed25519 | | matches the engine's report format |
| Deploy | gunicorn, Docker (untested here), Caddy example | Docker-ready | |

## Folder map (inside `website/`)

- `backend/app/` application code: `core/` (config, db, security, audit, mail, access scoping, PDF helpers),
  `authentication/`, `dashboard/`, `assets/` (laptops and jobs), `certificates/`, `reports/`, `integrations/`
  (engine ingest, API, simulator), `admin.py`, `public.py`, `seed.py`
- `frontend/templates` and `frontend/static` pages, CSS, JS
- `database/migrations` SQL schema
- `integrations/upload_report.py` command-line uploader for a station (outside the engine, stdlib only)
- `documentation/`, `tests/`, `docker/`

## Trust model for reports

1. Fingerprint = SHA-256 of the report JSON without its `integrity` block (sorted keys, compact separators), exactly as the engine computes it.
2. The signature is checked against Ed25519 public keys **registered by a company administrator**. Result: `valid`, `invalid`, `unsigned`, `unknown_key`, or `tampered`.
3. Cross-checks: the job exists for this company; an asset tag is not reused for a different serial; the serial is real (not "Unknown" or similar).
4. Only a report that passes all of this *and* says `check_result = CLEAR`, `wipe_state = WIPED`, with every drive `Success` and verified, and not from test mode, makes a certificate possible.

## Certificates

- Number `ITAD-YYYY-<12 hex>` (`DEMO-` prefix for demonstration data).
- Content is frozen as JSON with a SHA-256 and an HMAC (using `ITAD_SECRET_KEY`).
- Database triggers refuse any edit or delete of certificate content; the only allowed change is revocation, and a revoked certificate cannot be un-revoked.
- Public verification page re-checks the seal; it shows a masked serial and no customer or technician names.
- By default a person issues each certificate (management validation step recommended by NIST SP 800-88). A per-company switch allows automatic issue.

## Security measures

scrypt password hashing; password policy; login lockout; HttpOnly + SameSite cookies (Secure in production);
CSRF tokens on all forms; strict Content-Security-Policy (no inline script or style); role-scoped SQL
(Customer sees only own customer's records, Technician only assigned work, Company Admin only own company);
rate limiting; generic answers on register/forgot (no account enumeration); CSV formula-injection guard;
logo re-encoding; append-only audit log; secrets only from environment variables; API tokens stored as hashes.

## Roles

| Role | Can |
|---|---|
| Super Administrator | all companies, create/disable companies, audit |
| Company Administrator | own company: users, customers, keys, tokens, jobs, laptops, certificates, reports |
| Technician | assigned jobs and own laptops: register, inspect, upload report, view results |
| Customer | read-only view of own organisation's laptops, certificates, reports |
