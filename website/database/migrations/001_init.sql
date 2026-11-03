-- ITAD Seal Platform: initial schema (portable SQLite / PostgreSQL).
-- Placeholders replaced by app/db.py:  {PK} = auto id column,  {BLOB} = binary type.
-- All timestamps are ISO-8601 UTC text. The UI shows Europe/Dublin.

CREATE TABLE companies (
  id {PK},
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  logo_data TEXT,                    -- small data: URI (png/jpeg/svg), max 200 KB
  address TEXT, email TEXT, phone TEXT, website TEXT,
  cert_prefix TEXT NOT NULL DEFAULT 'ITAD',
  accent_color TEXT NOT NULL DEFAULT '#2E9BFF',
  require_signed INTEGER NOT NULL DEFAULT 1,   -- certificates need a trusted-key signed engine report
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE customers (
  id {PK},
  company_id INTEGER NOT NULL REFERENCES companies(id),
  name TEXT NOT NULL,
  contact_name TEXT, email TEXT, phone TEXT, address TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX ix_customers_company ON customers(company_id);

CREATE TABLE users (
  id {PK},
  company_id INTEGER REFERENCES companies(id),     -- NULL only for super administrators
  customer_id INTEGER REFERENCES customers(id),    -- set only for role = customer
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('super_admin','company_admin','technician','customer')),
  password_hash TEXT NOT NULL,
  email_verified INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  last_login TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX ix_users_company ON users(company_id);

CREATE TABLE sessions (
  id {PK},
  token_hash TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  csrf TEXT NOT NULL,
  ip TEXT, user_agent TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE tokens (                -- email verification and password reset (one time, hashed)
  id {PK},
  user_id INTEGER NOT NULL REFERENCES users(id),
  purpose TEXT NOT NULL CHECK (purpose IN ('verify','reset')),
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  used_at TEXT
);

CREATE TABLE outbox (                -- every e-mail the platform sends (also sent by SMTP if configured)
  id {PK},
  to_email TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  sent INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE jobs (
  id {PK},
  company_id INTEGER NOT NULL REFERENCES companies(id),
  customer_id INTEGER REFERENCES customers(id),
  job_ref TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','completed','cancelled')),
  technician_id INTEGER REFERENCES users(id),
  notes TEXT,
  due_date TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE (company_id, job_ref)
);
CREATE INDEX ix_jobs_company ON jobs(company_id);

CREATE TABLE assets (
  id {PK},
  company_id INTEGER NOT NULL REFERENCES companies(id),
  customer_id INTEGER REFERENCES customers(id),
  job_id INTEGER REFERENCES jobs(id),
  asset_tag TEXT NOT NULL,
  serial TEXT NOT NULL,
  manufacturer TEXT, model TEXT, cpu TEXT, ram_gb REAL,
  storage_summary TEXT, bios TEXT, os_info TEXT, chassis TEXT,
  technician_id INTEGER REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'registered'
    CHECK (status IN ('registered','inspected','erased','tested','assessed','completed','failed','blocked')),
  demo INTEGER NOT NULL DEFAULT 0,   -- 1 = demonstration data
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (company_id, asset_tag)
);
CREATE INDEX ix_assets_company ON assets(company_id);
CREATE INDEX ix_assets_serial ON assets(serial);
CREATE INDEX ix_assets_job ON assets(job_id);

CREATE TABLE stations (              -- a boot-USB / test bench that may push reports
  id {PK},
  company_id INTEGER NOT NULL REFERENCES companies(id),
  name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  token_prefix TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  last_seen TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE trusted_keys (          -- Ed25519 public keys whose engine reports are accepted as signed
  id {PK},
  company_id INTEGER NOT NULL REFERENCES companies(id),
  key_id TEXT NOT NULL,
  public_key_hex TEXT NOT NULL,
  label TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  added_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  UNIQUE (company_id, key_id)
);

CREATE TABLE submissions (           -- every engine report received, kept as evidence
  id {PK},
  company_id INTEGER NOT NULL REFERENCES companies(id),
  station_id INTEGER REFERENCES stations(id),
  asset_id INTEGER REFERENCES assets(id),
  job_ref TEXT, asset_tag TEXT, serial TEXT,
  payload TEXT NOT NULL,             -- the full report JSON, unchanged
  sha256 TEXT NOT NULL,              -- fingerprint recomputed by the platform
  fingerprint_ok INTEGER NOT NULL DEFAULT 0,
  signed INTEGER NOT NULL DEFAULT 0,
  signature_ok INTEGER NOT NULL DEFAULT 0,
  key_id TEXT,
  key_trusted INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('accepted','rejected')),
  reason TEXT,
  demo INTEGER NOT NULL DEFAULT 0,
  received_at TEXT NOT NULL
);
CREATE INDEX ix_submissions_asset ON submissions(asset_id);

CREATE TABLE inspections (
  id {PK},
  asset_id INTEGER NOT NULL REFERENCES assets(id),
  submission_id INTEGER REFERENCES submissions(id),
  check_result TEXT,                 -- CLEAR / BLOCKED / CANNOT CHECK / NOT CHECKED
  absolute_result TEXT, enrolment_result TEXT,
  cosmetic_grade TEXT, issues TEXT, notes TEXT,
  recorded_by INTEGER REFERENCES users(id),
  recorded_at TEXT NOT NULL
);
CREATE INDEX ix_inspections_asset ON inspections(asset_id);

CREATE TABLE erasures (
  id {PK},
  asset_id INTEGER NOT NULL REFERENCES assets(id),
  submission_id INTEGER REFERENCES submissions(id),
  device_path TEXT, model TEXT, serial TEXT, capacity_gb REAL,
  method TEXT, level TEXT, standard TEXT, passes INTEGER,
  status TEXT NOT NULL,              -- Success / Erased - not verified / Failed / Not erased
  verified INTEGER NOT NULL DEFAULT 0,
  verification TEXT,                 -- JSON: how it was verified
  started TEXT, finished TEXT, technician TEXT,
  erasure_ref TEXT,
  source TEXT NOT NULL DEFAULT 'engine' CHECK (source IN ('engine','manual','demo')),
  demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX ix_erasures_asset ON erasures(asset_id);

CREATE TABLE test_results (
  id {PK},
  asset_id INTEGER NOT NULL REFERENCES assets(id),
  submission_id INTEGER REFERENCES submissions(id),
  component TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PASS','FAIL','WARN','NOT TESTED','N/A')),
  notes TEXT, details TEXT,
  tested_by TEXT,
  tested_at TEXT NOT NULL,
  UNIQUE (asset_id, component)
);

CREATE TABLE assessments (
  id {PK},
  asset_id INTEGER NOT NULL REFERENCES assets(id),
  overall TEXT NOT NULL CHECK (overall IN ('PASS','FAIL','INCOMPLETE','BLOCKED')),
  reasons TEXT,
  computed_by INTEGER REFERENCES users(id),
  computed_at TEXT NOT NULL
);
CREATE INDEX ix_assessments_asset ON assessments(asset_id);

CREATE TABLE certificates (          -- IMMUTABLE (see triggers). Revocation lives in its own table.
  id {PK},
  company_id INTEGER NOT NULL REFERENCES companies(id),
  number TEXT NOT NULL UNIQUE,
  asset_id INTEGER NOT NULL REFERENCES assets(id),
  erasure_id INTEGER NOT NULL REFERENCES erasures(id),
  customer_id INTEGER REFERENCES customers(id),
  payload TEXT NOT NULL,             -- canonical JSON that was signed
  payload_sha256 TEXT NOT NULL,
  signature TEXT NOT NULL,           -- Ed25519 over payload_sha256 (hex)
  key_id TEXT NOT NULL,
  demo INTEGER NOT NULL DEFAULT 0,
  issued_by INTEGER REFERENCES users(id),
  issued_at TEXT NOT NULL,
  UNIQUE (erasure_id)
);
CREATE INDEX ix_certificates_company ON certificates(company_id);

CREATE TABLE certificate_revocations (
  id {PK},
  certificate_id INTEGER NOT NULL UNIQUE REFERENCES certificates(id),
  reason TEXT NOT NULL,
  revoked_by INTEGER REFERENCES users(id),
  revoked_at TEXT NOT NULL
);

CREATE TABLE reports (               -- history of generated reports (PDF kept)
  id {PK},
  company_id INTEGER REFERENCES companies(id),
  report_type TEXT NOT NULL,
  title TEXT NOT NULL,
  params TEXT,
  file_name TEXT NOT NULL,
  content {BLOB} NOT NULL,
  sha256 TEXT NOT NULL,
  generated_by INTEGER REFERENCES users(id),
  customer_id INTEGER REFERENCES customers(id),
  created_at TEXT NOT NULL
);
CREATE INDEX ix_reports_company ON reports(company_id);

CREATE TABLE audit_log (             -- append only, hash chained
  id {PK},
  company_id INTEGER,
  user_id INTEGER,
  action TEXT NOT NULL,
  entity TEXT, entity_id TEXT,
  detail TEXT, ip TEXT,
  created_at TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE INDEX ix_audit_company ON audit_log(company_id);

CREATE TABLE demo_requests (         -- homepage "request a demo" form
  id {PK},
  name TEXT NOT NULL, email TEXT NOT NULL, company TEXT, message TEXT,
  created_at TEXT NOT NULL
);
