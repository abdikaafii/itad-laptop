from conftest import post, token_from
from app.core.db import many, one


def test_public_pages_load(app):
    c = app.test_client()
    for p in ("/", "/login", "/register", "/verify", "/healthz"):
        assert c.get(p).status_code == 200
    html = c.get("/").get_data(True)
    assert "Complete IT Asset Intelligence" in html


def test_security_headers_and_no_inline_style(app):
    r = app.test_client().get("/")
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "unsafe-inline" not in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert 'style="' not in r.get_data(True)


def test_app_pages_need_login(app):
    c = app.test_client()
    for p in ("/dashboard", "/assets", "/certificates", "/reports", "/team", "/audit", "/company"):
        r = c.get(p)
        assert r.status_code in (302, 401, 403), p
        assert "/login" in (r.headers.get("Location") or "")


def test_login_wrong_password_and_lockout(app, creds):
    c = app.test_client()
    email = creds["tech2"][0]
    for _ in range(6):
        tok = token_from(c.get("/login").get_data(True))
        r = c.post("/login", data={"email": email, "password": "wrong-Password1", "_csrf": tok})
    assert r.status_code in (200, 429)
    # Even the right password is refused while locked.
    tok = token_from(c.get("/login").get_data(True))
    r = c.post("/login", data={"email": email, "password": creds["tech2"][1], "_csrf": tok})
    assert r.status_code != 302
    with app.app_context():
        from app.core.db import run, get_db
        run("UPDATE users SET failed_logins = 0, locked_until = '' WHERE email = ?", (email,))
        get_db().commit()


def test_csrf_required(login):
    c = login("admin")
    r = c.post("/jobs/new", data={"title": "x"})
    assert r.status_code == 400


def test_logout_ends_session(login):
    c = login("tech1")
    assert c.get("/dashboard").status_code == 200
    post(c, "/logout")
    assert c.get("/dashboard").status_code == 302


def test_customer_sees_only_own_records(app, login):
    c = login("cust_bank")
    with app.app_context():
        mine = one("SELECT id FROM customers WHERE name LIKE 'Northgate%'")["id"]
        other_assets = many("SELECT a.id FROM assets a JOIN customers cu ON cu.id = a.customer_id WHERE cu.id != ?", (mine,))
        other_certs = many("SELECT ce.id FROM certificates ce JOIN assets a ON a.id = ce.asset_id WHERE a.customer_id != ?", (mine,))
        other_jobs = many("SELECT id FROM jobs WHERE customer_id != ?", (mine,))
    assert other_assets and other_certs and other_jobs
    for r in other_assets:
        assert c.get("/assets/%d" % r["id"]).status_code == 404
        assert c.get("/reports/asset/%d.pdf" % r["id"]).status_code == 404
    for r in other_certs:
        assert c.get("/certificates/%d" % r["id"]).status_code == 404
        assert c.get("/certificates/%d/pdf" % r["id"]).status_code == 404
    for r in other_jobs:
        assert c.get("/jobs/%d" % r["id"]).status_code == 404
    listing = c.get("/assets").get_data(True)
    assert "Westfield" not in listing


def test_customer_cannot_use_management_pages(login):
    c = login("cust_bank")
    for p in ("/team", "/audit", "/company", "/customers", "/assets/new", "/admin/companies"):
        assert c.get(p).status_code == 403, p


def test_technician_cannot_manage_team_or_see_other_techs_assets(app, login):
    c = login("tech1")
    assert c.get("/team").status_code == 403
    with app.app_context():
        carl = one("SELECT id FROM users WHERE email LIKE 'carl@%'")["id"]
        theirs = many("SELECT id FROM assets WHERE technician_id = ? AND job_id IN "
                      "(SELECT id FROM jobs WHERE assigned_technician_id = ?)", (carl, carl))
    for r in theirs:
        assert c.get("/assets/%d" % r["id"]).status_code == 404


def test_company_admin_cannot_reach_super_admin_pages(login):
    assert login("admin").get("/admin/companies").status_code == 403


def test_csv_formula_injection_guard(app):
    from app.reports.service import _safe_cell
    assert _safe_cell("=1+1").startswith("'")
    assert _safe_cell("@SUM(A1)").startswith("'")
    assert _safe_cell("normal") == "normal"


def test_weak_password_rejected(app):
    from app.core.security import password_problems
    assert password_problems("short")
    assert password_problems("alllowercase123")
    assert not password_problems("Str0ng-enough-pw")


def test_password_hash_is_not_plaintext(app, creds):
    with app.app_context():
        row = one("SELECT password_hash FROM users WHERE email = ?", (creds["admin"][0],))
    assert creds["admin"][1] not in row["password_hash"]
    assert row["password_hash"].startswith(("scrypt", "$scrypt", "pbkdf2")) or ":" in row["password_hash"] or "$" in row["password_hash"]


def test_audit_log_is_append_only(app):
    import sqlite3
    with app.app_context():
        from app.core.db import get_db
        db = get_db()
        assert many("SELECT 1 FROM audit_log LIMIT 1")
        for sql in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"):
            try:
                db.execute(sql)
                raise AssertionError("audit_log was modified")
            except sqlite3.DatabaseError:
                pass
