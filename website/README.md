# ITAD Platform (Laptop ITAD)

A web platform around the existing laptop ITAD engine: identify, inspect, erase, test, certify and report on
laptops, for companies, technicians and their customers.

**Honest status.** The website, database, certificates, reports and tests are built and tested with
simulated reports in the engine's exact signed format. It has *not* yet been run against real hardware or a
real boot-stick report, and the Docker files could not be built where this was made. All demo data is
labelled DEMONSTRATION and its certificates carry a `DEMO-` number and watermark.

## What is in it

- Public home page (10 sections), certificate verification page, demo request form
- Registration, email verification, login/logout, forgot/reset password, profile
- Roles: Super Administrator, Company Administrator, Technician, Customer (customers see only their own data)
- Company profile with logo, customers, team invites, station signing keys, API tokens, audit log
- Dashboard with totals, charts, filters, search, CSV download
- Laptop workflow: identification → inspection → erasure result → hardware tests → final assessment → PDF
- Tamper-proof certificates (PDF, QR, public verification, revoke) only from verified, signed erasure records
- Reports: monthly, customer assets, job completion, failed devices, technician performance, certificates (PDF/CSV)
- One-way integration with the existing engine (see `documentation/API_DOCUMENTATION.md`)

## Quick start

```
cd backend
pip install -r requirements.txt
python manage.py init-db
python manage.py seed-demo
python manage.py run          # http://127.0.0.1:5000
```
Demo logins: `backend/instance/demo_credentials.txt`. Tests: `python -m pytest tests` (from this folder).
Full steps, server and Docker: `documentation/INSTALLATION_GUIDE.md`.

## Documents

[COMPETITOR_RESEARCH](documentation/COMPETITOR_RESEARCH.md) ·
[SYSTEM_ARCHITECTURE](documentation/SYSTEM_ARCHITECTURE.md) ·
[DATABASE_SCHEMA](documentation/DATABASE_SCHEMA.md) ·
[API_DOCUMENTATION](documentation/API_DOCUMENTATION.md) ·
[INSTALLATION_GUIDE](documentation/INSTALLATION_GUIDE.md) ·
[DEVELOPMENT_ROADMAP](documentation/DEVELOPMENT_ROADMAP.md)

## Safety rules built in

The website cannot start a wipe or bypass any device safety check; it never says "erased" without a signed,
verified report; certificates cannot be edited or deleted; secrets come only from environment variables
(`.env.example`); the existing engine files are untouched.
