# Database schema

Two engines are supported: SQLite (development, tests) and PostgreSQL (production, see limits in INSTALLATION_GUIDE.md).
The files are in `database/migrations/`: `001_init.sql` (tables), `002_immutable.sqlite.sql` / `002_immutable.postgres.sql` (rules that block editing or deleting evidence).

## companies

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| name | TEXT NOT NULL |
| slug | TEXT NOT NULL UNIQUE |
| logo_data | TEXT,                    -- small data: URI (png/jpeg/svg), max 200 KB |
| address | TEXT, email TEXT, phone TEXT, website TEXT |
| cert_prefix | TEXT NOT NULL DEFAULT 'ITAD' |
| accent_color | TEXT NOT NULL DEFAULT '#2E9BFF' |
| require_signed | INTEGER NOT NULL DEFAULT 1,   -- certificates need a trusted-key signed engine report |
| active | INTEGER NOT NULL DEFAULT 1 |
| created_at | TEXT NOT NULL |

## customers

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| company_id | INTEGER NOT NULL REFERENCES companies(id) |
| name | TEXT NOT NULL |
| contact_name | TEXT, email TEXT, phone TEXT, address TEXT |
| created_at | TEXT NOT NULL |

## users

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| company_id | INTEGER REFERENCES companies(id),     -- NULL only for super administrators |
| customer_id | INTEGER REFERENCES customers(id),    -- set only for role = customer |
| email | TEXT NOT NULL UNIQUE |
| name | TEXT NOT NULL |
| role | TEXT NOT NULL CHECK (role IN ('super_admin','company_admin','technician','customer')) |
| password_hash | TEXT NOT NULL |
| email_verified | INTEGER NOT NULL DEFAULT 0 |
| active | INTEGER NOT NULL DEFAULT 1 |
| failed_logins | INTEGER NOT NULL DEFAULT 0 |
| locked_until | TEXT |
| last_login | TEXT |
| created_at | TEXT NOT NULL |

## sessions

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| token_hash | TEXT NOT NULL UNIQUE |
| user_id | INTEGER NOT NULL REFERENCES users(id) |
| csrf | TEXT NOT NULL |
| ip | TEXT, user_agent TEXT |
| created_at | TEXT NOT NULL |
| expires_at | TEXT NOT NULL |

## tokens

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| user_id | INTEGER NOT NULL REFERENCES users(id) |
| purpose | TEXT NOT NULL CHECK (purpose IN ('verify','reset')) |
| token_hash | TEXT NOT NULL UNIQUE |
| expires_at | TEXT NOT NULL |
| used_at | TEXT |

## outbox

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| to_email | TEXT NOT NULL |
| subject | TEXT NOT NULL |
| body | TEXT NOT NULL |
| created_at | TEXT NOT NULL |
| sent | INTEGER NOT NULL DEFAULT 0 |

## jobs

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| company_id | INTEGER NOT NULL REFERENCES companies(id) |
| customer_id | INTEGER REFERENCES customers(id) |
| job_ref | TEXT NOT NULL |
| title | TEXT NOT NULL |
| status | TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','completed','cancelled')) |
| technician_id | INTEGER REFERENCES users(id) |
| notes | TEXT |
| due_date | TEXT |
| created_at | TEXT NOT NULL |
| completed_at | TEXT |

## assets

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| company_id | INTEGER NOT NULL REFERENCES companies(id) |
| customer_id | INTEGER REFERENCES customers(id) |
| job_id | INTEGER REFERENCES jobs(id) |
| asset_tag | TEXT NOT NULL |
| serial | TEXT NOT NULL |
| manufacturer | TEXT, model TEXT, cpu TEXT, ram_gb REAL |
| storage_summary | TEXT, bios TEXT, os_info TEXT, chassis TEXT |
| technician_id | INTEGER REFERENCES users(id) |
| status | TEXT NOT NULL DEFAULT 'registered' |
| demo | INTEGER NOT NULL DEFAULT 0,   -- 1 = demonstration data |
| created_at | TEXT NOT NULL |
| updated_at | TEXT NOT NULL |

## stations

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| company_id | INTEGER NOT NULL REFERENCES companies(id) |
| name | TEXT NOT NULL |
| token_hash | TEXT NOT NULL UNIQUE |
| token_prefix | TEXT NOT NULL |
| active | INTEGER NOT NULL DEFAULT 1 |
| last_seen | TEXT |
| created_at | TEXT NOT NULL |

## trusted_keys

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| company_id | INTEGER NOT NULL REFERENCES companies(id) |
| key_id | TEXT NOT NULL |
| public_key_hex | TEXT NOT NULL |
| label | TEXT |
| active | INTEGER NOT NULL DEFAULT 1 |
| added_by | INTEGER REFERENCES users(id) |
| created_at | TEXT NOT NULL |

