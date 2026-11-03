"""Shared test setup. Every test run uses a throw-away database and demo data."""
import os
import re
import sys

import pytest

import tempfile

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)

# Must happen before the app package is imported anywhere: config reads these at import time.
_TMP = tempfile.mkdtemp(prefix="itad_test_")
os.environ["ITAD_DATABASE"] = os.path.join(_TMP, "test.sqlite3")
os.environ["ITAD_UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ.pop("ITAD_SECRET_KEY", None)
os.environ["APP_ENV"] = "development"


@pytest.fixture(scope="session")
def env(tmp_path_factory):
    import pathlib
    d = pathlib.Path(_TMP)
    from app import create_app
    from app.core.db import get_db
    from app.seed import seed_demo
    app = create_app({"TESTING": True, "TESTING_NO_LIMIT": True})
    with app.app_context():
        creds = seed_demo(str(d))
        get_db().commit()
    return app, creds, d


@pytest.fixture()
def app(env):
    return env[0]


@pytest.fixture()
def creds(env):
    return env[1]


@pytest.fixture()
def instance_dir(env):
    return env[2]


def token_from(html):
    m = re.search(r'name="_csrf" value="([^"]+)"', html)
    return m.group(1) if m else ""


@pytest.fixture()
def login(app, creds):
    def _login(role):
        c = app.test_client()
        email, pw = creds[role]
        tok = token_from(c.get("/login").get_data(True))
        r = c.post("/login", data={"email": email, "password": pw, "_csrf": tok})
        assert r.status_code == 302, "login failed for %s" % role
        return c
    return _login


def post(client, url, data=None, page="/dashboard"):
    """POST with a valid CSRF token taken from a page the client can see."""
    tok = token_from(client.get(page).get_data(True))
    d = dict(data or {})
    d["_csrf"] = tok
    return client.post(url, data=d)
