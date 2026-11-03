"""Passwords, sessions, CSRF, roles and the audit log."""
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from . import config
from .db import get_db, utcnow

ROLES = ("super_admin", "company_admin", "technician", "customer")


class HTTPError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail


# ---------------------------------------------------------------- passwords
_SCRYPT = dict(n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def hash_password(password):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return "scrypt$%s$%s" % (salt.hex(), dk.hex())


def verify_password(password, stored):
    try:
        scheme, salt, dk = stored.split("$")
        if scheme != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), **_SCRYPT)
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False


def check_password_policy(password):
    if len(password) < config.MIN_PASSWORD_LEN:
        raise HTTPError(422, "Password must be at least %d characters." % config.MIN_PASSWORD_LEN)
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 3:
        raise HTTPError(422, "Password needs at least 3 of: lower case, upper case, digit, symbol.")


# ----------------------------------------------------------------- sessions
def _h(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user, ip="", ua=""):
    db = get_db()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    db.insert("sessions", {
        "token_hash": _h(token), "user_id": user["id"], "csrf": csrf,
        "ip": ip, "user_agent": (ua or "")[:200],
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(hours=config.SESSION_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    return token, csrf


def load_session(token):
    if not token:
        return None, None
    db = get_db()
    s = db.one("SELECT * FROM sessions WHERE token_hash = ?", (_h(token),))
    if not s or s["expires_at"] < utcnow():
        return None, None
    u = db.one("SELECT * FROM users WHERE id = ?", (s["user_id"],))
    if not u or not u["active"]:
        return None, None
    return u, s


def destroy_session(token):
    if token:
        get_db().execute("DELETE FROM sessions WHERE token_hash = ?", (_h(token),))


def destroy_user_sessions(user_id):
    get_db().execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


# ------------------------------------------------------------- one-time tokens
def make_token(user_id, purpose, hours):
    db = get_db()
    raw = secrets.token_urlsafe(32)
    db.insert("tokens", {
        "user_id": user_id, "purpose": purpose, "token_hash": _h(raw),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    return raw


def use_token(raw, purpose):
    db = get_db()
    t = db.one("SELECT * FROM tokens WHERE token_hash = ? AND purpose = ?", (_h(raw or ""), purpose))
    if not t or t["used_at"] or t["expires_at"] < utcnow():
        raise HTTPError(400, "This link is invalid or has expired.")
    db.execute("UPDATE tokens SET used_at = ? WHERE id = ?", (utcnow(), t["id"]))
    return t["user_id"]


# ---------------------------------------------------------------- e-mail out
def send_email(to, subject, body):
    db = get_db()
    oid = db.insert("outbox", {"to_email": to, "subject": subject, "body": body,
                               "created_at": utcnow(), "sent": 0})
    if config.SMTP_HOST:
        try:
            import smtplib
            from email.message import EmailMessage
            m = EmailMessage()
            m["From"], m["To"], m["Subject"] = config.SMTP_FROM, to, subject
            m.set_content(body)
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
                s.starttls()
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(m)
            db.execute("UPDATE outbox SET sent = 1 WHERE id = ?", (oid,))
        except Exception:
            pass                                  # stays in the outbox, sent = 0
    return oid


# --------------------------------------------------------------------- audit
def audit(user, action, entity="", entity_id="", detail=None, ip="", company_id=None):
    """Append to the hash-chained audit log. Each row includes the hash of the
    row before it, so removing or editing a row breaks the chain."""
    db = get_db()
    cid = company_id if company_id is not None else (user or {}).get("company_id")
    with db.transaction():
        last = db.one("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1")
        prev = last["hash"] if last else "0" * 64
        now = utcnow()
        d = json.dumps(detail or {}, sort_keys=True, default=str)
        uid = (user or {}).get("id")
        h = hashlib.sha256("|".join([prev, now, str(uid), action, entity, str(entity_id), d]).encode()).hexdigest()
        db.insert("audit_log", {"company_id": cid, "user_id": uid, "action": action,
                                "entity": entity, "entity_id": str(entity_id), "detail": d,
                                "ip": ip, "created_at": now, "prev_hash": prev, "hash": h})


def verify_audit_chain():
    db = get_db()
    prev = "0" * 64
    for r in db.query("SELECT * FROM audit_log ORDER BY id"):
        h = hashlib.sha256("|".join([prev, r["created_at"], str(r["user_id"]), r["action"],
                                     r["entity"] or "", str(r["entity_id"] or ""),
                                     r["detail"] or ""]).encode()).hexdigest()
        if r["prev_hash"] != prev or r["hash"] != h:
            return False, r["id"]
        prev = r["hash"]
    return True, None


# --------------------------------------------------------------------- access
def is_super(u):
    return u["role"] == "super_admin"


def tenant_filter(u, alias=""):
    """(sql fragment, params) restricting a query to what this user may see.
    Customers only see their own customer record, everyone else only their company."""
    p = alias + "." if alias else ""
    if is_super(u):
        return "1 = 1", []
    if u["role"] == "customer":
        return "%scompany_id = ? AND %scustomer_id = ?" % (p, p), [u["company_id"], u["customer_id"]]
    return "%scompany_id = ?" % p, [u["company_id"]]


def require_roles(u, *roles):
    if u["role"] not in roles:
        raise HTTPError(403, "Your role is not allowed to do this.")


STAFF = ("super_admin", "company_admin", "technician")
ADMINS = ("super_admin", "company_admin")