## submissions

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| company_id | INTEGER NOT NULL REFERENCES companies(id) |
| station_id | INTEGER REFERENCES stations(id) |
| asset_id | INTEGER REFERENCES assets(id) |
| job_ref | TEXT, asset_tag TEXT, serial TEXT |
| payload | TEXT NOT NULL,             -- the full report JSON, unchanged |
| sha256 | TEXT NOT NULL,              -- fingerprint recomputed by the platform |
| fingerprint_ok | INTEGER NOT NULL DEFAULT 0 |
| signed | INTEGER NOT NULL DEFAULT 0 |
| signature_ok | INTEGER NOT NULL DEFAULT 0 |
| key_id | TEXT |
| key_trusted | INTEGER NOT NULL DEFAULT 0 |
| status | TEXT NOT NULL CHECK (status IN ('accepted','rejected')) |
| reason | TEXT |
| demo | INTEGER NOT NULL DEFAULT 0 |
| received_at | TEXT NOT NULL |

## inspections

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| asset_id | INTEGER NOT NULL REFERENCES assets(id) |
| submission_id | INTEGER REFERENCES submissions(id) |
| absolute_result | TEXT, enrolment_result TEXT |
| cosmetic_grade | TEXT, issues TEXT, notes TEXT |
| recorded_by | INTEGER REFERENCES users(id) |
| recorded_at | TEXT NOT NULL |

## erasures

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| asset_id | INTEGER NOT NULL REFERENCES assets(id) |
| submission_id | INTEGER REFERENCES submissions(id) |
| device_path | TEXT, model TEXT, serial TEXT, capacity_gb REAL |
| method | TEXT, level TEXT, standard TEXT, passes INTEGER |
| status | TEXT NOT NULL,              -- Success / Erased - not verified / Failed / Not erased |
| verified | INTEGER NOT NULL DEFAULT 0 |
| verification | TEXT,                 -- JSON: how it was verified |
| started | TEXT, finished TEXT, technician TEXT |
| erasure_ref | TEXT |
| source | TEXT NOT NULL DEFAULT 'engine' CHECK (source IN ('engine','manual','demo')) |
| demo | INTEGER NOT NULL DEFAULT 0 |
| created_at | TEXT NOT NULL |

## test_results

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| asset_id | INTEGER NOT NULL REFERENCES assets(id) |
| submission_id | INTEGER REFERENCES submissions(id) |
| component | TEXT NOT NULL |
| status | TEXT NOT NULL CHECK (status IN ('PASS','FAIL','WARN','NOT TESTED','N/A')) |
| notes | TEXT, details TEXT |
| tested_by | TEXT |
| tested_at | TEXT NOT NULL |

## assessments

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| asset_id | INTEGER NOT NULL REFERENCES assets(id) |
| overall | TEXT NOT NULL CHECK (overall IN ('PASS','FAIL','INCOMPLETE','BLOCKED')) |
| reasons | TEXT |
| computed_by | INTEGER REFERENCES users(id) |
| computed_at | TEXT NOT NULL |

## certificates

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| company_id | INTEGER NOT NULL REFERENCES companies(id) |
| number | TEXT NOT NULL UNIQUE |
| asset_id | INTEGER NOT NULL REFERENCES assets(id) |
| erasure_id | INTEGER NOT NULL REFERENCES erasures(id) |
| customer_id | INTEGER REFERENCES customers(id) |
| payload | TEXT NOT NULL,             -- canonical JSON that was signed |
| payload_sha256 | TEXT NOT NULL |
| signature | TEXT NOT NULL,           -- Ed25519 over payload_sha256 (hex) |
| key_id | TEXT NOT NULL |
| demo | INTEGER NOT NULL DEFAULT 0 |
| issued_by | INTEGER REFERENCES users(id) |
| issued_at | TEXT NOT NULL |

## certificate_revocations

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| certificate_id | INTEGER NOT NULL UNIQUE REFERENCES certificates(id) |
| reason | TEXT NOT NULL |
| revoked_by | INTEGER REFERENCES users(id) |
| revoked_at | TEXT NOT NULL |

## reports

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| company_id | INTEGER REFERENCES companies(id) |
| report_type | TEXT NOT NULL |
| title | TEXT NOT NULL |
| params | TEXT |
| file_name | TEXT NOT NULL |
| content | {BLOB} NOT NULL |
| sha256 | TEXT NOT NULL |
| generated_by | INTEGER REFERENCES users(id) |
| customer_id | INTEGER REFERENCES customers(id) |
| created_at | TEXT NOT NULL |

## audit_log

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| company_id | INTEGER |
| user_id | INTEGER |
| action | TEXT NOT NULL |
| entity | TEXT, entity_id TEXT |
| detail | TEXT, ip TEXT |
| created_at | TEXT NOT NULL |
| prev_hash | TEXT NOT NULL |
| hash | TEXT NOT NULL |

## demo_requests

| Column | Definition |
|---|---|
| id | primary key (auto number) |
| name | TEXT NOT NULL, email TEXT NOT NULL, company TEXT, message TEXT |
| created_at | TEXT NOT NULL |

## Tamper protection
- `audit_log`: rows can be added only. Each row stores a hash of the previous row (a chain). `GET /api/audit/verify` re-checks the chain.
- `erasures`, `certificates`, `submissions`: the evidence columns cannot be changed or deleted after they are written (database triggers).
- `certificate_revocations` is a separate table, so a certificate itself is never edited to revoke it.
- Passwords and all tokens (sessions, e-mail, reset, station) are stored only as hashes.

## Tenancy
Every business table carries `company_id`. Customers are additionally limited to their own `customer_id`. The API adds these filters on every query (`tenant_filter`), and tests check that one company or customer can never read another's rows.
