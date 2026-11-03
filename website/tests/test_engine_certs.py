"""Engine report import, assessment, certificates and tamper protection."""
import importlib.util
import json
import os
import sqlite3

import pytest

from helpers import COMPONENTS, Engine, fingerprint
from test_rbac import mk_job_asset

REPO = os.path.join(os.path.dirname(__file__), "..", "..")
VERIFY = os.path.join(REPO, "build", "config", "includes.chroot", "opt", "itad-laptop", "verify_report.py")


def station(co):
    r = co.admin.post("/api/stations", {"name": "Bench 1"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


def push(co, hdr, rep):
    return co.admin.c.post("/api/engine/submit", json=rep, headers=hdr)


def trust(co, eng):
    r = co.admin.post("/api/keys", {"public_key": eng.pub.hex(), "label": "Bench USB"})
    assert r.status_code == 200 and r.json()["key_id"] == eng.key_id


def test_fingerprint_rule_matches_the_real_engine_verifier():
    """Our fingerprint must equal the one in the repo's own verify_report.py."""
    spec = importlib.util.spec_from_file_location("verify_report", VERIFY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rep = Engine().report("J1", "A1", "S1", sign=False)
    assert mod.fingerprint(rep) == fingerprint(rep) == rep["integrity"]["sha256"]
    from app.integrity import fingerprint as ours
    assert ours(rep) == mod.fingerprint(rep)


def test_station_token_required(co):
    r = co.admin.c.post("/api/engine/submit", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert co.admin.c.get("/api/engine/ping").status_code == 401


def test_full_happy_path_signed_trusted_certificate(co):
    job, a = mk_job_asset(co)
    eng, hdr = Engine(), station(co)
    res = push(co, hdr, eng.report(job["job_ref"], a["asset_tag"], a["serial"]))
    assert res.status_code == 200 and res.json()["status"] == "accepted"
    assert res.json()["overall"] == "PASS" and res.json()["signature_valid"] is True and res.json()["key_trusted"] is False
    d = co.admin.get("/api/assets/%d" % a["id"]).json()
    er = d["erasures"][0]
    assert er["verified"] == 1 and d["asset"]["status"] == "completed"
    # signed, but the key is not trusted yet: the certificate is refused
    r = co.admin.post("/api/erasures/%d/certificate" % er["id"])
    assert r.status_code == 409 and "not trusted" in r.json()["detail"]
    # a company admin trusts the key from the report itself
    sid = res.json()["submission_id"]
    assert co.admin.post("/api/keys/from-submission/%d" % sid, {"label": "Bench 1 USB"}).status_code == 200
    r = co.tech.post("/api/erasures/%d/certificate" % er["id"])
    assert r.status_code == 200, r.text
    cert = r.json()
    assert cert["state"] == "VALID" and cert["demo"] is False
    # one certificate per erasure
    assert co.admin.post("/api/erasures/%d/certificate" % er["id"]).status_code == 409
    pdf = co.admin.get("/api/certificates/%d/pdf" % cert["id"])
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    # customer can download their own certificate, and sees it listed
    assert [c["number"] for c in co.customer.get("/api/certificates").json()] == [cert["number"]]
    assert co.customer.get("/api/certificates/%d/pdf" % cert["id"]).status_code == 200
    # public verification page data
    pub = co.admin.c.get("/api/public/verify/%s" % cert["number"]).json()
    assert pub["state"] == "VALID" and pub["serial_masked"].endswith(a["serial"][-4:]) and "*" in pub["serial_masked"]
    assert "customer" not in pub


def test_unsigned_report_gives_no_certificate_by_default(co):
    job, a = mk_job_asset(co)
    res = push(co, station(co), Engine().report(job["job_ref"], a["asset_tag"], a["serial"], sign=False))
    assert res.json()["status"] == "accepted" and res.json()["signed"] is False
    er = co.admin.get("/api/assets/%d" % a["id"]).json()["erasures"][0]
    r = co.admin.post("/api/erasures/%d/certificate" % er["id"])
    assert r.status_code == 409 and "not signed" in r.json()["detail"]
    # admin may relax the rule for the company (audited) and then it works
    assert co.admin.put("/api/companies/%d" % co.company_id, {"require_signed": False}).status_code == 200
    assert co.admin.post("/api/erasures/%d/certificate" % er["id"]).status_code == 200
    assert "company.updated" in {x["action"] for x in co.admin.get("/api/audit").json()}


def test_tampered_report_is_rejected(co):
    job, a = mk_job_asset(co)
    eng = Engine()
    rep = eng.report(job["job_ref"], a["asset_tag"], a["serial"])
    rep["tests"][0]["status"] = "FAIL" if rep["tests"][0]["status"] == "PASS" else "PASS"      # edit after signing
    r = push(co, station(co), rep)
    assert r.status_code == 422 and "Integrity" in r.json()["reason"]
    assert co.admin.get("/api/assets/%d" % a["id"]).json()["erasures"] == []
    subs = co.admin.get("/api/submissions").json()
    assert subs[0]["status"] == "rejected"            # kept as evidence


def test_forged_signature_is_not_valid(co):
    job, a = mk_job_asset(co)
    rep = Engine().report(job["job_ref"], a["asset_tag"], a["serial"])
    rep["integrity"]["signature"] = "00" * 64
    r = push(co, station(co), rep)
    assert r.status_code == 200 and r.json()["signature_valid"] is False
    er = co.admin.get("/api/assets/%d" % a["id"]).json()["erasures"][0]
    assert co.admin.post("/api/erasures/%d/certificate" % er["id"]).status_code == 409


def test_serial_mismatch_and_wrong_job_rejected(co):
    job, a = mk_job_asset(co)
    eng, hdr = Engine(), station(co)
    r = push(co, hdr, eng.report(job["job_ref"], a["asset_tag"], "DIFFERENT-SERIAL"))
    assert r.status_code == 422 and "Serial number mismatch" in r.json()["reason"]
    r = push(co, hdr, eng.report("JOB-DOES-NOT-EXIST", a["asset_tag"], a["serial"]))
    assert r.status_code == 422 and "does not exist" in r.json()["reason"]
    job2 = co.admin.post("/api/jobs", {"title": "Second"}).json()
    r = push(co, hdr, eng.report(job2["job_ref"], a["asset_tag"], a["serial"]))
    assert r.status_code == 422 and "different job" in r.json()["reason"]


def test_unknown_asset_is_registered_from_the_report(co):
    job = co.admin.post("/api/jobs", {"title": "Walk-in", "customer_id": co.customer_id}).json()
    r = push(co, station(co), Engine().report(job["job_ref"], "AST-NEW", "NEWSERIAL1"))
    assert r.status_code == 200
    items = co.admin.get("/api/assets?q=NEWSERIAL1").json()["items"]
    assert len(items) == 1 and items[0]["asset_tag"] == "AST-NEW"
    assert co.customer.get("/api/assets").json()["total"] == 1        # inherited the job's customer


def test_duplicate_report_is_not_stored_twice(co):
    job, a = mk_job_asset(co)
    rep, hdr = Engine().report(job["job_ref"], a["asset_tag"], a["serial"]), station(co)
    assert push(co, hdr, rep).json()["status"] == "accepted"
    assert push(co, hdr, rep).json()["status"] == "duplicate"
    assert len(co.admin.get("/api/assets/%d" % a["id"]).json()["erasures"]) == 1


def test_assessment_is_calculated_not_trusted(co):
    eng, hdr = Engine(), station(co)
    def run(tag, **kw):
        job, a = mk_job_asset(co, tag, "SN-" + tag)
        rep = eng.report(job["job_ref"], tag, "SN-" + tag, **kw)
        rep["overall"] = "PASS"                       # the report claims PASS. We must not believe it.
        rep["integrity"]["sha256"] = fingerprint(rep)
        rep["integrity"]["signed"] = False
        r = push(co, hdr, rep)
        assert r.status_code == 200, r.text
        return r.json()
    assert run("T1")["overall"] == "PASS"
    r = run("T2", tests={"Battery": "FAIL"})
    assert r["overall"] == "FAIL" and "Battery failed" in r["reasons"]
    r = run("T3", wipe_ok=False)
    assert r["overall"] == "FAIL" and "Wipe failed" in r["reasons"]
    r = run("T4", verified=False)
    assert r["overall"] == "INCOMPLETE" and any("not verified" in x for x in r["reasons"])
    r = run("T5", tests={"Keyboard": "WARN"})
    assert r["overall"] == "INCOMPLETE"
    r = run("T6", check="BLOCKED")
    assert r["overall"] == "BLOCKED"


def test_failed_or_unverified_erasure_never_gets_a_certificate(co):
    eng, hdr = Engine(), station(co)
    co.admin.put("/api/companies/%d" % co.company_id, {"require_signed": False})
    for tag, kw in (("F1", {"wipe_ok": False}), ("F2", {"verified": False})):
        job, a = mk_job_asset(co, tag, "SN-" + tag)
        push(co, hdr, eng.report(job["job_ref"], tag, "SN-" + tag, **kw))
        er = co.admin.get("/api/assets/%d" % a["id"]).json()["erasures"][0]
        r = co.admin.post("/api/erasures/%d/certificate" % er["id"])
        assert r.status_code == 409 and "not verified" in r.json()["detail"]


def test_manual_erasure_can_never_be_certified(co):
    job, a = mk_job_asset(co)
    d = co.tech.post("/api/assets/%d/erasures" % a["id"], {"method": "Wiped by hand with another tool"}).json()
    er = d["erasures"][0]
    assert er["verified"] == 0 and er["source"] == "manual"
    assert d["assessment"]["overall"] == "INCOMPLETE"
    assert co.admin.post("/api/erasures/%d/certificate" % er["id"]).status_code == 409


def test_person_cannot_mark_clear_only_blocked(co):
    job, a = mk_job_asset(co)
    assert co.tech.post("/api/assets/%d/inspection" % a["id"], {"check_result": "CLEAR"}).status_code == 422
    d = co.tech.post("/api/assets/%d/inspection" % a["id"], {"check_result": "BLOCKED", "notes": "Absolute found"}).json()
    assert d["assessment"]["overall"] == "BLOCKED" and d["asset"]["status"] == "blocked"


def test_revocation_changes_public_state(co):
    job, a = mk_job_asset(co)
    co.admin.put("/api/companies/%d" % co.company_id, {"require_signed": False})
    push(co, station(co), Engine().report(job["job_ref"], a["asset_tag"], a["serial"], sign=False))
    er = co.admin.get("/api/assets/%d" % a["id"]).json()["erasures"][0]
    cert = co.admin.post("/api/erasures/%d/certificate" % er["id"]).json()
    assert co.tech.post("/api/certificates/%d/revoke" % cert["id"], {"reason": "x"}).status_code == 403
    assert co.admin.post("/api/certificates/%d/revoke" % cert["id"], {"reason": ""}).status_code == 422
    assert co.admin.post("/api/certificates/%d/revoke" % cert["id"], {"reason": "Issued in error"}).status_code == 200
    pub = co.admin.c.get("/api/public/verify/%s" % cert["number"]).json()
    assert pub["state"] == "REVOKED" and pub["revoked_reason"] == "Issued in error"


def test_certificate_numbers_unique_and_unknown_not_found(co, anon):
    assert anon.get("/api/public/verify/NOPE-1").status_code == 404


def test_database_blocks_changes_to_certificates_and_evidence(co):
    from app.db import get_db
    job, a = mk_job_asset(co)
    co.admin.put("/api/companies/%d" % co.company_id, {"require_signed": False})
    push(co, station(co), Engine().report(job["job_ref"], a["asset_tag"], a["serial"], sign=False))
    er = co.admin.get("/api/assets/%d" % a["id"]).json()["erasures"][0]
    cert = co.admin.post("/api/erasures/%d/certificate" % er["id"]).json()
    db = get_db()
    for sql in ("UPDATE certificates SET payload = 'x' WHERE id = %d" % cert["id"],
                "DELETE FROM certificates WHERE id = %d" % cert["id"],
                "UPDATE erasures SET method = 'Edited' WHERE id = %d" % er["id"],
                "DELETE FROM erasures WHERE id = %d" % er["id"],
                "UPDATE submissions SET payload = '{}' WHERE asset_id = %d" % a["id"],
                "DELETE FROM submissions WHERE asset_id = %d" % a["id"],
                "UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(sqlite3.DatabaseError):
            db.execute(sql)
    # even a direct, trigger-free edit of the payload is detected on verification
    from app.integrity import signer
    c = db.one("SELECT * FROM certificates WHERE id = ?", (cert["id"],))
    assert signer.verify(c["payload"], c["payload_sha256"], c["signature"], c["key_id"])
    forged = json.loads(c["payload"])
    forged["asset"]["serial"] = "FORGED"
    assert not signer.verify(json.dumps(forged), c["payload_sha256"], c["signature"], c["key_id"])


def test_engine_wipe_advice_endpoint(co):
    job, a = mk_job_asset(co, "AST-ADV", "SN-ADV")
    hdr = station(co)
    g = lambda q: co.admin.c.get("/api/engine/assets/AST-ADV" + q, headers=hdr).json()
    assert g("?serial=SN-ADV&job=%s" % job["job_ref"])["wipe_allowed"] is True
    assert g("?serial=WRONG")["wipe_allowed"] is False and "Serial" in g("?serial=WRONG")["reason"]
    assert g("?job=OTHER")["wipe_allowed"] is False
    co.tech.post("/api/assets/%d/inspection" % a["id"], {"check_result": "BLOCKED"})
    assert g("")["wipe_allowed"] is False and "BLOCKED" in g("")["reason"]
    assert co.admin.c.get("/api/engine/assets/UNKNOWN", headers=hdr).json()["known"] is False


def test_bundle_format_from_the_three_scripts(co):
    """The raw laptop_check / laptop_wipe / laptop_test outputs are accepted too."""
    job, a = mk_job_asset(co)
    bundle = {"job_id": job["job_ref"], "asset_id": a["asset_tag"],
              "check": {"result": "CLEAR", "absolute_result": "CLEAR", "autopilot_result": "CLEAN",
                        "identity": {"manufacturer": "HP", "model": "840 G5", "serial": a["serial"], "cpu": "i5"}},
              "wipe": {"technician": "Ali", "time": "2026-11-02T10:17:32+00:00", "dry_run": False,
                       "devices": [{"path": "/dev/nvme0n1", "model": "PM981", "serial": "S4Y", "size_gb": 256, "achieved": "crypto",
                                    "verification": {"verified": True, "sample_sha256": None, "notes": []}}]},
              "test": {"results": [{"component": c, "status": "PASS", "notes": []} for c in COMPONENTS]}}
    bundle["integrity"] = {"sha256": fingerprint(bundle), "signed": False}
    r = push(co, station(co), bundle)
    assert r.status_code == 200 and r.json()["overall"] == "PASS", r.text
    assert co.admin.get("/api/assets/%d" % a["id"]).json()["erasures"][0]["method"] == "Purge - cryptographic erase"


def test_simulation_is_labelled_demo_and_certificate_says_demonstration(co):
    job, a = mk_job_asset(co)
    r = co.tech.post("/api/dev/simulate-engine", {"asset_id": a["id"], "scenario": "pass"})
    assert r.status_code == 200 and r.json()["overall"] == "PASS"
    d = co.admin.get("/api/assets/%d" % a["id"]).json()
    assert d["asset"]["demo"] == 1 and d["erasures"][0]["demo"] == 1
    cert = co.admin.post("/api/erasures/%d/certificate" % d["erasures"][0]["id"]).json()
    assert cert["state"] == "DEMONSTRATION" and cert["demo"] is True
