-- Tamper protection enforced by the database itself (PostgreSQL).
CREATE FUNCTION itad_block() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% is protected: % not allowed', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION itad_block_locked_erasure() RETURNS trigger AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM certificates WHERE erasure_id = OLD.id) THEN
    RAISE EXCEPTION 'erasure record is locked by a certificate';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER certificates_immutable BEFORE UPDATE OR DELETE ON certificates
  FOR EACH ROW EXECUTE FUNCTION itad_block();
CREATE TRIGGER revocations_append_only BEFORE UPDATE OR DELETE ON certificate_revocations
  FOR EACH ROW EXECUTE FUNCTION itad_block();
CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION itad_block();
CREATE TRIGGER submissions_no_delete BEFORE DELETE ON submissions
  FOR EACH ROW EXECUTE FUNCTION itad_block();
CREATE TRIGGER submissions_no_edit BEFORE UPDATE OF payload, sha256, signed, signature_ok, key_id ON submissions
  FOR EACH ROW EXECUTE FUNCTION itad_block();
CREATE TRIGGER erasures_locked BEFORE UPDATE OR DELETE ON erasures
  FOR EACH ROW EXECUTE FUNCTION itad_block_locked_erasure();
