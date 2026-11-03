-- Tamper protection enforced by the database itself (SQLite).
CREATE TRIGGER certificates_no_update BEFORE UPDATE ON certificates
BEGIN SELECT RAISE(ABORT, 'certificates are immutable'); END;
CREATE TRIGGER certificates_no_delete BEFORE DELETE ON certificates
BEGIN SELECT RAISE(ABORT, 'certificates are immutable'); END;
CREATE TRIGGER revocations_no_update BEFORE UPDATE ON certificate_revocations
BEGIN SELECT RAISE(ABORT, 'revocations are append only'); END;
CREATE TRIGGER revocations_no_delete BEFORE DELETE ON certificate_revocations
BEGIN SELECT RAISE(ABORT, 'revocations are append only'); END;
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append only'); END;
CREATE TRIGGER submissions_no_update BEFORE UPDATE OF payload, sha256, signed, signature_ok, key_id ON submissions
BEGIN SELECT RAISE(ABORT, 'engine submissions are evidence and cannot be edited'); END;
CREATE TRIGGER submissions_no_delete BEFORE DELETE ON submissions
BEGIN SELECT RAISE(ABORT, 'engine submissions cannot be deleted'); END;
-- An erasure record that a certificate was issued for can no longer change.
CREATE TRIGGER erasures_locked_update BEFORE UPDATE ON erasures
WHEN EXISTS (SELECT 1 FROM certificates WHERE erasure_id = OLD.id)
BEGIN SELECT RAISE(ABORT, 'erasure record is locked by a certificate'); END;
CREATE TRIGGER erasures_locked_delete BEFORE DELETE ON erasures
WHEN EXISTS (SELECT 1 FROM certificates WHERE erasure_id = OLD.id)
BEGIN SELECT RAISE(ABORT, 'erasure record is locked by a certificate'); END;
