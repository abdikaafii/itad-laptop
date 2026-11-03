# ITAD Seal platform (laptop module)

A web platform for laptop IT-asset disposal: register assets, import verified erasure results from the existing
engine, record hardware tests, calculate a result, issue signed certificates and produce reports.
It sits **beside** the existing boot-USB engine in this repository and does not change any engine script.

| Folder | What is in it |
|---|---|
| `backend/` | The web service (Starlette): `app/api.py` routes, `security.py`, `integrity.py`, `services.py`, PDF and report code |
| `frontend/` | Homepage, the app (`/app`), public verification page (`/verify`), CSS, JavaScript |
| `database/` | SQL migrations (tables and tamper-protection rules) |
| `integrations/` | `itad_push.py`: sends a finished engine report to the platform |
| `docker/` | Dockerfile and docker-compose (app + PostgreSQL) |
| `tests/` | 44 automated tests |
| `documentation/` | Competitor research, architecture, database, API, install guide, roadmap |
| `authentication/ dashboard/ certificates/ reports/ assets/` | Reserved folders matching the requested layout; the code for these areas lives in `backend/app` (see documentation/SYSTEM_ARCHITECTURE.md) |

## Quick start
```
cd website/backend
pip install -r requirements.txt
python3 -m uvicorn app.main:app --port 8000      # then open http://localhost:8000
```
Full steps, demo logins and Docker: `documentation/INSTALLATION_GUIDE.md`.

## The rules the platform keeps
- A verified erasure exists only if it came from an **intact engine report**. Typed-in wipes are stored as *not verified* and can never get a certificate.
- The result (PASS / FAIL / INCOMPLETE / BLOCKED) is always calculated from stored records.
- Certificates are signed, locked, publicly verifiable, and can be revoked (never edited).
- Customers can only ever see their own data.
- The website never starts a wipe and never bypasses the station's physical-device safety checks.

## Honest status
Working and tested here: everything above on SQLite (44 tests, plus browser checks of every screen).
Written but **not run here**: the PostgreSQL path, SMTP mail, and a real hardware bench (no network access to install packages or attach hardware).
The stack differs from the suggested React/FastAPI for that reason; see `documentation/SYSTEM_ARCHITECTURE.md`.
Reports: laptop, hardware testing, customer assets, technician performance, job completion, failed devices, monthly processing (PDF + CSV). The "erasure certificate" report is the certificate itself (PDF).
