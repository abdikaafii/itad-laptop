-- ITAD Platform: initial schema (SQLite for development, written to port to PostgreSQL).
-- Applied automatically, in filename order, by backend/app/core/db.py.

CREATE TABLE companies (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    legal_name              TEXT NOT NULL DEFAULT '',
    address                 TEXT NOT NULL DEFAULT '',
    email                   TEXT NOT NULL DEFAULT '',
    phone                   TEXT NOT NULL DEFAULT '',
    website                 TEXT NOT NULL DEFAULT '',
    logo_file               TEXT NOT NULL DEFAULT '',
    certificate_footer      TEXT NOT NULL DEFAULT '',
    auto_issue_certificates INTEGER NOT NULL DEFAULT 0,
    is_active               INTEGER NOT NULL DEFAULT 1,
    is_demo                 INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL
);

CREATE TABLE customers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id    INTEGER NOT NULL REFERENCES companies(id),
    name          TEXT NOT NULL,
    contact_name  TEXT NOT NULL DEFAULT '',
    contact_email TEXT NOT NULL DEFAULT '',
    address       TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    UNIQUE (company_id, name)
);

CREATE TABLE users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id     INTEGER REFERENCES companies(id),      -- NULL only for super administrators
    customer_id    INTEGER REFERENCES customers(id),      -- set only for role 'customer'
    email          TEXT NOT NULL UNIQUE,
    full_name      TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('super_admin','company_admin','technician','customer')),
    password_hash  TEXT NOT NULL,
    email_verified INTEGER NOT NULL DEFAULT 0,
    is_active      INTEGER NOT NULL DEFAULT 1,
    failed_logins  INTEGER NOT NULL DEFAULT 0,
    locked_until   TEXT NOT NULL DEFAULT '',
    session_epoch  INTEGER NOT NULL DEFAULT 1,            -- bump to sign the user out everywhere
    last_login     TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    CHECK (role = 'super_admin' OR company_id IS NOT NULL),
    CHECK (role <> 'customer' OR customer_id IS NOT NULL)
);

CREATE TABLE auth_tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    purpose    TEXT NOT NULL CHECK (purpose IN ('verify_email','reset_password')),
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE jobs (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id             INTEGER NOT NULL REFERENCES companies(id),
    customer_id            INTEGER REFERENCES customers(id),
    job_ref                TEXT NOT NULL,
    title                  TEXT NOT NULL DEFAULT '',
    status                 TEXT NOT NULL DEFAULT 'open'
                           CHECK (status IN ('open','in_progress','completed','cancelled')),
    assigned_technician_id INTEGER REFERENCES users(id),
    expected_assets        INTEGER NOT NULL DEFAULT 0,
    notes                  TEXT NOT NULL DEFAULT '',
    created_by             INTEGER REFERENCES users(id),
    created_at             TEXT NOT NULL,
    completed_at           TEXT NOT NULL DEFAULT '',
    is_demo                INTEGER NOT NULL DEFAULT 0,
    UNIQUE (company_id, job_ref)
);

CREATE TABLE assets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       INTEGER NOT NULL REFERENCES companies(id),
    customer_id      INTEGER REFERENCES customers(id),
    job_id           INTEGER REFERENCES jobs(id),
    asset_tag        TEXT NOT NULL DEFAULT '',
    serial           TEXT NOT NULL,
    manufacturer     TEXT NOT NULL DEFAULT '',
    model            TEXT NOT NULL DEFAULT '',
    cpu              TEXT NOT NULL DEFAULT '',
    ram              TEXT NOT NULL DEFAULT '',
    storage          TEXT NOT NULL DEFAULT '',
    bios             TEXT NOT NULL DEFAULT '',
    os_info          TEXT NOT NULL DEFAULT '',
    boot_mode        TEXT NOT NULL DEFAULT '',
    location         TEXT NOT NULL DEFAULT '',
    technician_id    INTEGER REFERENCES users(id),
    technician_name  TEXT NOT NULL DEFAULT '',
    -- registered -> inspected -> erased -> (tested) -> passed | failed | incomplete
    status           TEXT NOT NULL DEFAULT 'registered'
                     CHECK (status IN ('registered','inspected','erased','passed','failed','incomplete')),
    inspection_notes TEXT NOT NULL DEFAULT '',
    check_result     TEXT NOT NULL DEFAULT '',
    absolute_status  TEXT NOT NULL DEFAULT '',
    enrolment_status TEXT NOT NULL DEFAULT '',
    cosmetic_grade   TEXT NOT NULL DEFAULT '',
    is_demo          INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    processed_at     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_assets_company ON assets(company_id);
CREATE INDEX idx_assets_serial  ON assets(company_id, serial);
CREATE INDEX idx_assets_tag     ON assets(company_id, asset_tag);
CREATE INDEX idx_assets_job     ON assets(job_id);
CREATE INDEX idx_assets_customer ON assets(customer_id);

