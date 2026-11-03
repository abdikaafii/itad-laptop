# Development roadmap

## Done in this build
Public site, registration/login/reset/verification, roles and isolation, dashboard with charts and filters,
laptop and job workflow, report ingest with signature verification, certificates with tamper protection and
public verification, eight report/PDF/CSV exports, demo data, 38 automated tests, Docker files.

## Not tested here (needs your environment)
- Real hardware, and real reports from the boot stick (only simulated reports in the same format were used).
- Docker build (no Docker daemon in the build environment).
- Real email sending (SMTP), HTTPS deployment.

## Next
1. Run a real station report through `upload_report.py`; register that stick's public key.
2. PostgreSQL option (replace `core/db.py`, migrate triggers), then SQLAlchemy/Alembic if wanted.
3. Two-factor sign-in for staff; password-breach check.
4. Live job-progress screen from the station (read-only status pings).
5. React/Next.js front end over the same REST endpoints if a richer UI is needed.
6. Bulk certificate issue; certificate email to customer; scheduled monthly report mail.
7. Extra asset types (phones, servers, drives) reusing the same engine/report/certificate pipeline.
8. Backups job and restore test; log shipping.
