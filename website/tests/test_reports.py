"""Every report type, both formats, access rules, history, and the public verification page."""
import pytest

from helpers import Engine
from test_engine_certs import push, station, trust
from test_rbac import mk_job_asset

STAFF_TYPES = ["hardware_testing", "customer_assets", "technician_performance", "job_completion", "failed_devices", "monthly_processing"]


def prepare(co):
    job, a = mk_job_asset(co)
    eng, hdr = Engine(), station(co)
    trust(co, eng)
    res = push(co, hdr, eng.report(job["job_ref"], a["asset_tag"], a["serial"]))
    assert res.status_code == 200
    return job, a, res.json()


@pytest.mark.parametrize("fmt,magic", [("pdf", b"%PDF"), ("csv", b"")])
def test_every_report_type_generates(co, fmt, magic):
    job, a, _ = prepare(co)
    for kind in STAFF_TYPES:
        params = {"month": "2026-11"} if kind == "monthly_processing" else {}
        if kind == "customer_assets":
            params["customer_id"] = co.customer_id
        r = co.admin.post("/api/reports", {"type": kind, "format": fmt, "params": params})
        assert r.status_code == 200, (kind, r.text)
        d = co.admin.get("/api/reports/%d/download" % r.json()["id"])
        assert d.status_code == 200 and d.content.startswith(magic) and len(d.content) > 50
    r = co.tech.post("/api/reports", {"type": "laptop_report", "format": fmt, "params": {"asset_id": a["id"]}})
    assert r.status_code == 200, r.text
    assert co.tech.get("/api/reports/%d/download" % r.json()["id"]).content.startswith(magic)


def test_history_search_and_scoping(co, co2):
    prepare(co)
    r = co.admin.post("/api/reports", {"type": "job_completion", "format": "csv"}).json()
    hist = co.admin.get("/api/reports").json()
    assert any(h["id"] == r["id"] for h in hist)
    assert co.admin.get("/api/reports?q=completion").json()
    # another company can neither list nor download it
    assert co2.admin.get("/api/reports").json() == []
    assert co2.admin.get("/api/reports/%d/download" % r["id"]).status_code == 404


def test_customer_report_limits(co):
    prepare(co)
    t = co.customer.get("/api/report-types").json()
    kinds = {x["type"] for x in t}
    assert "technician_performance" not in kinds and "laptop_report" not in kinds
    assert co.customer.post("/api/reports", {"type": "technician_performance", "format": "csv"}).status_code in (403, 404)
    assert co.customer.post("/api/reports", {"type": "laptop_report", "format": "pdf", "params": {"asset_id": 1}}).status_code == 403
    assert co.customer.post("/api/reports", {"type": "bogus", "format": "pdf"}).status_code in (404, 422)


def test_public_verify_page_and_api(co, anon):
    job, a, res = prepare(co)
    er = co.admin.get("/api/assets/%d" % a["id"]).json()["erasures"][0]
    cert = co.admin.post("/api/erasures/%d/certificate" % er["id"]).json()
    v = anon.get("/api/public/verify/" + cert["number"])
    assert v.status_code == 200 and v.json()["state"] == "VALID"
    assert a["serial"] not in str(v.json())          # serial is masked on the public page
    assert anon.get("/api/public/verify/NOPE-0000").json()["state"] == "NOT FOUND"
    assert anon.get("/verify/" + cert["number"]).status_code == 200