-- Trusted station signing keys (the itad_signing_public.pem from each ITAD_DATA stick).
CREATE TABLE signing_keys (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id     INTEGER NOT NULL REFERENCES companies(id),
    key_id         TEXT NOT NULL,
    public_key_hex TEXT NOT NULL,
    label          TEXT NOT NULL DEFAULT '',
    is_active      INTEGER NOT NULL DEFAULT 1,
    is_demo        INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    UNIQUE (company_id, key_id)
);

-- Every report received from the engine, stored exactly as received.
CREATE TABLE engine_reports (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       INTEGER NOT NULL REFERENCES companies(id),
    asset_id         INTEGER REFERENCES assets(id),
    source           TEXT NOT NULL DEFAULT 'engine',     -- engine | upload | demo
    received_at      TEXT NOT NULL,
    report_json      TEXT NOT NULL,
    claimed_sha256   TEXT NOT NULL DEFAULT '',
    computed_sha256  TEXT NOT NULL DEFAULT '',
    signature_status TEXT NOT NULL
                     CHECK (signature_status IN ('valid','invalid','unsigned','unknown_key','tampered')),
    key_id           TEXT NOT NULL DEFAULT '',
    public_key_hex   TEXT NOT NULL DEFAULT '',
    job_ref          TEXT NOT NULL DEFAULT '',
    serial           TEXT NOT NULL DEFAULT '',
    overall          TEXT NOT NULL DEFAULT '',
    test_mode        INTEGER NOT NULL DEFAULT 0,
    cross_check_notes TEXT NOT NULL DEFAULT '',          -- JSON list of problems found
    is_demo          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_reports_asset ON engine_reports(asset_id);

CREATE TABLE erasures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id    INTEGER NOT NULL REFERENCES assets(id),
    report_id   INTEGER NOT NULL REFERENCES engine_reports(id),
    erasure_ref TEXT NOT NULL DEFAULT '',
    device_path TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    serial      TEXT NOT NULL DEFAULT '',
    capacity_gb REAL,
    method      TEXT NOT NULL DEFAULT '',
    level       TEXT NOT NULL DEFAULT '',
    standard    TEXT NOT NULL DEFAULT '',
    passes      INTEGER NOT NULL DEFAULT 0,
    verified    INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT '',                -- Success | Erased - not verified | Failed
    technician  TEXT NOT NULL DEFAULT '',
    started     TEXT NOT NULL DEFAULT '',
    finished    TEXT NOT NULL DEFAULT '',
    evidence    TEXT NOT NULL DEFAULT ''                 -- JSON: hidden areas, wipe sha256 etc.
);
CREATE INDEX idx_erasures_asset ON erasures(asset_id);

CREATE TABLE test_results (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id  INTEGER NOT NULL REFERENCES assets(id),
    report_id INTEGER NOT NULL REFERENCES engine_reports(id),
    component TEXT NOT NULL,
    status    TEXT NOT NULL CHECK (status IN ('PASS','FAIL','WARN','NOT_TESTED','NOT_FITTED')),
    notes     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_tests_asset ON test_results(asset_id);

CREATE TABLE certificates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id    INTEGER NOT NULL REFERENCES companies(id),
    asset_id      INTEGER NOT NULL REFERENCES assets(id),
    erasure_id    INTEGER NOT NULL UNIQUE REFERENCES erasures(id),
    customer_id   INTEGER REFERENCES customers(id),
    number        TEXT NOT NULL UNIQUE,
    issued_at     TEXT NOT NULL,
    issued_by     INTEGER REFERENCES users(id),
    content_json  TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    mac           TEXT NOT NULL,
    is_demo       INTEGER NOT NULL DEFAULT 0,
    revoked       INTEGER NOT NULL DEFAULT 0,
    revoked_reason TEXT NOT NULL DEFAULT '',
    revoked_at    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_certs_company ON certificates(company_id);

-- A certificate's content can never be edited or deleted. Only the revocation fields may change.
CREATE TRIGGER certificates_no_edit
BEFORE UPDATE OF company_id, asset_id, erasure_id, customer_id, number, issued_at, issued_by,
                 content_json, content_sha256, mac, is_demo ON certificates
BEGIN
    SELECT RAISE(ABORT, 'certificates cannot be modified');
END;
CREATE TRIGGER certificates_no_delete
BEFORE DELETE ON certificates
BEGIN
    SELECT RAISE(ABORT, 'certificates cannot be deleted');
END;
CREATE TRIGGER certificates_revoke_once
BEFORE UPDATE OF revoked ON certificates
WHEN OLD.revoked = 1 AND NEW.revoked = 0
BEGIN
    SELECT RAISE(ABORT, 'a revoked certificate cannot be un-revoked');
END;

CREATE TABLE api_tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    name       TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    prefix     TEXT NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 1,
    last_used  TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    company_id INTEGER,
    user_id    INTEGER,
    actor      TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL,
    entity     TEXT NOT NULL DEFAULT '',
    entity_id  TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '',
    ip         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_audit_company ON audit_log(company_id, ts);
CREATE TRIGGER audit_no_edit   BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;

CREATE TABLE email_outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    to_addr    TEXT NOT NULL,
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent       INTEGER NOT NULL DEFAULT 0,
    error      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE demo_requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL,
    company    TEXT NOT NULL DEFAULT '',
    message    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
