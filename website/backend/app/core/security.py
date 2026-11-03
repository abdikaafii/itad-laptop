"""Passwords, tokens, CSRF, sessions, role checks and security headers."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import abort, current_app, g, jsonify, redirect, request, session, url_for

from .db import many, one, run, utcnow

ROLES = ("super_admin", "company_admin", "technician", "customer")
STAFF_ROLES = ("super_admin", "company_admin", "technician")
ROLE_LABELS = {"super_admin": "Super Administrator", "company_admin": "Company Administrator",
               "technician": "Technician", "customer": "Customer"}

# ---------------------------------------------------------------- passwords
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (_N, _R, _P, salt.hex(), dk.hex())


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, want = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                            n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk.hex(), want)
    except Exception:
        return False


def password_problems(password):
    """Return a list of reasons the password is too weak (empty list = fine)."""
    out = []
    if len(password) < 10:
        out.append("at least 10 characters")
    if password.lower() == password or password.upper() == password:
        out.append("a mix of upper and lower case letters")
    if not any(c.isdigit() for c in password):
        out.append("at least one number")
    if len(password) > 200:
        out.append("no more than 200 characters")
    return out


# ------------------------------------------------------------------- tokens
def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_auth_token(user_id, purpose, hours):
    raw = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    run("INSERT INTO auth_tokens (user_id, purpose, token_hash, expires_at, created_at) "
        "VALUES (?,?,?,?,?)", (user_id, purpose, sha256_hex(raw), exp, utcnow()))
    return raw


def consume_auth_token(raw, purpose):
    """Return the user id if the token is valid and unused, and mark it used."""
    row = one("SELECT * FROM auth_tokens WHERE token_hash = ? AND purpose = ?",
              (sha256_hex(raw or ""), purpose))
    if not row or row["used_at"] or row["expires_at"] < utcnow():
        return None
    run("UPDATE auth_tokens SET used_at = ? WHERE id = ?", (utcnow(), row["id"]))
    return row["user_id"]


# --------------------------------------------------------------------- CSRF
def csrf_token():
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf"] = tok
    return tok


def check_csrf():
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
    want = session.get("_csrf") or ""
    if not want or not hmac.compare_digest(sent, want):
        abort(400, "Security check failed. Go back, reload the page and try again.")


# ----------------------------------------------------------------- sessions
def login_user(user):
    session.clear()
    session["uid"] = user["id"]
    session["epoch"] = user["session_epoch"]
    session.permanent = False
    csrf_token()


def logout_user():
    session.clear()


def load_user():
    """Load the signed-in user (or None) once per request."""
    if "user" in g.__dict__:
        return g.user
    g.user = None
    uid = session.get("uid")
    if uid:
        row = one("SELECT * FROM users WHERE id = ?", (uid,))
        if (row and row["is_active"] and row["email_verified"]
                and row["session_epoch"] == session.get("epoch")):
            comp = None
            if row["company_id"]:
                comp = one("SELECT * FROM companies WHERE id = ?", (row["company_id"],))
            if row["role"] == "super_admin" or (comp and comp["is_active"]):
                g.user = row
                g.company = comp
        if g.user is None:
            session.clear()
    return g.user


def current_user():
    return load_user()


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not load_user():
            if request.path.startswith("/api/"):
                return jsonify(error="authentication required"), 401
            return redirect(url_for("authentication.login", next=request.full_path.rstrip("?")))
        return fn(*a, **kw)
    return wrapper


def roles_required(*roles):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            if g.user["role"] not in roles:
                if request.path.startswith("/api/"):
                    return jsonify(error="forbidden"), 403
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def is_staff(user=None):
    user = user or g.get("user")
    return bool(user and user["role"] in STAFF_ROLES)


def can_manage_company(user=None):
    user = user or g.get("user")
    return bool(user and user["role"] in ("super_admin", "company_admin"))


def safe_next(target):
    """Only allow redirects back into this site."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return ""


# ------------------------------------------------------------ rate limiting
_HITS = {}


def rate_limit(name, limit, seconds):
    """Allow `limit` calls per `seconds` per client address. In-memory, per process: fine for one
    server; put a shared store (Redis) behind it when running several workers."""
    import time

    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if current_app.config.get("TESTING_NO_LIMIT"):
                return fn(*a, **kw)
            now = time.time()
            key = (name, request.remote_addr)
            hits = [t for t in _HITS.get(key, []) if now - t < seconds]
            if len(hits) >= limit:
                abort(429, "Too many requests. Please wait a moment and try again.")
            hits.append(now)
            _HITS[key] = hits
            return fn(*a, **kw)
        return wrapper
    return deco


# ------------------------------------------------------------------ headers
def add_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    if current_app.config.get("SECURE_COOKIES"):
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.endpoint and "static" not in request.endpoint:
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp
