import re

from conftest import token_from
from app.core.db import one


def _form(c, url, data):
    tok = token_from(c.get(url).get_data(True))
    return c.post(url, data=dict(data, _csrf=tok))


def _last_link(app, to, pattern):
    with app.app_context():
        row = one("SELECT body FROM email_outbox WHERE to_addr = ? ORDER BY id DESC", (to,))
    assert row, "no email saved for " + to
    return re.search(pattern, row["body"]).group(0)


def test_register_verify_login_and_reset(app):
    c = app.test_client()
    email, pw = "new.person@example.org", "Sup3r-secret-pw"
    r = _form(c, "/register", {"company": "Acme Recyclers", "full_name": "New Person", "email": email,
                               "password": pw, "confirm": pw})
    assert r.status_code == 200

    # Cannot sign in before the email is verified.
    r = _form(c, "/login", {"email": email, "password": pw})
    assert r.status_code != 302

    link = _last_link(app, email, r"/verify-email/[\w\-]+")
    assert c.get(link).status_code in (200, 302)
    r = _form(c, "/login", {"email": email, "password": pw})
    assert r.status_code == 302
    # New company starts empty and the person is its administrator.
    assert c.get("/dashboard").status_code == 200
    assert c.get("/team").status_code == 200
    assert c.get("/admin/companies").status_code == 403
    c.post("/logout", data={"_csrf": token_from(c.get("/dashboard").get_data(True))})

    # Forgot / reset password, link works once.
    _form(c, "/forgot-password", {"email": email})
    link = _last_link(app, email, r"/reset-password/[\w\-]+")
    newpw = "An0ther-secret-pw"
    assert _form(c, link, {"password": "weak", "confirm": "weak"}).status_code == 400
    assert _form(c, link, {"password": newpw, "confirm": newpw}).status_code == 302
    assert c.get(link).status_code == 400
    assert _form(c, "/login", {"email": email, "password": pw}).status_code != 302
    assert _form(c, "/login", {"email": email, "password": newpw}).status_code == 302


def test_register_does_not_reveal_existing_accounts(app, creds):
    c = app.test_client()
    a = _form(c, "/register", {"company": "Dup", "full_name": "Dup", "email": creds["admin"][0],
                               "password": "Sup3r-secret-pw", "confirm": "Sup3r-secret-pw"})
    b = _form(c, "/register", {"company": "Fresh Co", "full_name": "Fresh", "email": "fresh1@example.org",
                               "password": "Sup3r-secret-pw", "confirm": "Sup3r-secret-pw"})
    assert a.status_code == b.status_code == 200
    assert "already" not in a.get_data(True).lower()


def test_forgot_password_same_answer_for_unknown_email(app):
    c = app.test_client()
    a = _form(c, "/forgot-password", {"email": "nobody@example.org"})
    b = _form(c, "/forgot-password", {"email": "admin@harbour-demo.example"})
    assert a.status_code == b.status_code == 200
    assert a.get_data(True) == b.get_data(True) or "reset link" in a.get_data(True)


def test_admin_pages_and_actions(app, login):
    c = login("admin")
    for p in ("/company", "/team", "/team/new", "/customers", "/audit"):
        assert c.get(p).status_code == 200, p
    from conftest import post
    r = post(c, "/customers", {"name": "Test Customer Ltd", "contact_name": "T", "contact_email": "t@example.org"}, page="/customers")
    assert r.status_code in (200, 302)
    assert "Test Customer Ltd" in c.get("/customers").get_data(True)
    r = post(c, "/team/new", {"full_name": "Tess Tech", "email": "tess@harbour-demo.example", "role": "technician"}, page="/team/new")
    assert r.status_code in (200, 302)
    with app.app_context():
        assert one("SELECT role FROM users WHERE email = 'tess@harbour-demo.example'")["role"] == "technician"


def test_company_admin_cannot_create_super_admin(app, login):
    from conftest import post
    c = login("admin")
    post(c, "/team/new", {"full_name": "Evil", "email": "evil@example.org", "role": "super_admin"}, page="/team/new")
    with app.app_context():
        assert one("SELECT 1 FROM users WHERE email = 'evil@example.org'") is None
