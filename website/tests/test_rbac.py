"""Role-based access and tenant isolation."""


def mk_job_asset(co, tag="AST-1", serial="SN0001"):
    cust = co.customer_id
    job = co.admin.post("/api/jobs", {"title": "Batch", "customer_id": cust}).json()
    a = co.admin.post("/api/assets", {"asset_tag": tag, "serial": serial, "job_id": job["id"], "manufacturer": "HP", "model": "840"})
    assert a.status_code == 200, a.text
    return job, a.json()


def test_technician_cannot_manage_users_or_company(co):
    assert co.tech.get("/api/users").status_code == 403
    assert co.tech.post("/api/users", {"name": "x", "email": "x@x.test", "role": "technician"}).status_code == 403
    assert co.tech.put("/api/companies/%d" % co.company_id, {"name": "Hacked"}).status_code == 403
    assert co.tech.get("/api/audit").status_code == 403
    assert co.tech.get("/api/stations").status_code == 403


def test_admin_cannot_create_super_admin(co):
    r = co.admin.post("/api/users", {"name": "Evil", "email": "evil@x.test", "role": "super_admin"})
    assert r.status_code == 403


def test_customer_is_read_only_and_limited(co):
    job, a = mk_job_asset(co)
    assert co.customer.get("/api/assets").json()["total"] == 1
    for url, body in (("/api/assets", {"asset_tag": "X", "serial": "Y"}), ("/api/jobs", {"title": "t"}), ("/api/customers", {"name": "c"}),
                      ("/api/stations", {"name": "s"}), ("/api/assets/%d/tests" % a["id"], {"component": "CPU", "status": "PASS"})):
        assert co.customer.post(url, body).status_code == 403, url
    assert co.customer.get("/api/customers").status_code == 403
    assert co.customer.get("/api/submissions").status_code == 403
    assert co.customer.get("/api/audit").status_code == 403
    assert co.customer.get("/api/assets/%d" % a["id"]).json()["submissions"] == []


def test_customer_cannot_see_another_customers_assets(co):
    job, a = mk_job_asset(co)
    other = co.admin.post("/api/customers", {"name": "Other customer"}).json()
    job2 = co.admin.post("/api/jobs", {"title": "Other batch", "customer_id": other["id"]}).json()
    a2 = co.admin.post("/api/assets", {"asset_tag": "AST-2", "serial": "SN0002", "job_id": job2["id"]}).json()
    ids = [x["id"] for x in co.customer.get("/api/assets").json()["items"]]
    assert ids == [a["id"]]
    assert co.customer.get("/api/assets/%d" % a2["id"]).status_code == 404
    assert [j["id"] for j in co.customer.get("/api/jobs").json()] == [job["id"]]
    assert co.customer.get("/api/jobs/%d" % job2["id"]).status_code == 404


def test_company_isolation(co, co2):
    job, a = mk_job_asset(co, "SHARED-TAG", "SN-A")
    job2, b = mk_job_asset(co2, "SHARED-TAG", "SN-B")      # same asset tag in another company is fine
    assert co.admin.get("/api/assets").json()["total"] == 1
    assert co2.admin.get("/api/assets/%d" % a["id"]).status_code == 404
    assert co2.admin.put("/api/assets/%d" % a["id"], {"model": "x"}).status_code == 404
    assert co2.tech.post("/api/assets/%d/tests" % a["id"], {"component": "CPU", "status": "PASS"}).status_code == 404
    assert co2.admin.put("/api/companies/%d" % co.company_id, {"name": "x"}).status_code == 404
    assert co2.admin.put("/api/users/%d" % co.tech.user["id"], {"active": False}).status_code == 404
    assert co2.admin.get("/api/jobs/%d" % job["id"]).status_code == 404
    assert co2.admin.post("/api/assets", {"asset_tag": "Z", "serial": "Z", "job_id": job["id"]}).status_code == 422
    assert co2.admin.get("/api/certificates").json() == []


def test_duplicate_asset_tag_rejected(co):
    mk_job_asset(co)
    r = co.admin.post("/api/assets", {"asset_tag": "AST-1", "serial": "OTHER"})
    assert r.status_code == 409


def test_technician_register_and_test_manually(co):
    job, a = mk_job_asset(co)
    r = co.tech.post("/api/assets/%d/tests" % a["id"], {"component": "Keyboard", "status": "PASS", "notes": "all keys"})
    assert r.status_code == 200
    assert [t for t in r.json()["tests"] if t["component"] == "Keyboard"][0]["status"] == "PASS"
    assert co.tech.post("/api/assets/%d/tests" % a["id"], {"component": "Flux", "status": "PASS"}).status_code == 422
    assert co.tech.post("/api/assets/%d/tests" % a["id"], {"component": "CPU", "status": "GREAT"}).status_code == 422


def test_audit_log_records_and_chain_is_intact(co):
    mk_job_asset(co)
    rows = co.admin.get("/api/audit").json()
    actions = {r["action"] for r in rows}
    assert {"asset.created", "job.created", "customer.created"} <= actions
    assert co.admin.get("/api/audit/verify").json()["intact"] is True


def test_logo_validation(co):
    assert co.admin.put("/api/companies/%d" % co.company_id, {"logo_data": "data:text/html;base64,PHNjcmlwdD4="}).status_code == 422
    png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    assert co.admin.put("/api/companies/%d" % co.company_id, {"logo_data": png}).status_code == 200


def test_dashboard_numbers(co):
    mk_job_asset(co)
    d = co.admin.get("/api/dashboard").json()
    assert d["total_assets"] == 1 and d["certificates"] == 0 and d["jobs_open"] == 1
    assert co.customer.get("/api/dashboard").json()["total_assets"] == 1
