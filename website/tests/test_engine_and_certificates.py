import copy
import json
import sqlite3

import pytest
from cryptography.hazmat.primitives import serialization

from conftest import post
from app.core.db import get_db, many, one, run
from app.integrations import simulator
from app.integrations.engine import ingest_report
from app.certificates.service import eligible_erasures, issue_certificate, revoke_certificate, verify_certificate


@pytest.fixture()
def demo_key(instance_dir):
    return serialization.load_pem_private_key(open(instance_dir / "demo_signing_key.pem", "rb").read(), None)


def company_id():
    return one("SELECT id FROM companies WHERE is_demo = 1")["id"]


def hdr(creds):
    return {"Authorization": "Bearer " + creds["api_token"][1]}


def make(demo_key, tag, serial, scenario="pass", job="DEMO-J-1001"):
    return simulator.make_report(demo_key, job, tag, serial, "Ali Hassan", scenario=scenario)


def test_api_requires_token(app):
    c = app.test_client()
    assert c.get("/api/v1/engine/ping").status_code == 401
    assert c.get("/api/v1/engine/ping", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.post("/api/v1/engine/reports", json={}).status_code == 401


def test_api_ping_and_job_ticket(app, creds):
    c = app.test_client()
    assert c.get("/api/v1/engine/ping", headers=hdr(creds)).get_json()["ok"] is True
    t = c.get("/api/v1/engine/jobs/DEMO-J-1001", headers=hdr(creds))
    assert t.status_code == 200 and t.get_json()["job_ref"] == "DEMO-J-1001"
    assert c.get("/api/v1/engine/jobs/NOPE", headers=hdr(creds)).status_code == 404


def test_api_has_no_way_to_start_wipes_or_issue_certificates(app):
    rules = [str(r) for r in app.url_map.iter_rules() if r.endpoint.startswith("engine_api")]
    assert sorted(rules) == ["/api/v1/engine/jobs/<job_ref>", "/api/v1/engine/ping", "/api/v1/engine/reports"]


def test_valid_signed_report_ingested_over_api(app, creds, demo_key):
    rep = make(demo_key, "T-API-1", "TESTSN100001")
    r = app.test_client().post("/api/v1/engine/reports", json=rep, headers=hdr(creds))
    assert r.status_code == 201, r.get_data(True)
    with app.app_context():
        row = one("SELECT * FROM engine_reports WHERE id = ?", (r.get_json()["report_id"],))
        assert row["signature_status"] == "valid"
        assert many("SELECT * FROM erasures WHERE report_id = ?", (row["id"],))


def test_bad_json_and_bad_shape_rejected(app, creds):
    c = app.test_client()
    assert c.post("/api/v1/engine/reports", data="not json", headers=hdr(creds)).status_code == 400
    assert c.post("/api/v1/engine/reports", json={"hello": 1}, headers=hdr(creds)).status_code == 422


def test_tampered_report_is_flagged_and_cannot_certify(app, demo_key):
    rep = make(demo_key, "T-TAMP-1", "TESTSN100002")
    rep["erasures"][0]["status"] = "Success"
    rep["model"] = "Changed after signing"
    with app.app_context():
        res = ingest_report(company_id(), rep)
        get_db().commit()
        row = one("SELECT signature_status FROM engine_reports WHERE id = ?", (res["report_id"],))
        assert row["signature_status"] == "tampered"
        el = eligible_erasures(res["asset_id"])
        assert not el["ok"] and el["blockers"]


def test_unsigned_report_cannot_certify(app, demo_key):
    with app.app_context():
        res = ingest_report(company_id(), make(demo_key, "T-UNS-1", "TESTSN100003", "unsigned"))
        get_db().commit()
        assert one("SELECT signature_status FROM engine_reports WHERE id = ?", (res["report_id"],))["signature_status"] == "unsigned"
        assert not eligible_erasures(res["asset_id"])["ok"]


def test_unknown_key_cannot_certify(app):
    other = simulator.new_demo_key()
    rep = simulator.make_report(other, "DEMO-J-1001", "T-UNK-1", "TESTSN100004", "Ali Hassan")
    with app.app_context():
        res = ingest_report(company_id(), rep)
        get_db().commit()
        assert one("SELECT signature_status FROM engine_reports WHERE id = ?", (res["report_id"],))["signature_status"] == "unknown_key"
        assert not eligible_erasures(res["asset_id"])["ok"]


@pytest.mark.parametrize("scenario", ["wipe_failed", "not_verified", "test_mode"])
def test_unverified_or_failed_wipes_never_get_certificates(app, demo_key, scenario):
    serial = "TESTSN2%05d" % abs(hash(scenario) % 99999)
    with app.app_context():
        res = ingest_report(company_id(), make(demo_key, "T-" + scenario[:6], serial, scenario))
        get_db().commit()
        el = eligible_erasures(res["asset_id"])
        assert not el["ok"]
        admin = one("SELECT * FROM users WHERE role = 'company_admin'")
        for e in many("SELECT id FROM erasures WHERE report_id = ?", (res["report_id"],)):
            with pytest.raises(ValueError):  # forcing issue directly must also be refused
                issue_certificate(e["id"], user=admin)
        assert one("SELECT COUNT(*) AS n FROM certificates WHERE asset_id = ?", (res["asset_id"],))["n"] == 0


def test_wiped_report_makes_failed_asset_status(app, demo_key):
    with app.app_context():
        res = ingest_report(company_id(), make(demo_key, "T-FAIL-1", "TESTSN100005", "wipe_failed"))
        get_db().commit()
        assert one("SELECT status FROM assets WHERE id = ?", (res["asset_id"],))["status"] in ("failed", "incomplete")


def test_serial_clash_with_asset_tag_blocks_certificate(app, demo_key):
    with app.app_context():
        first = ingest_report(company_id(), make(demo_key, "T-CLASH-1", "TESTSN100006"))
        second = ingest_report(company_id(), make(demo_key, "T-CLASH-1", "TESTSN100007"))
        get_db().commit()
        assert not eligible_erasures(second["asset_id"])["ok"] or first["asset_id"] != second["asset_id"]


def test_issue_verify_tamper_revoke_cycle(app, login, demo_key):
    with app.app_context():
        res = ingest_report(company_id(), make(demo_key, "T-CERT-1", "TESTSN100008"))
        get_db().commit()
        admin = one("SELECT * FROM users WHERE role = 'company_admin'")
        el = eligible_erasures(res["asset_id"])
        assert el["ok"]
        out = issue_certificate(el["erasures"][0]["id"], user=admin)
        get_db().commit()
        number, cid = out["number"], out["id"]
        assert number.startswith("DEMO-")  # demo key, so clearly marked
        assert not eligible_erasures(res["asset_id"])["ok"]  # cannot be issued twice
        assert verify_certificate(number)["state"] == "valid"

        db = get_db()
        for sql in ("UPDATE certificates SET number = 'X' WHERE id = %d" % cid,
                    "UPDATE certificates SET content_json = '{}' WHERE id = %d" % cid,
                    "DELETE FROM certificates WHERE id = %d" % cid):
            with pytest.raises(sqlite3.DatabaseError):
                db.execute(sql)
        db.rollback()

    c = login("admin")
    assert c.get("/certificates/%d/pdf" % cid).data.startswith(b"%PDF")
    pub = app.test_client().get("/verify/" + number).get_data(True)
    assert number in pub and "TESTSN100008" not in pub  # serial is masked on the public page

    with app.app_context():
        revoke_certificate(cid, "Issued in error", one("SELECT * FROM users WHERE role = 'company_admin'"))
        get_db().commit()
        assert verify_certificate(number)["state"] == "revoked"
        with pytest.raises(sqlite3.DatabaseError):
            get_db().execute("UPDATE certificates SET revoked = 0 WHERE id = %d" % cid)
        get_db().rollback()


def test_content_tamper_detected_by_seal(app, demo_key):
    """Even if someone bypasses the triggers (e.g. edits the file offline) verification fails."""
    with app.app_context():
        res = ingest_report(company_id(), make(demo_key, "T-SEAL-1", "TESTSN100009"))
        get_db().commit()
        admin = one("SELECT * FROM users WHERE role = 'company_admin'")
        out = issue_certificate(eligible_erasures(res["asset_id"])["erasures"][0]["id"], user=admin)
        get_db().commit()
        db = get_db()
        db.execute("DROP TRIGGER certificates_no_edit")
        db.execute("UPDATE certificates SET content_json = replace(content_json, 'TESTSN100009', 'FORGED') WHERE id = ?", (out["id"],))
        assert verify_certificate(out["number"])["state"] != "valid"
        db.rollback()


def test_public_verify_unknown_number_gives_no_details(app):
    r = app.test_client().get("/verify/ITAD-2026-000000000000")
    assert r.status_code in (200, 404)
    assert "serial" not in r.get_data(True).lower() or r.status_code == 404


def test_issue_requires_login_and_csrf(app, login):
    c = app.test_client()
    assert c.post("/assets/1/issue").status_code in (302, 400, 403)
    a = login("admin")
    assert a.post("/assets/1/issue", data={}).status_code == 400


def test_reports_exports(login):
    c = login("admin")
    for k in ("monthly", "customer_assets", "job_completion", "failed_devices", "technician_performance", "erasure_certificates"):
        r = c.get("/reports/%s.csv" % k)
        assert r.status_code == 200 and r.mimetype == "text/csv", k
        assert c.get("/reports/%s.pdf" % k).data.startswith(b"%PDF"), k
    assert c.get("/dashboard.csv").status_code == 200
    j = c.get("/api/v1/dashboard/stats").get_json()
    assert "totals" in j or "total" in json.dumps(j).lower()


def test_individual_pdfs(app, login):
    c = login("admin")
    with app.app_context():
        ids = [r["id"] for r in many("SELECT id FROM assets ORDER BY id LIMIT 5")]
    for i in ids:
        assert c.get("/reports/asset/%d.pdf" % i).data.startswith(b"%PDF")
        assert c.get("/reports/asset/%d/hardware.pdf" % i).data.startswith(b"%PDF")
