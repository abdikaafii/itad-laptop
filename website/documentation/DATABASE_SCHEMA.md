# Database schema

Source of truth: `database/migrations/001_initial.sql` (applied automatically at start-up; new changes go in
new numbered files). Times are UTC text `YYYY-MM-DD HH:MM:SS`.

| Table | Purpose |
|---|---|
| `companies` | An ITAD business (tenant). Logo, details, certificate footer, `auto_issue_certificates` (default 0), `is_demo`. |
| `customers` | A company's clients (e.g. a bank). Customer users are linked to one. |
| `users` | People. `role` ∈ super_admin, company_admin, technician, customer. Password hash, lockout counters, `session_epoch` (sign out everywhere). |
| `auth_tokens` | One-time email-verify / password-reset tokens (stored hashed, expiring). |
| `jobs` | A collection or batch (Job ID), customer, assigned technician, expected count, status. |
| `assets` | A laptop: asset tag, serial, make/model, status (registered, inspected, erased, passed, failed, incomplete), inspection notes. |
| `signing_keys` | Ed25519 public keys a company trusts for station reports. |
| `engine_reports` | Every report received, full JSON, fingerprint, `signature_status`, cross-check notes, test-mode flag. |
| `erasures` | One row per drive in a report: device, serial, method, NIST level, passes, verified, status, times. |
| `test_results` | Hardware test rows (PASS / FAIL / WARN / NOT_TESTED / NOT_FITTED) per component. |
| `certificates` | Sealed certificates. Triggers: no edit, no delete, revoke only, no un-revoke. |
| `api_tokens` | Station API tokens (hash + prefix only). |
| `audit_log` | Append-only (triggers block update/delete). |
| `email_outbox` | Mail saved when SMTP is not configured. |
| `demo_requests` | Messages from the homepage demo form. |

Relationships: company → customers, users, jobs, assets, keys, tokens, certificates. job → assets.
asset → engine_reports → erasures, test_results. erasure → certificate (unique: one certificate per erasure).

Moving to PostgreSQL: the SQL uses standard types; replace `INTEGER PRIMARY KEY AUTOINCREMENT`,
`?` placeholders and the SQLite triggers (`RAISE(ABORT, …)` → `RAISE EXCEPTION`) and swap `core/db.py`.
