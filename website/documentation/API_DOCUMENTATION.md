# API documentation

Base URL: your platform address. JSON in, JSON out. Sessions use an HttpOnly cookie; every state-changing request from a browser must send the header `X-CSRF-Token` (value returned by `/api/auth/login` and `/api/auth/me`). Stations use `Authorization: Bearer <station token>` instead.

Errors look like `{"detail": "message"}` with status 400/401/403/404/409/422/429. Times are shown in Europe/Dublin; stored in UTC.

| Method | Path | Who | What |
|---|---|---|---|
| GET | `/api/assets` | any signed-in user (tenant-scoped) | assets list |
| POST | `/api/assets` | staff (not customers) | asset create |
| GET | `/api/assets/{id:int}` | any signed-in user (tenant-scoped) | asset detail |
| PUT | `/api/assets/{id:int}` | staff (not customers) | asset update |
| POST | `/api/assets/{id:int}/assess` | staff (not customers) | asset assess |
| POST | `/api/assets/{id:int}/erasures` | staff (not customers) | Record that a wipe was done outside the engine. It is stored as NOT verified |
| POST | `/api/assets/{id:int}/inspection` | staff (not customers) | Technician observations. The only check result a person may type is BLOCKED |
| POST | `/api/assets/{id:int}/tests` | staff (not customers) | asset test |
| GET | `/api/audit` | company/super admin | audit list |
| GET | `/api/audit/verify` | company/super admin | audit verify |
| POST | `/api/auth/forgot` | public | forgot |
| POST | `/api/auth/login` | public | login |
| POST | `/api/auth/logout` | optional | logout |
| GET | `/api/auth/me` | optional | me |
| PUT | `/api/auth/profile` | any signed-in user (tenant-scoped) | profile |
| POST | `/api/auth/register` | public | register |
| POST | `/api/auth/reset` | public | reset |
| POST | `/api/auth/verify-email` | public | verify email |
| GET | `/api/certificates` | any signed-in user (tenant-scoped) | certificates list |
| GET | `/api/certificates/{id:int}` | any signed-in user (tenant-scoped) | certificate detail |
| GET | `/api/certificates/{id:int}/pdf` | any signed-in user (tenant-scoped) | certificate pdf |
| POST | `/api/certificates/{id:int}/revoke` | company/super admin | certificate revoke |
| GET | `/api/companies` | company/super admin | companies |
| POST | `/api/companies` | super admin | company create |
| PUT | `/api/companies/{id:int}` | company/super admin | company update |
| POST | `/api/contact` | public | contact |
| GET | `/api/customers` | staff (not customers) | customers list |
| POST | `/api/customers` | admin or technician | customer create |
| PUT | `/api/customers/{id:int}` | company/super admin | customer update |
| GET | `/api/dashboard` | any signed-in user (tenant-scoped) | dashboard |
| POST | `/api/dev/simulate-engine` | staff (not customers) | Development only. Builds a report like laptop_window.py would, clearly marked |
| GET | `/api/engine/assets/{tag}` | station token | Safety cross-check before a wipe. The station asks, the platform answers, |
| GET | `/api/engine/ping` | station token | engine ping |
| POST | `/api/engine/submit` | station token | engine submit |
| GET | `/api/erasures/{id:int}/blockers` | staff (not customers) | erasure blockers |
| POST | `/api/erasures/{id:int}/certificate` | staff (not customers) | certificate issue |
| GET | `/api/health` | public | health |
| GET | `/api/jobs` | any signed-in user (tenant-scoped) | jobs list |
| POST | `/api/jobs` | admin or technician | job create |
| GET | `/api/jobs/{id:int}` | any signed-in user (tenant-scoped) | job detail |
| PUT | `/api/jobs/{id:int}` | admin or technician | job update |
| GET | `/api/keys` | company/super admin | keys list |
| POST | `/api/keys` | company/super admin | key add |
| POST | `/api/keys/from-submission/{id:int}` | company/super admin | key trust submission |
| DELETE | `/api/keys/{id:int}` | company/super admin | key disable |
| GET | `/api/public/verify/{number}` | public | public verify |
| GET | `/api/report-types` | any signed-in user (tenant-scoped) | report types |
| GET | `/api/reports` | any signed-in user (tenant-scoped) | reports list |
| POST | `/api/reports` | any signed-in user (tenant-scoped) | report generate |
| GET | `/api/reports/{id:int}/download` | any signed-in user (tenant-scoped) | report download |
| GET | `/api/stations` | company/super admin | stations list |
| POST | `/api/stations` | company/super admin | station create |
| DELETE | `/api/stations/{id:int}` | company/super admin | station disable |
| GET | `/api/submissions` | staff (not customers) | submissions list |
| POST | `/api/submissions` | staff (not customers) | A person uploads a report file (for example from the USB stick) instead of a station pushing it. |
| GET | `/api/technicians` | staff (not customers) | technicians |
| GET | `/api/users` | company/super admin | users list |
| POST | `/api/users` | company/super admin | user create |
| PUT | `/api/users/{id:int}` | company/super admin | user update |

## Pushing an engine report (station)
```
curl -X POST $URL/api/engine/submit -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" --data @report.json
```
Reply: `{ok, status, overall, signature_valid, key_trusted, submission_id, ...}`. A changed or unsigned-but-claiming-signed report is kept as evidence with status `rejected` and never creates a verified erasure.

## Public verification
`GET /api/public/verify/{number}` and the web page `/verify/{number}`. Returns state `VALID`, `DEMONSTRATION`, `REVOKED` or `NOT FOUND`, plus minimal, masked details.

Interactive: the same list can be seen in the code in `backend/app/api.py` (one `@route` per row above).
