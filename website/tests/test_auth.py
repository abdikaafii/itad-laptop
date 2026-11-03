import itertools

from starlette.testclient import TestClient

from app.main import app
from conftest import PW, Session

_n = itertools.count(1000)


def test_register_verify_login_flow():
    c = TestClient(app)
    n = next(_n)
    email = "flow%d@x.test" % n
    r = c.post("/api/auth/register", json={"name": "Flo", "email": email, "password": PW, "company_name": "Flow Co %d" % n})
    assert r.status_code == 200
    # cannot sign in before the e-mail is verified
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 403
    assert c.post("/api/auth/verify-email", json={"token": r.json()["dev_token"]}).status_code == 200
    s = Session(email)
    assert s.user["role"] == "company_admin" and s.user["company_name"] == "Flow Co %d" % n
    # token works once only
    assert c.post("/api/auth/verify-email", json={"token": r.json()["dev_token"]}).status_code == 400


def test_weak_password_rejected():
    c = TestClient(app)
    for pw in ("short1A!", "alllowercaseletters", "NoDigitsOrSymbolsHere"):
        r = c.post("/api/auth/register", json={"name": "A", "email": "weak%d@x.test" % next(_n), "password": pw, "company_name": "W"})
        assert r.status_code == 422, pw


def test_registration_does_not_reveal_existing_email(co):
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"name": "Dup", "email": co.admin.user["email"], "password": PW, "company_name": "Dup Co"})
    assert r.status_code == 200 and "dev_token" not in r.json()


def test_lockout_after_wrong_passwords(co):
    c = TestClient(app)
    email = co.tech.user["email"]
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": email, "password": "wrong-Password1!"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 423


def test_csrf_required_for_changes(co):
    r = co.admin.c.post("/api/customers", json={"name": "No token"})
    assert r.status_code == 403
    assert co.admin.post("/api/customers", {"name": "With token"}).status_code == 200


def test_logout_ends_session(co):
    assert co.tech.get("/api/auth/me").json()["user"]["role"] == "technician"
    co.tech.post("/api/auth/logout")
    assert co.tech.get("/api/auth/me").json()["user"] is None
    assert co.tech.get("/api/dashboard").status_code == 401


def test_password_reset_flow(co):
    c = TestClient(app)
    r = c.post("/api/auth/forgot", json={"email": co.tech.user["email"]})
    tok = r.json()["dev_token"]
    # unknown address looks identical and gives no token
    assert "dev_token" not in c.post("/api/auth/forgot", json={"email": "nobody@x.test"}).json()
    assert c.post("/api/auth/reset", json={"token": tok, "password": "weak"}).status_code == 422
    assert c.post("/api/auth/reset", json={"token": tok, "password": "New-Passw0rd!!"}).status_code == 200
    assert c.post("/api/auth/reset", json={"token": tok, "password": "New-Passw0rd!!"}).status_code == 400
    assert Session(co.tech.user["email"], "New-Passw0rd!!")
    # old sessions were killed by the reset
    assert co.tech.get("/api/dashboard").status_code == 401


def test_session_cookie_flags(co):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": co.admin.user["email"], "password": PW})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_security_headers(anon):
    r = anon.get("/api/health")
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_rate_limit_blocks_brute_force(monkeypatch):
    from app import api, config
    monkeypatch.setattr(config, "RATE_LIMIT", True)
    api._hits.clear()
    c = TestClient(app)
    codes = [c.post("/api/auth/login", json={"email": "nobody@x.test", "password": "x"}).status_code for _ in range(25)]
    assert 429 in codes and codes[0] == 401
    api._hits.clear()
