"""Test fixtures. A fresh temporary database per test session; each test builds its own
company through the public API so tenants never share data."""
import itertools
import os
import sys
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="itad_test_")
os.environ.update({
    "ITAD_DATA_DIR": _tmp,
    "ITAD_DATABASE_URL": "sqlite:///%s/test.db" % _tmp,
    "ITAD_ENV": "development",
    "ITAD_SEED_DEMO": "0",
    "ITAD_EXPOSE_DEV_TOKENS": "1",
    "ITAD_SECRET_KEY": "test-secret",
    "ITAD_RATE_LIMIT": "0",
})
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from starlette.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PW = "Str0ng-Passw0rd!"
_n = itertools.count(1)


class Session:
    """A logged-in API client (own cookie jar + CSRF token)."""

    def __init__(self, email, password=PW):
        self.c = TestClient(app)
        r = self.c.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf"]
        self.user = r.json()["user"]

    def _h(self):
        return {"X-CSRF-Token": self.csrf}

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, json=None, **kw):
        return self.c.post(url, json=json or {}, headers=self._h(), **kw)

    def put(self, url, json=None):
        return self.c.put(url, json=json or {}, headers=self._h())

    def delete(self, url):
        return self.c.delete(url, headers=self._h())


@pytest.fixture(scope="session")
def anon():
    return TestClient(app)


def make_company(label=None):
    """Register a company through the public API, verify the e-mail, log in as its admin,
    then create a technician and a customer login. Returns a namespace of sessions."""
    k = next(_n)
    label = label or "Co%d" % k
    anon = TestClient(app)
    email = "admin%d@%s.test" % (k, label.lower())
    r = anon.post("/api/auth/register", json={"name": "Admin %d" % k, "email": email, "password": PW, "company_name": "Company %s" % label})
    assert r.status_code == 200, r.text
    tok = r.json()["dev_token"]
    assert anon.post("/api/auth/verify-email", json={"token": tok}).status_code == 200
    admin = Session(email)

    def add_user(role, name, customer_id=None):
        em = "%s%d@%s.test" % (role, k, label.lower())
        body = {"name": name, "email": em, "role": role}
        if customer_id:
            body["customer_id"] = customer_id
        rr = admin.post("/api/users", body)
        assert rr.status_code == 200, rr.text
        a = TestClient(app)
        assert a.post("/api/auth/reset", json={"token": rr.json()["dev_token"], "password": PW}).status_code == 200
        return Session(em)

    tech = add_user("technician", "Tech %d" % k)
    cust = admin.post("/api/customers", {"name": "Customer %s" % label}).json()
    customer = add_user("customer", "Cust User %d" % k, cust["id"])
    ns = type("Co", (), {})()
    ns.admin, ns.tech, ns.customer, ns.customer_id, ns.k, ns.company_id = admin, tech, customer, cust["id"], k, admin.user["company_id"]
    return ns


@pytest.fixture()
def co():
    return make_company()


@pytest.fixture()
def co2():
    return make_company()
