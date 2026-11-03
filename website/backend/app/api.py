"""REST API. Small router on top of Starlette; every handler is a plain function
that gets a Ctx and returns a dict/list (JSON) or a Response."""
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import defaultdict
from types import SimpleNamespace

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import config, pdfgen, reports as reportdefs
from .db import get_db, utcnow
from .integrity import fingerprint, key_id_of, signer
from .security import (ADMINS, STAFF, HTTPError, audit, check_password_policy, create_session, destroy_session,
                       destroy_user_sessions, hash_password, load_session, make_token, send_email, tenant_filter,
                       use_token, verify_audit_chain, verify_password)
from .services import (COMPONENTS, TEST_STATUSES, certificate_blockers, certificate_status, compute_assessment,
                       dashboard_stats, dublin, ingest_report, issue_certificate, jloads, refresh_asset, revoke_certificate)

COOKIE = "itad_session"
SAFE = ("GET", "HEAD", "OPTIONS")
ROUTES = []
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


def route(method, path, roles=None, auth="session", maxbody=2_000_000):
    def deco(fn):
        ROUTES.append((method, path, fn, roles, auth, maxbody))
        return fn
    return deco


# --------------------------------------------------------------- rate limiting
_hits = defaultdict(list)


def rate_limit(key, limit, window=60):
    if not config.RATE_LIMIT:
        return
    now = time.time()
    _hits[key] = [t for t in _hits[key] if now - t < window]
    if len(_hits[key]) >= limit:
        raise HTTPError(429, "Too many requests. Try again in a minute.")
    _hits[key].append(now)


# ------------------------------------------------------------------- helpers
def need(body, name, maxlen=200, required=True):
    v = body.get(name)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise HTTPError(422, "%s is required." % name.replace("_", " "))
        return None
    if not isinstance(v, (str, int, float)):
        raise HTTPError(422, "%s is not valid." % name)
    v = str(v).strip()
    if len(v) > maxlen:
        raise HTTPError(422, "%s is too long." % name.replace("_", " "))
    return v


def get_company(ctx, company_id=None):
    """The company the caller works in (super administrators must name one)."""
    u = ctx.user
    cid = u["company_id"]
    if u["role"] == "super_admin":
        cid = company_id or ctx.body.get("company_id") or ctx.q.get("company_id")
        if not cid:
            raise HTTPError(422, "company_id is required.")
    c = ctx.db.one("SELECT * FROM companies WHERE id = ?", (int(cid),))
    if not c:
        raise HTTPError(404, "Company not found.")
    return c


def scoped(ctx, table, row_id, alias="t"):
    f, p = tenant_filter(ctx.user, alias)
    r = ctx.db.one("SELECT %s.* FROM %s %s WHERE %s.id = ? AND %s" % (alias, table, alias, alias, f), [row_id] + p)
    if not r:
        raise HTTPError(404, "Not found.")
    return r


def user_public(u):
    c = get_db().one("SELECT name FROM companies WHERE id = ?", (u["company_id"],)) if u["company_id"] else None
    return {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"], "company_id": u["company_id"],
            "company_name": c["name"] if c else None, "customer_id": u["customer_id"]}


def page(ctx, default=50, maximum=200):
    try:
        lim = max(1, min(int(ctx.q.get("limit", default)), maximum))
        off = max(0, int(ctx.q.get("offset", 0)))
    except ValueError:
        raise HTTPError(422, "limit/offset must be numbers.")
    return lim, off


def like(v):
    return "%" + v.replace("%", "").replace("_", "") + "%"


# ================================================================== public
@route("GET", "/api/health", auth="public")
def health(ctx):
    return {"status": "ok", "product": config.PRODUCT_NAME, "env": config.ENV}


@route("POST", "/api/contact", auth="public")
def contact(ctx):
    rate_limit("contact:" + ctx.ip, 5, 600)
    name, email = need(ctx.body, "name", 100), need(ctx.body, "email", 200)
    if not EMAIL_RE.match(email):
        raise HTTPError(422, "Enter a valid e-mail address.")
    ctx.db.insert("demo_requests", {"name": name, "email": email, "company": need(ctx.body, "company", 150, False),
                                    "message": need(ctx.body, "message", 2000, False), "created_at": utcnow()})
    return {"ok": True, "message": "Thank you. We will contact you shortly."}


@route("GET", "/api/public/verify/{number}", auth="public")
def public_verify(ctx):
    rate_limit("verify:" + ctx.ip, 40)
    c = ctx.db.one("SELECT * FROM certificates WHERE number = ?", (ctx.p["number"].strip().upper(),))
    if not c:
        return JSONResponse({"found": False, "state": "NOT FOUND"}, status_code=404)
    st = certificate_status(c)
    p = json.loads(c["payload"])
    serial = p["asset"]["serial"] or ""
    return {"found": True, "state": st["state"], "number": c["number"], "issued": dublin(c["issued_at"]),
            "company": p["company"]["name"], "device": ("%s %s" % (p["asset"]["manufacturer"] or "", p["asset"]["model"] or "")).strip(),
            "serial_masked": ("*" * max(0, len(serial) - 4)) + serial[-4:], "method": p["erasure"]["method"],
            "standard": p["erasure"]["standard"], "verified": p["erasure"]["verified"],
            "finished": p["erasure"]["finished"], "demo": bool(c["demo"]), "revoked_reason": st["revoked_reason"],
            "intact": st["intact"], "content_sha256": c["payload_sha256"]}


# ================================================================== auth
def _login_response(user, ctx):
    token, csrf = create_session(user, ctx.ip, ctx.req.headers.get("user-agent", ""))
    r = JSONResponse({"user": user_public(user), "csrf": csrf})
    r.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
                 max_age=config.SESSION_HOURS * 3600, path="/")
    return r


@route("POST", "/api/auth/register", auth="public")
def register(ctx):
    if not config.ALLOW_REGISTRATION:
        raise HTTPError(403, "Registration is closed. Ask your administrator for an account.")
    rate_limit("register:" + ctx.ip, 5, 600)
    name, email = need(ctx.body, "name", 100), need(ctx.body, "email", 200).lower()
    company = need(ctx.body, "company_name", 150)
    pw = need(ctx.body, "password", 200)
    if not EMAIL_RE.match(email):
        raise HTTPError(422, "Enter a valid e-mail address.")
    check_password_policy(pw)
    db = ctx.db
    generic = {"ok": True, "message": "Account created. Check your e-mail for the verification link."}
    if db.one("SELECT 1 AS x FROM users WHERE email = ?", (email,)):
        return generic                              # do not reveal that the address exists
    slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-") or "company"
    base, n = slug, 1
    while db.one("SELECT 1 AS x FROM companies WHERE slug = ?", (slug,)):
        n += 1
        slug = "%s-%d" % (base, n)
    cid = db.insert("companies", {"name": company, "slug": slug, "cert_prefix": (re.sub(r"[^A-Z0-9]", "", company.upper())[:4] or "ITAD"),
                                  "created_at": utcnow()})
    uid = db.insert("users", {"company_id": cid, "email": email, "name": name, "role": "company_admin",
                              "password_hash": hash_password(pw), "email_verified": 0 if config.REQUIRE_EMAIL_VERIFICATION else 1,
                              "created_at": utcnow()})
    raw = make_token(uid, "verify", 48)
    send_email(email, "Verify your e-mail", "Welcome to %s.\n\nVerify your e-mail: %s/app#/verify?token=%s\n\nThe link works once and expires in 48 hours." %
               (config.PRODUCT_NAME, config.PUBLIC_BASE_URL, raw))
    audit({"id": uid, "company_id": cid}, "user.registered", "user", uid, {"company": company}, ctx.ip, cid)
    if config.EXPOSE_DEV_TOKENS:
        generic["dev_token"] = raw
    return generic


@route("POST", "/api/auth/verify-email", auth="public")
def verify_email(ctx):
    uid = use_token(ctx.body.get("token"), "verify")
    ctx.db.update("users", uid, {"email_verified": 1})
    audit({"id": uid}, "user.email_verified", "user", uid, ip=ctx.ip)
    return {"ok": True}


@route("POST", "/api/auth/login", auth="public")
def login(ctx):
    rate_limit("login:" + ctx.ip, 20)
    email = need(ctx.body, "email", 200).lower()
    pw = need(ctx.body, "password", 200)
    db = ctx.db
    u = db.one("SELECT * FROM users WHERE email = ?", (email,))
    if not u:
        hash_password(pw)                           # same time as a real check
        raise HTTPError(401, "Wrong e-mail or password.")
    if u["locked_until"] and u["locked_until"] > utcnow():
        raise HTTPError(423, "Too many wrong passwords. This account is locked for %d minutes." % config.LOCKOUT_MINUTES)
    if not verify_password(pw, u["password_hash"]):
        n = u["failed_logins"] + 1
        vals = {"failed_logins": n}
        if n >= config.MAX_FAILED_LOGINS:
            from datetime import datetime, timedelta, timezone
            vals.update(failed_logins=0, locked_until=(datetime.now(timezone.utc) + timedelta(minutes=config.LOCKOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ"))
            audit(u, "user.locked", "user", u["id"], ip=ctx.ip)
        db.update("users", u["id"], vals)
        raise HTTPError(401, "Wrong e-mail or password.")
    if not u["active"]:
        raise HTTPError(403, "This account is disabled.")
    if not u["email_verified"]:
        raise HTTPError(403, "Verify your e-mail first. Check your inbox for the link.")
    if u["company_id"] and not db.one("SELECT active FROM companies WHERE id = ?", (u["company_id"],))["active"]:
        raise HTTPError(403, "This company is disabled.")
    db.update("users", u["id"], {"failed_logins": 0, "locked_until": None, "last_login": utcnow()})
    audit(u, "user.login", "user", u["id"], ip=ctx.ip)
    return _login_response(u, ctx)


@route("POST", "/api/auth/logout", auth="optional")
def logout(ctx):
    destroy_session(ctx.req.cookies.get(COOKIE))
    if ctx.user:
        audit(ctx.user, "user.logout", "user", ctx.user["id"], ip=ctx.ip)
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE, path="/")
    return r


@route("GET", "/api/auth/me", auth="optional")
def me(ctx):
    if not ctx.user:
        return JSONResponse({"user": None}, status_code=200)
    return {"user": user_public(ctx.user), "csrf": ctx.session["csrf"]}


@route("POST", "/api/auth/forgot", auth="public")
def forgot(ctx):
    rate_limit("forgot:" + ctx.ip, 5, 600)
    email = need(ctx.body, "email", 200).lower()
    u = ctx.db.one("SELECT * FROM users WHERE email = ? AND active = 1", (email,))
    out = {"ok": True, "message": "If that address has an account, a reset link was sent."}
    if u:
        raw = make_token(u["id"], "reset", 2)
        send_email(email, "Reset your password", "Reset your password: %s/app#/reset?token=%s\n\nThe link works once and expires in 2 hours. "
                   "If you did not ask for this, ignore this e-mail." % (config.PUBLIC_BASE_URL, raw))
        audit(u, "user.reset_requested", "user", u["id"], ip=ctx.ip)
        if config.EXPOSE_DEV_TOKENS:
            out["dev_token"] = raw
    return out


@route("POST", "/api/auth/reset", auth="public")
def reset(ctx):
    pw = need(ctx.body, "password", 200)
    check_password_policy(pw)
    uid = use_token(ctx.body.get("token"), "reset")
    ctx.db.update("users", uid, {"password_hash": hash_password(pw), "email_verified": 1, "failed_logins": 0, "locked_until": None})
    destroy_user_sessions(uid)
    audit({"id": uid}, "user.password_reset", "user", uid, ip=ctx.ip)
    return {"ok": True}


@route("PUT", "/api/auth/profile")
def profile(ctx):
    vals = {}
    if ctx.body.get("name"):
        vals["name"] = need(ctx.body, "name", 100)
    if ctx.body.get("new_password"):
        if not verify_password(ctx.body.get("current_password") or "", ctx.user["password_hash"]):
            raise HTTPError(403, "Current password is wrong.")
        check_password_policy(ctx.body["new_password"])
        vals["password_hash"] = hash_password(ctx.body["new_password"])
    if vals:
        ctx.db.update("users", ctx.user["id"], vals)
        audit(ctx.user, "user.profile_updated", "user", ctx.user["id"], {"fields": list(vals)}, ctx.ip)
    return {"user": user_public(ctx.db.one("SELECT * FROM users WHERE id = ?", (ctx.user["id"],)))}


# ================================================================== companies
def _company_row(c):
    return {k: c[k] for k in ("id", "name", "slug", "logo_data", "address", "email", "phone", "website", "cert_prefix",
                              "accent_color", "require_signed", "active", "created_at")}


@route("GET", "/api/companies", roles=ADMINS)
def companies(ctx):
    if ctx.user["role"] == "super_admin":
        rows = ctx.db.query("SELECT * FROM companies ORDER BY name")
    else:
        rows = ctx.db.query("SELECT * FROM companies WHERE id = ?", (ctx.user["company_id"],))
    return [_company_row(c) for c in rows]


@route("POST", "/api/companies", roles=("super_admin",))
def company_create(ctx):
    name = need(ctx.body, "name", 150)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "company"
    base, n = slug, 1
    while ctx.db.one("SELECT 1 AS x FROM companies WHERE slug = ?", (slug,)):
        n += 1
        slug = "%s-%d" % (base, n)
    cid = ctx.db.insert("companies", {"name": name, "slug": slug, "created_at": utcnow(),
                                      "cert_prefix": re.sub(r"[^A-Z0-9]", "", name.upper())[:4] or "ITAD"})
    audit(ctx.user, "company.created", "company", cid, {"name": name}, ctx.ip, cid)
    return _company_row(ctx.db.one("SELECT * FROM companies WHERE id = ?", (cid,)))


@route("PUT", "/api/companies/{id:int}", roles=ADMINS)
def company_update(ctx):
    cid = ctx.p["id"]
    if ctx.user["role"] != "super_admin" and ctx.user["company_id"] != cid:
        raise HTTPError(404, "Not found.")
    c = ctx.db.one("SELECT * FROM companies WHERE id = ?", (cid,))
    if not c:
        raise HTTPError(404, "Not found.")
    b, vals = ctx.body, {}
    for f, mx in (("name", 150), ("address", 300), ("email", 200), ("phone", 50), ("website", 200)):
        if f in b:
            vals[f] = need(b, f, mx, f != "name")
    if "cert_prefix" in b:
        pre = re.sub(r"[^A-Z0-9]", "", str(b["cert_prefix"]).upper())
        if not 2 <= len(pre) <= 8:
            raise HTTPError(422, "Certificate prefix must be 2 to 8 letters or digits.")
        vals["cert_prefix"] = pre
    if "accent_color" in b:
        if not re.match(r"^#[0-9a-fA-F]{6}$", str(b["accent_color"])):
            raise HTTPError(422, "Accent colour must look like #2E9BFF.")
        vals["accent_color"] = b["accent_color"]
    if "logo_data" in b:
        d = b["logo_data"] or ""
        if d and (len(d) > 200_000 or not re.match(r"^data:image/(png|jpeg);base64,[A-Za-z0-9+/=]+$", d)):
            raise HTTPError(422, "Logo must be a PNG or JPEG under 150 KB.")
        vals["logo_data"] = d or None
    if "require_signed" in b:
        vals["require_signed"] = int(bool(b["require_signed"]))
    if "active" in b and ctx.user["role"] == "super_admin":
        vals["active"] = int(bool(b["active"]))
    if vals:
        ctx.db.update("companies", cid, vals)
        audit(ctx.user, "company.updated", "company", cid, {k: ("(logo)" if k == "logo_data" else v) for k, v in vals.items()}, ctx.ip, cid)
    return _company_row(ctx.db.one("SELECT * FROM companies WHERE id = ?", (cid,)))


# ================================================================== users
@route("GET", "/api/users", roles=ADMINS)
def users_list(ctx):
    if ctx.user["role"] == "super_admin":
        rows = ctx.db.query("SELECT u.*, c.name AS company_name FROM users u LEFT JOIN companies c ON c.id = u.company_id ORDER BY u.name")
    else:
        rows = ctx.db.query("SELECT u.*, c.name AS company_name FROM users u LEFT JOIN companies c ON c.id = u.company_id "
                            "WHERE u.company_id = ? ORDER BY u.name", (ctx.user["company_id"],))
    return [{"id": r["id"], "name": r["name"], "email": r["email"], "role": r["role"], "active": r["active"],
             "company_id": r["company_id"], "company_name": r["company_name"], "customer_id": r["customer_id"],
             "email_verified": r["email_verified"], "last_login": dublin(r["last_login"])} for r in rows]


@route("GET", "/api/technicians", roles=STAFF)
def technicians(ctx):
    cid = ctx.user["company_id"] if ctx.user["role"] != "super_admin" else (ctx.q.get("company_id") or None)
    rows = ctx.db.query("SELECT id, name FROM users WHERE role IN ('technician','company_admin') AND active = 1 AND company_id = ? ORDER BY name", (cid,)) if cid else []
    return rows


@route("POST", "/api/users", roles=ADMINS)
def user_create(ctx):
    b = ctx.body
    role = need(b, "role", 30)
    allowed = ("super_admin", "company_admin", "technician", "customer") if ctx.user["role"] == "super_admin" else ("company_admin", "technician", "customer")
    if role not in allowed:
        raise HTTPError(403, "You cannot create that kind of user.")
    name, email = need(b, "name", 100), need(b, "email", 200).lower()
    if not EMAIL_RE.match(email):
        raise HTTPError(422, "Enter a valid e-mail address.")
    if ctx.db.one("SELECT 1 AS x FROM users WHERE email = ?", (email,)):
        raise HTTPError(409, "A user with this e-mail already exists.")
    company_id = customer_id = None
    if role != "super_admin":
        company_id = get_company(ctx, b.get("company_id")).get("id")
    if role == "customer":
        cu = ctx.db.one("SELECT * FROM customers WHERE id = ? AND company_id = ?", (b.get("customer_id") or 0, company_id))
        if not cu:
            raise HTTPError(422, "Choose the customer this login belongs to.")
        customer_id = cu["id"]
    uid = ctx.db.insert("users", {"company_id": company_id, "customer_id": customer_id, "email": email, "name": name, "role": role,
                                  "password_hash": hash_password(secrets.token_urlsafe(24)), "email_verified": 0, "created_at": utcnow()})
    raw = make_token(uid, "reset", 72)
    send_email(email, "Your %s account" % config.PRODUCT_NAME, "An account was created for you.\n\nSet your password: %s/app#/reset?token=%s\n\nThe link works once and expires in 72 hours." %
               (config.PUBLIC_BASE_URL, raw))
    audit(ctx.user, "user.created", "user", uid, {"email": email, "role": role}, ctx.ip, company_id)
    out = {"id": uid, "email": email, "role": role}
    if config.EXPOSE_DEV_TOKENS:
        out["dev_token"] = raw
    return out


@route("PUT", "/api/users/{id:int}", roles=ADMINS)
def user_update(ctx):
    u = ctx.db.one("SELECT * FROM users WHERE id = ?", (ctx.p["id"],))
    if not u or (ctx.user["role"] != "super_admin" and u["company_id"] != ctx.user["company_id"]):
        raise HTTPError(404, "Not found.")
    if u["role"] == "super_admin" and ctx.user["role"] != "super_admin":
        raise HTTPError(403, "Only a super administrator can change a super administrator.")
    vals = {}
    if "name" in ctx.body:
        vals["name"] = need(ctx.body, "name", 100)
    if "active" in ctx.body:
        if u["id"] == ctx.user["id"]:
            raise HTTPError(409, "You cannot disable your own account.")
        vals["active"] = int(bool(ctx.body["active"]))
    if "role" in ctx.body:
        if ctx.body["role"] not in ("company_admin", "technician") or u["role"] in ("customer", "super_admin"):
            raise HTTPError(422, "Role can only switch between company_admin and technician.")
        if u["id"] == ctx.user["id"]:
            raise HTTPError(409, "You cannot change your own role.")
        vals["role"] = ctx.body["role"]
    if vals:
        ctx.db.update("users", u["id"], vals)
        if vals.get("active") == 0 or "role" in vals:
            destroy_user_sessions(u["id"])
        audit(ctx.user, "user.updated", "user", u["id"], vals, ctx.ip, u["company_id"])
    return {"ok": True}


# ================================================================== customers
@route("GET", "/api/customers", roles=STAFF)
def customers_list(ctx):
    f, p = tenant_filter(ctx.user, "c")
    f = f.replace("c.customer_id", "c.id")
    q = ctx.q.get("q")
    sql = "SELECT c.*, (SELECT COUNT(*) FROM assets a WHERE a.customer_id = c.id) AS asset_count FROM customers c WHERE %s" % f
    if q:
        sql += " AND c.name LIKE ?"
        p.append(like(q))
    return ctx.db.query(sql + " ORDER BY c.name", p)


@route("POST", "/api/customers", roles=ADMINS + ("technician",))
def customer_create(ctx):
    co = get_company(ctx)
    b = ctx.body
    cid = ctx.db.insert("customers", {"company_id": co["id"], "name": need(b, "name", 150), "contact_name": need(b, "contact_name", 100, False),
                                      "email": need(b, "email", 200, False), "phone": need(b, "phone", 50, False),
                                      "address": need(b, "address", 300, False), "created_at": utcnow()})
    audit(ctx.user, "customer.created", "customer", cid, {"name": b["name"]}, ctx.ip, co["id"])
    return ctx.db.one("SELECT * FROM customers WHERE id = ?", (cid,))


@route("PUT", "/api/customers/{id:int}", roles=ADMINS)
def customer_update(ctx):
    c = ctx.db.one("SELECT * FROM customers WHERE id = ?", (ctx.p["id"],))
    if not c or (ctx.user["role"] != "super_admin" and c["company_id"] != ctx.user["company_id"]):
        raise HTTPError(404, "Not found.")
    vals = {f: need(ctx.body, f, 300, f != "name") for f in ("name", "contact_name", "email", "phone", "address") if f in ctx.body}
    if vals:
        ctx.db.update("customers", c["id"], vals)
        audit(ctx.user, "customer.updated", "customer", c["id"], vals, ctx.ip, c["company_id"])
    return ctx.db.one("SELECT * FROM customers WHERE id = ?", (c["id"],))


# ================================================================== jobs
def _job_rows(ctx, extra="", extra_p=()):
    f, p = tenant_filter(ctx.user, "j")
    sql = ("SELECT j.*, c.name AS customer_name, u.name AS technician_name, "
           "(SELECT COUNT(*) FROM assets a WHERE a.job_id = j.id) AS asset_count, "
           "(SELECT COUNT(*) FROM assets a WHERE a.job_id = j.id AND a.status = 'completed') AS passed_count "
           "FROM jobs j LEFT JOIN customers c ON c.id = j.customer_id LEFT JOIN users u ON u.id = j.technician_id WHERE %s" % f)
    p = list(p)
    if ctx.user["role"] == "technician" and not ctx.q.get("all"):
        sql += " AND (j.technician_id = ? OR j.technician_id IS NULL)"
        p.append(ctx.user["id"])
    if ctx.q.get("status"):
        sql += " AND j.status = ?"
        p.append(ctx.q["status"])
    if ctx.q.get("q"):
        sql += " AND (j.job_ref LIKE ? OR j.title LIKE ?)"
        p += [like(ctx.q["q"])] * 2
    sql += extra
    return ctx.db.query(sql + " ORDER BY j.id DESC LIMIT ? OFFSET ?", p + list(extra_p) + list(page(ctx)))


@route("GET", "/api/jobs")
def jobs_list(ctx):
    return _job_rows(ctx)


@route("POST", "/api/jobs", roles=ADMINS + ("technician",))
def job_create(ctx):
    co = get_company(ctx)
    b = ctx.body
    ref = need(b, "job_ref", 60, False)
    if not ref:
        n = ctx.db.one("SELECT COUNT(*) AS n FROM jobs WHERE company_id = ?", (co["id"],))["n"] + 1
        ref = "JOB-%s-%04d" % (time.strftime("%Y%m%d"), n)
    if ctx.db.one("SELECT 1 AS x FROM jobs WHERE company_id = ? AND job_ref = ?", (co["id"], ref)):
        raise HTTPError(409, "Job reference %s already exists." % ref)
    cu = None
    if b.get("customer_id"):
        cu = ctx.db.one("SELECT * FROM customers WHERE id = ? AND company_id = ?", (b["customer_id"], co["id"]))
        if not cu:
            raise HTTPError(422, "Customer not found in this company.")
    tech = b.get("technician_id")
    if ctx.user["role"] == "technician":
        tech = ctx.user["id"]
    if tech and not ctx.db.one("SELECT 1 AS x FROM users WHERE id = ? AND company_id = ? AND active = 1", (tech, co["id"])):
        raise HTTPError(422, "Technician not found in this company.")
    jid = ctx.db.insert("jobs", {"company_id": co["id"], "customer_id": cu["id"] if cu else None, "job_ref": ref,
                                 "title": need(b, "title", 200), "technician_id": tech, "notes": need(b, "notes", 2000, False),
                                 "due_date": need(b, "due_date", 10, False), "created_at": utcnow()})
    audit(ctx.user, "job.created", "job", jid, {"job_ref": ref}, ctx.ip, co["id"])
    return ctx.db.one("SELECT * FROM jobs WHERE id = ?", (jid,))


@route("GET", "/api/jobs/{id:int}")
def job_detail(ctx):
    j = scoped(ctx, "jobs", ctx.p["id"], "j")
    f, p = tenant_filter(ctx.user, "a")
    assets = ctx.db.query("SELECT a.id, a.asset_tag, a.serial, a.model, a.status, "
                          "(SELECT overall FROM assessments s WHERE s.asset_id = a.id ORDER BY s.id DESC LIMIT 1) AS overall "
                          "FROM assets a WHERE a.job_id = ? AND %s ORDER BY a.asset_tag" % f, [j["id"]] + p)
    return {"job": j, "assets": assets}


@route("PUT", "/api/jobs/{id:int}", roles=ADMINS + ("technician",))
def job_update(ctx):
    j = scoped(ctx, "jobs", ctx.p["id"], "j")
    vals = {}
    for f, mx in (("title", 200), ("notes", 2000), ("due_date", 10)):
        if f in ctx.body:
            vals[f] = need(ctx.body, f, mx, f == "title")
    if "status" in ctx.body:
        if ctx.body["status"] not in ("open", "in_progress", "completed", "cancelled"):
            raise HTTPError(422, "Invalid status.")
        if ctx.body["status"] == "completed":
            bad = ctx.db.one("SELECT COUNT(*) AS n FROM assets WHERE job_id = ? AND status NOT IN ('completed','failed','blocked')", (j["id"],))["n"]
            if bad:
                raise HTTPError(409, "%d asset(s) in this job are not finished yet." % bad)
            vals["completed_at"] = utcnow()
        vals["status"] = ctx.body["status"]
    if "technician_id" in ctx.body and ctx.user["role"] in ADMINS:
        t = ctx.body["technician_id"]
        if t and not ctx.db.one("SELECT 1 AS x FROM users WHERE id = ? AND company_id = ? AND active = 1", (t, j["company_id"])):
            raise HTTPError(422, "Technician not found in this company.")
        vals["technician_id"] = t
    if vals:
        ctx.db.update("jobs", j["id"], vals)
        audit(ctx.user, "job.updated", "job", j["id"], vals, ctx.ip, j["company_id"])
    return ctx.db.one("SELECT * FROM jobs WHERE id = ?", (j["id"],))


# ================================================================== assets
@route("GET", "/api/assets")
def assets_list(ctx):
    f, p = tenant_filter(ctx.user, "a")
    sql = ("SELECT a.id, a.asset_tag, a.serial, a.manufacturer, a.model, a.status, a.demo, a.updated_at, j.job_ref, c.name AS customer_name, "
           "u.name AS technician_name, (SELECT overall FROM assessments s WHERE s.asset_id = a.id ORDER BY s.id DESC LIMIT 1) AS overall "
           "FROM assets a LEFT JOIN jobs j ON j.id = a.job_id LEFT JOIN customers c ON c.id = a.customer_id "
           "LEFT JOIN users u ON u.id = a.technician_id WHERE %s" % f)
    p = list(p)
    for key, col in (("status", "a.status"), ("job_id", "a.job_id"), ("customer_id", "a.customer_id")):
        if ctx.q.get(key):
            sql += " AND %s = ?" % col
            p.append(ctx.q[key])
    if ctx.q.get("q"):
        sql += " AND (a.asset_tag LIKE ? OR a.serial LIKE ? OR a.model LIKE ? OR a.manufacturer LIKE ?)"
        p += [like(ctx.q["q"])] * 4
    total = ctx.db.one("SELECT COUNT(*) AS n FROM (%s) x" % sql, p)["n"]
    rows = ctx.db.query(sql + " ORDER BY a.id DESC LIMIT ? OFFSET ?", p + list(page(ctx)))
    for r in rows:
        r["updated_at"] = dublin(r["updated_at"])
    return {"total": total, "items": rows}


@route("POST", "/api/assets", roles=STAFF)
def asset_create(ctx):
    b = ctx.body
    job = None
    if b.get("job_id"):
        job = ctx.db.one("SELECT * FROM jobs WHERE id = ?", (b["job_id"],))
        if not job or (ctx.user["role"] != "super_admin" and job["company_id"] != ctx.user["company_id"]):
            raise HTTPError(422, "Job not found.")
        co_id = job["company_id"]
    else:
        co_id = get_company(ctx)["id"]
    tag, serial = need(b, "asset_tag", 60), need(b, "serial", 80)
    if ctx.db.one("SELECT 1 AS x FROM assets WHERE company_id = ? AND asset_tag = ?", (co_id, tag)):
        raise HTTPError(409, "Asset ID %s already exists." % tag)
    dup = ctx.db.one("SELECT asset_tag FROM assets WHERE company_id = ? AND LOWER(serial) = ?", (co_id, serial.lower()))
    aid = ctx.db.insert("assets", {
        "company_id": co_id, "customer_id": (job or {}).get("customer_id") or b.get("customer_id"), "job_id": (job or {}).get("id"),
        "asset_tag": tag, "serial": serial, "manufacturer": need(b, "manufacturer", 80, False), "model": need(b, "model", 120, False),
        "cpu": need(b, "cpu", 120, False), "ram_gb": float(b["ram_gb"]) if b.get("ram_gb") not in (None, "") else None,
        "storage_summary": need(b, "storage_summary", 300, False), "bios": need(b, "bios", 120, False), "os_info": need(b, "os_info", 120, False),
        "chassis": need(b, "chassis", 40, False), "technician_id": ctx.user["id"] if ctx.user["role"] == "technician" else b.get("technician_id"),
        "status": "registered", "demo": 0, "created_at": utcnow(), "updated_at": utcnow()})
    refresh_asset(aid, ctx.user)
    audit(ctx.user, "asset.created", "asset", aid, {"tag": tag, "serial": serial}, ctx.ip, co_id)
    out = ctx.db.one("SELECT * FROM assets WHERE id = ?", (aid,))
    if dup:
        out["warning"] = "Another asset (%s) already has this serial number." % dup["asset_tag"]
    return out


def _asset_bundle(ctx, a):
    db = ctx.db
    insp = db.one("SELECT * FROM inspections WHERE asset_id = ? ORDER BY id DESC LIMIT 1", (a["id"],))
    erasures = db.query("SELECT e.*, c.number AS certificate_number, c.id AS certificate_id FROM erasures e "
                        "LEFT JOIN certificates c ON c.erasure_id = e.id WHERE e.asset_id = ? ORDER BY e.id", (a["id"],))
    co = db.one("SELECT * FROM companies WHERE id = ?", (a["company_id"],))
    for e in erasures:
        e["verification"] = jloads(e["verification"], {})
        e["certificate_blockers"] = [] if e["certificate_id"] else certificate_blockers(e, a, co)
    stored = {t["component"]: t for t in db.query("SELECT * FROM test_results WHERE asset_id = ?", (a["id"],))}
    tests = []
    for comp in COMPONENTS:
        t = stored.pop(comp, None)
        tests.append({"component": comp, "status": t["status"] if t else "NOT TESTED", "notes": t["notes"] if t else "",
                      "tested_by": t["tested_by"] if t else None, "tested_at": dublin(t["tested_at"]) if t else None})
    for comp, t in stored.items():
        tests.append({"component": comp, "status": t["status"], "notes": t["notes"], "tested_by": t["tested_by"], "tested_at": dublin(t["tested_at"])})
    overall, reasons = compute_assessment(a["id"])
    certs = db.query("SELECT id, number, issued_at, demo FROM certificates WHERE asset_id = ?", (a["id"],))
    for c in certs:
        c["issued_at"] = dublin(c["issued_at"])
    tech = db.one("SELECT name FROM users WHERE id = ?", (a["technician_id"],)) if a["technician_id"] else None
    subs = []
    if ctx.user["role"] != "customer":
        subs = db.query("SELECT id, received_at, status, signed, signature_ok, key_id, key_trusted, fingerprint_ok, reason, demo FROM submissions "
                        "WHERE asset_id = ? ORDER BY id DESC", (a["id"],))
        for s in subs:
            s["received_at"] = dublin(s["received_at"])
    if insp:
        insp["issues"] = jloads(insp["issues"], [])
        insp["recorded_at"] = dublin(insp["recorded_at"])
    a = dict(a)
    for k in ("created_at", "updated_at"):
        a[k] = dublin(a[k])
    return {"asset": a, "job": db.one("SELECT id, job_ref, title, status FROM jobs WHERE id = ?", (a["job_id"],)) if a["job_id"] else None,
            "customer": db.one("SELECT id, name FROM customers WHERE id = ?", (a["customer_id"],)) if a["customer_id"] else None,
            "technician_name": tech["name"] if tech else None, "inspection": insp, "erasures": erasures, "tests": tests,
            "assessment": {"overall": overall, "reasons": reasons}, "certificates": certs, "submissions": subs}


@route("GET", "/api/assets/{id:int}")
def asset_detail(ctx):
    return _asset_bundle(ctx, scoped(ctx, "assets", ctx.p["id"], "a"))


@route("PUT", "/api/assets/{id:int}", roles=STAFF)
def asset_update(ctx):
    a = scoped(ctx, "assets", ctx.p["id"], "a")
    vals = {}
    for f, mx in (("manufacturer", 80), ("model", 120), ("cpu", 120), ("storage_summary", 300), ("bios", 120), ("os_info", 120), ("chassis", 40)):
        if f in ctx.body:
            vals[f] = need(ctx.body, f, mx, False)
    if "ram_gb" in ctx.body:
        vals["ram_gb"] = float(ctx.body["ram_gb"]) if ctx.body["ram_gb"] not in (None, "") else None
    if "serial" in ctx.body:
        if ctx.db.one("SELECT 1 AS x FROM erasures WHERE asset_id = ? AND source = 'engine'", (a["id"],)):
            raise HTTPError(409, "The serial number cannot change after engine results exist for this asset.")
        vals["serial"] = need(ctx.body, "serial", 80)
    if vals:
        vals["updated_at"] = utcnow()
        ctx.db.update("assets", a["id"], vals)
        audit(ctx.user, "asset.updated", "asset", a["id"], vals, ctx.ip, a["company_id"])
    return _asset_bundle(ctx, ctx.db.one("SELECT * FROM assets WHERE id = ?", (a["id"],)))


@route("POST", "/api/assets/{id:int}/inspection", roles=STAFF)
def asset_inspection(ctx):
    """Technician observations. The only check result a person may type is BLOCKED
    (the safe direction). CLEAR can only come from the engine."""
    a = scoped(ctx, "assets", ctx.p["id"], "a")
    b = ctx.body
    last = ctx.db.one("SELECT * FROM inspections WHERE asset_id = ? ORDER BY id DESC LIMIT 1", (a["id"],))
    check = last["check_result"] if last else "NOT CHECKED"
    if b.get("check_result"):
        if b["check_result"] != "BLOCKED":
            raise HTTPError(422, "A person can only mark a laptop BLOCKED. CLEAR comes from the engine check.")
        check = "BLOCKED"
    grade = need(b, "cosmetic_grade", 2, False)
    if grade and grade.upper() not in ("A", "B", "C", "D"):
        raise HTTPError(422, "Grade must be A, B, C or D.")
    ctx.db.insert("inspections", {"asset_id": a["id"], "check_result": check, "absolute_result": last["absolute_result"] if last else None,
                                  "enrolment_result": last["enrolment_result"] if last else None,
                                  "cosmetic_grade": grade.upper() if grade else None,
                                  "issues": json.dumps([str(x)[:60] for x in (b.get("issues") or [])][:20]),
                                  "notes": need(b, "notes", 2000, False), "recorded_by": ctx.user["id"], "recorded_at": utcnow()})
    refresh_asset(a["id"], ctx.user)
    audit(ctx.user, "asset.inspection", "asset", a["id"], {"check": check, "grade": grade}, ctx.ip, a["company_id"])
    return _asset_bundle(ctx, ctx.db.one("SELECT * FROM assets WHERE id = ?", (a["id"],)))


@route("POST", "/api/assets/{id:int}/tests", roles=STAFF)
def asset_test(ctx):
    a = scoped(ctx, "assets", ctx.p["id"], "a")
    comp = need(ctx.body, "component", 40)
    if comp not in COMPONENTS:
        raise HTTPError(422, "Unknown component. Use one of: %s." % ", ".join(COMPONENTS))
    st = need(ctx.body, "status", 20)
    if st not in TEST_STATUSES:
        raise HTTPError(422, "Status must be one of %s." % ", ".join(TEST_STATUSES))
    ctx.db.execute("DELETE FROM test_results WHERE asset_id = ? AND component = ?", (a["id"], comp))
    ctx.db.insert("test_results", {"asset_id": a["id"], "component": comp, "status": st, "notes": need(ctx.body, "notes", 500, False),
                                   "details": "{}", "tested_by": ctx.user["name"], "tested_at": utcnow()})
    refresh_asset(a["id"], ctx.user)
    audit(ctx.user, "asset.test", "asset", a["id"], {"component": comp, "status": st}, ctx.ip, a["company_id"])
    return _asset_bundle(ctx, ctx.db.one("SELECT * FROM assets WHERE id = ?", (a["id"],)))


@route("POST", "/api/assets/{id:int}/erasures", roles=STAFF)
def asset_erasure_manual(ctx):
    """Record that a wipe was done outside the engine. It is stored as NOT verified
    and can never receive a certificate: only the engine can prove an erasure."""
    a = scoped(ctx, "assets", ctx.p["id"], "a")
    b = ctx.body
    ctx.db.insert("erasures", {"asset_id": a["id"], "device_path": need(b, "device_path", 60, False), "model": need(b, "model", 120, False),
                               "serial": need(b, "serial", 80, False), "method": need(b, "method", 200), "level": None, "standard": None,
                               "passes": 1, "status": "Erased - not verified", "verified": 0, "verification": json.dumps({"manual": True}),
                               "started": need(b, "started", 40, False), "finished": need(b, "finished", 40, False), "technician": ctx.user["name"],
                               "source": "manual", "demo": 0, "created_at": utcnow()})
    refresh_asset(a["id"], ctx.user)
    audit(ctx.user, "asset.erasure_manual", "asset", a["id"], {"method": b.get("method")}, ctx.ip, a["company_id"])
    return _asset_bundle(ctx, ctx.db.one("SELECT * FROM assets WHERE id = ?", (a["id"],)))


@route("POST", "/api/assets/{id:int}/assess", roles=STAFF)
def asset_assess(ctx):
    a = scoped(ctx, "assets", ctx.p["id"], "a")
    ov, reasons = refresh_asset(a["id"], ctx.user)
    audit(ctx.user, "asset.assessed", "asset", a["id"], {"overall": ov}, ctx.ip, a["company_id"])
    return {"overall": ov, "reasons": reasons}


# ============================================================== certificates
def _cert_row(c):
    p = json.loads(c["payload"])
    st = certificate_status(c)
    return {"id": c["id"], "number": c["number"], "asset_id": c["asset_id"], "asset_tag": p["asset"]["tag"], "serial": p["asset"]["serial"],
            "device": ("%s %s" % (p["asset"]["manufacturer"] or "", p["asset"]["model"] or "")).strip(), "customer": p["customer"],
            "method": p["erasure"]["method"], "issued_at": dublin(c["issued_at"]), "demo": bool(c["demo"]), "state": st["state"]}


@route("GET", "/api/certificates")
def certificates_list(ctx):
    f, p = tenant_filter(ctx.user, "c")
    sql = "SELECT c.* FROM certificates c WHERE %s" % f
    p = list(p)
    if ctx.q.get("q"):
        sql += " AND (c.number LIKE ? OR c.payload LIKE ?)"
        p += [like(ctx.q["q"])] * 2
    rows = ctx.db.query(sql + " ORDER BY c.id DESC LIMIT ? OFFSET ?", p + list(page(ctx)))
    return [_cert_row(r) for r in rows]


@route("GET", "/api/certificates/{id:int}")
def certificate_detail(ctx):
    c = scoped(ctx, "certificates", ctx.p["id"], "c")
    out = _cert_row(c)
    out["payload"] = json.loads(c["payload"])
    out["payload_sha256"], out["signature"], out["key_id"] = c["payload_sha256"], c["signature"], c["key_id"]
    return out


@route("GET", "/api/certificates/{id:int}/pdf")
def certificate_pdf(ctx):
    c = scoped(ctx, "certificates", ctx.p["id"], "c")
    co = ctx.db.one("SELECT * FROM companies WHERE id = ?", (c["company_id"],))
    pdf = pdfgen.certificate_pdf(c, co, certificate_status(c))
    audit(ctx.user, "certificate.downloaded", "certificate", c["id"], {"number": c["number"]}, ctx.ip, c["company_id"])
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="%s.pdf"' % c["number"]})


@route("GET", "/api/erasures/{id:int}/blockers", roles=STAFF)
def erasure_blockers(ctx):
    e = ctx.db.one("SELECT * FROM erasures WHERE id = ?", (ctx.p["id"],))
    if not e:
        raise HTTPError(404, "Not found.")
    a = scoped(ctx, "assets", e["asset_id"], "a")
    return {"blockers": certificate_blockers(e, a, ctx.db.one("SELECT * FROM companies WHERE id = ?", (a["company_id"],)))}


@route("POST", "/api/erasures/{id:int}/certificate", roles=STAFF)
def certificate_issue(ctx):
    c = issue_certificate(ctx.user, ctx.p["id"], ctx.ip)
    return _cert_row(c)


@route("POST", "/api/certificates/{id:int}/revoke", roles=ADMINS)
def certificate_revoke(ctx):
    revoke_certificate(ctx.user, ctx.p["id"], ctx.body.get("reason"), ctx.ip)
    return {"ok": True}


# ================================================================== reports
@route("GET", "/api/report-types")
def report_types(ctx):
    r = ctx.user["role"]
    ok = lambda m: m["roles"] == "all" or (m["roles"] == "staff" and r != "customer") or (m["roles"] == "admin" and r in ADMINS)
    out = [{"type": k, "title": v["title"], "desc": v["desc"]} for k, v in reportdefs.REPORT_TYPES.items() if ok(v)]
    if r != "customer":
        out.insert(0, {"type": "laptop_report", "title": "Individual laptop report", "desc": "Full report for one asset (pick it on the asset page)."})
    return out


def _store_report(ctx, rtype, title, fname, content, params, company_id, customer_id=None):
    rid = ctx.db.insert("reports", {"company_id": company_id, "report_type": rtype, "title": title, "params": json.dumps(params), "file_name": fname,
                                    "content": content, "sha256": hashlib.sha256(content).hexdigest(), "generated_by": ctx.user["id"],
                                    "customer_id": customer_id, "created_at": utcnow()})
    audit(ctx.user, "report.generated", "report", rid, {"type": rtype, "file": fname}, ctx.ip, company_id)
    return {"id": rid, "file_name": fname, "title": title}


@route("POST", "/api/reports")
def report_generate(ctx):
    kind = need(ctx.body, "type", 40)
    fmt = (ctx.body.get("format") or "pdf").lower()
    params = ctx.body.get("params") or {}
    if fmt not in ("pdf", "csv"):
        raise HTTPError(422, "format must be pdf or csv.")
    stamp = time.strftime("%Y%m%d-%H%M")
    if kind == "laptop_report":
        if ctx.user["role"] == "customer":
            raise HTTPError(403, "Not allowed.")
        a = scoped(ctx, "assets", int(params.get("asset_id") or 0), "a")
        data = _asset_bundle(ctx, a)
        if fmt == "csv":
            cols = ["Component", "Result", "Notes"]
            rows = [[t["component"], t["status"], t["notes"]] for t in data["tests"]]
            content = pdfgen.csv_bytes(cols, rows)
        else:
            content = pdfgen.asset_report_pdf(data, ctx.db.one("SELECT * FROM companies WHERE id = ?", (a["company_id"],)))
        return _store_report(ctx, kind, "Laptop report %s" % a["asset_tag"], "laptop-%s-%s.%s" % (re.sub(r"[^A-Za-z0-9_-]", "_", a["asset_tag"]), stamp, fmt),
                             content, {"asset_id": a["id"]}, a["company_id"], a["customer_id"])
    title, sub, cols, rows, summary = reportdefs.build(kind, ctx.user, params)
    co_id = ctx.user["company_id"] or (int(params["company_id"]) if params.get("company_id") else None)
    co = ctx.db.one("SELECT * FROM companies WHERE id = ?", (co_id,)) if co_id else None
    content = pdfgen.csv_bytes(cols, rows) if fmt == "csv" else pdfgen.table_pdf(title, sub, cols, rows, co, summary)
    return _store_report(ctx, kind, title, "%s-%s.%s" % (kind, stamp, fmt), content, params, co_id,
                         ctx.user["customer_id"] if ctx.user["role"] == "customer" else params.get("customer_id"))


@route("GET", "/api/reports")
def reports_list(ctx):
    u = ctx.user
    if u["role"] == "super_admin":
        w, p = "1 = 1", []
    elif u["role"] == "customer":
        w, p = "r.company_id = ? AND r.customer_id = ?", [u["company_id"], u["customer_id"]]
    else:
        w, p = "r.company_id = ?", [u["company_id"]]
    sql = "SELECT r.id, r.report_type, r.title, r.file_name, r.sha256, r.created_at, x.name AS generated_by FROM reports r LEFT JOIN users x ON x.id = r.generated_by WHERE %s" % w
    if ctx.q.get("q"):
        sql += " AND (r.title LIKE ? OR r.file_name LIKE ?)"
        p += [like(ctx.q["q"])] * 2
    rows = ctx.db.query(sql + " ORDER BY r.id DESC LIMIT ? OFFSET ?", p + list(page(ctx)))
    for r in rows:
        r["created_at"] = dublin(r["created_at"])
    return rows


@route("GET", "/api/reports/{id:int}/download")
def report_download(ctx):
    r = ctx.db.one("SELECT * FROM reports WHERE id = ?", (ctx.p["id"],))
    u = ctx.user
    ok = r and (u["role"] == "super_admin" or (r["company_id"] == u["company_id"] and (u["role"] != "customer" or r["customer_id"] == u["customer_id"])))
    if not ok:
        raise HTTPError(404, "Not found.")
    mt = "application/pdf" if r["file_name"].endswith(".pdf") else "text/csv; charset=utf-8"
    return Response(r["content"], media_type=mt, headers={"Content-Disposition": 'inline; filename="%s"' % r["file_name"]})


# ================================================================== dashboard
@route("GET", "/api/dashboard")
def dashboard(ctx):
    f, p = tenant_filter(ctx.user, "a")
    return dashboard_stats(ctx.user, f, p)


# ======================================================== stations and keys
@route("GET", "/api/stations", roles=ADMINS)
def stations_list(ctx):
    w, p = ("1 = 1", []) if ctx.user["role"] == "super_admin" else ("company_id = ?", [ctx.user["company_id"]])
    rows = ctx.db.query("SELECT id, company_id, name, token_prefix, active, last_seen, created_at FROM stations WHERE %s ORDER BY id DESC" % w, p)
    for r in rows:
        r["last_seen"], r["created_at"] = dublin(r["last_seen"]), dublin(r["created_at"])
    return rows


@route("POST", "/api/stations", roles=ADMINS)
def station_create(ctx):
    co = get_company(ctx)
    token = "itad_st_" + secrets.token_urlsafe(32)
    sid = ctx.db.insert("stations", {"company_id": co["id"], "name": need(ctx.body, "name", 80), "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                                     "token_prefix": token[:14], "active": 1, "created_at": utcnow()})
    audit(ctx.user, "station.created", "station", sid, {"name": ctx.body["name"]}, ctx.ip, co["id"])
    return {"id": sid, "token": token, "note": "Copy this token now. It is shown only once."}


@route("DELETE", "/api/stations/{id:int}", roles=ADMINS)
def station_disable(ctx):
    s = ctx.db.one("SELECT * FROM stations WHERE id = ?", (ctx.p["id"],))
    if not s or (ctx.user["role"] != "super_admin" and s["company_id"] != ctx.user["company_id"]):
        raise HTTPError(404, "Not found.")
    ctx.db.update("stations", s["id"], {"active": 0})
    audit(ctx.user, "station.disabled", "station", s["id"], {}, ctx.ip, s["company_id"])
    return {"ok": True}


@route("GET", "/api/keys", roles=ADMINS)
def keys_list(ctx):
    w, p = ("1 = 1", []) if ctx.user["role"] == "super_admin" else ("company_id = ?", [ctx.user["company_id"]])
    rows = ctx.db.query("SELECT id, company_id, key_id, label, active, created_at FROM trusted_keys WHERE %s ORDER BY id DESC" % w, p)
    for r in rows:
        r["created_at"] = dublin(r["created_at"])
    return rows


def _add_key(ctx, company_id, pub_hex, label):
    try:
        raw = bytes.fromhex(pub_hex)
        if len(raw) != 32:
            raise ValueError
    except ValueError:
        raise HTTPError(422, "Public key must be 32 bytes in hex (the Ed25519 key from itad_signing_public.pem).")
    kid = key_id_of(raw)
    if ctx.db.one("SELECT 1 AS x FROM trusted_keys WHERE company_id = ? AND key_id = ?", (company_id, kid)):
        raise HTTPError(409, "This key is already trusted.")
    ctx.db.insert("trusted_keys", {"company_id": company_id, "key_id": kid, "public_key_hex": pub_hex.lower(), "label": label,
                                   "active": 1, "added_by": ctx.user["id"], "created_at": utcnow()})
    audit(ctx.user, "key.trusted", "key", kid, {"label": label}, ctx.ip, company_id)
    return kid


@route("POST", "/api/keys", roles=ADMINS)
def key_add(ctx):
    co = get_company(ctx)
    pub = need(ctx.body, "public_key", 3000)
    if "BEGIN PUBLIC KEY" in pub:                     # PEM from the ITAD_DATA stick
        from cryptography.hazmat.primitives import serialization
        try:
            k = serialization.load_pem_public_key(pub.encode())
            pub = k.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        except Exception:
            raise HTTPError(422, "That is not a valid public key PEM.")
    return {"key_id": _add_key(ctx, co["id"], pub.strip(), need(ctx.body, "label", 80, False))}


@route("POST", "/api/keys/from-submission/{id:int}", roles=ADMINS)
def key_trust_submission(ctx):
    s = ctx.db.one("SELECT * FROM submissions WHERE id = ?", (ctx.p["id"],))
    if not s or (ctx.user["role"] != "super_admin" and s["company_id"] != ctx.user["company_id"]):
        raise HTTPError(404, "Not found.")
    if not s["signature_ok"]:
        raise HTTPError(409, "Only a report with a cryptographically valid signature can be used to trust a key.")
    pub = (json.loads(s["payload"]).get("integrity") or {}).get("public_key", "")
    kid = _add_key(ctx, s["company_id"], pub, need(ctx.body, "label", 80, False) or "Added from report %d" % s["id"])
    # Refresh the trust flag on matching submissions (the flag is derived; evidence itself is unchanged).
    ctx.db.execute("UPDATE submissions SET key_trusted = 1 WHERE company_id = ? AND key_id = ? AND signature_ok = 1", (s["company_id"], kid))
    return {"key_id": kid}


@route("DELETE", "/api/keys/{id:int}", roles=ADMINS)
def key_disable(ctx):
    k = ctx.db.one("SELECT * FROM trusted_keys WHERE id = ?", (ctx.p["id"],))
    if not k or (ctx.user["role"] != "super_admin" and k["company_id"] != ctx.user["company_id"]):
        raise HTTPError(404, "Not found.")
    ctx.db.update("trusted_keys", k["id"], {"active": 0})
    ctx.db.execute("UPDATE submissions SET key_trusted = 0 WHERE company_id = ? AND key_id = ?", (k["company_id"], k["key_id"]))
    audit(ctx.user, "key.untrusted", "key", k["key_id"], {}, ctx.ip, k["company_id"])
    return {"ok": True}


@route("GET", "/api/submissions", roles=STAFF)
def submissions_list(ctx):
    w, p = ("1 = 1", []) if ctx.user["role"] == "super_admin" else ("s.company_id = ?", [ctx.user["company_id"]])
    rows = ctx.db.query("SELECT s.id, s.received_at, s.job_ref, s.asset_tag, s.serial, s.status, s.reason, s.fingerprint_ok, s.signed, s.signature_ok, "
                        "s.key_id, s.key_trusted, s.demo, st.name AS station FROM submissions s LEFT JOIN stations st ON st.id = s.station_id "
                        "WHERE %s ORDER BY s.id DESC LIMIT ? OFFSET ?" % w, p + list(page(ctx)))
    for r in rows:
        r["received_at"] = dublin(r["received_at"])
    return rows


@route("POST", "/api/submissions", roles=STAFF, maxbody=8_000_000)
def submission_upload(ctx):
    """A person uploads a report file (for example from the USB stick) instead of a station pushing it."""
    co = get_company(ctx)
    return ingest_report(co["id"], ctx.body.get("report") or ctx.body, None, ctx.user, ctx.ip)


# ============================================================ engine (stations)
@route("POST", "/api/engine/submit", auth="station", maxbody=8_000_000)
def engine_submit(ctx):
    rate_limit("engine:%s" % ctx.station["id"], 120)
    res = ingest_report(ctx.station["company_id"], ctx.body, ctx.station, None, ctx.ip)
    return JSONResponse(res, status_code=200 if res["ok"] else 422)


@route("GET", "/api/engine/assets/{tag}", auth="station")
def engine_asset(ctx):
    """Safety cross-check before a wipe. The station asks, the platform answers,
    but the station still enforces its own physical-device checks."""
    a = ctx.db.one("SELECT * FROM assets WHERE company_id = ? AND asset_tag = ?", (ctx.station["company_id"], ctx.p["tag"]))
    serial = ctx.q.get("serial", "")
    job = ctx.q.get("job", "")
    if not a:
        return {"known": False, "wipe_allowed": False, "reason": "Asset ID is not on the platform."}
    j = ctx.db.one("SELECT * FROM jobs WHERE id = ?", (a["job_id"],)) if a["job_id"] else None
    reasons = []
    if serial and serial.strip().lower() != a["serial"].strip().lower():
        reasons.append("Serial number does not match the platform record.")
    if job and (not j or j["job_ref"] != job):
        reasons.append("Asset belongs to a different job.")
    if a["status"] == "blocked":
        reasons.append("Asset is BLOCKED (lock found).")
    if not j or j["status"] in ("completed", "cancelled"):
        reasons.append("The job is not open.")
    return {"known": True, "asset_tag": a["asset_tag"], "serial": a["serial"], "job_ref": j["job_ref"] if j else None,
            "status": a["status"], "wipe_allowed": not reasons, "reason": " ".join(reasons),
            "note": "Advisory only. The station always runs its own live-USB, OS-disk, removable-disk and serial checks."}


@route("GET", "/api/engine/ping", auth="station")
def engine_ping(ctx):
    return {"ok": True, "station": ctx.station["name"], "time": dublin(utcnow())}


# =================================================================== audit
@route("GET", "/api/audit", roles=ADMINS)
def audit_list(ctx):
    w, p = ("1 = 1", []) if ctx.user["role"] == "super_admin" else ("l.company_id = ?", [ctx.user["company_id"]])
    rows = ctx.db.query("SELECT l.id, l.created_at, l.action, l.entity, l.entity_id, l.detail, l.ip, u.name AS user_name FROM audit_log l "
                        "LEFT JOIN users u ON u.id = l.user_id WHERE %s ORDER BY l.id DESC LIMIT ? OFFSET ?" % w, p + list(page(ctx, 100, 500)))
    for r in rows:
        r["created_at"], r["detail"] = dublin(r["created_at"]), jloads(r["detail"], {})
    return rows


@route("GET", "/api/audit/verify", roles=ADMINS)
def audit_verify(ctx):
    ok, bad = verify_audit_chain()
    return {"intact": ok, "first_bad_row": bad}


# ===================================================== development simulation
@route("POST", "/api/dev/simulate-engine", roles=STAFF)
def simulate_engine(ctx):
    """Development only. Builds a report like laptop_window.py would, clearly marked
    as demonstration data, and sends it through the same import path as a real one."""
    if config.IS_PROD:
        raise HTTPError(404, "Not found.")
    a = scoped(ctx, "assets", int(need(ctx.body, "asset_id", 12)), "a")
    job = ctx.db.one("SELECT * FROM jobs WHERE id = ?", (a["job_id"],)) if a["job_id"] else None
    if not job:
        raise HTTPError(409, "Put the asset in a job first.")
    scen = ctx.body.get("scenario") or "pass"
    rep = simulated_report(a, job, scen, ctx.user["name"])
    return ingest_report(a["company_id"], rep, None, ctx.user, ctx.ip)


def simulated_report(asset, job, scenario, tech):
    t0 = "2026-11-02 10:16:10"
    tests = []
    for comp in COMPONENTS:
        st = "N/A" if comp == "Touchscreen" else "PASS"
        notes = ["Simulated result"]
        if scenario == "fail" and comp == "Battery":
            st, notes = "FAIL", ["Health 38% - below the 60% limit"]
        if scenario == "incomplete" and comp in ("Keyboard", "Webcam"):
            st, notes = "WARN", ["Not tested"]
        tests.append({"component": comp, "status": st, "notes": notes, "details": {}})
    wiped = scenario != "blocked"
    rep = {"tool": "laptop_window.py", "version": "simulation", "time": t0 + " (Europe/Dublin)", "job_id": job["job_ref"],
           "asset_id": asset["asset_tag"], "serial": asset["serial"], "technician": tech, "make_model": ("%s %s" % (asset["manufacturer"] or "", asset["model"] or "")).strip() or "Simulated Laptop",
           "cpu": asset["cpu"] or "Simulated CPU", "ram": "16 GB", "storage": "nvme0n1 (SSD) 256 GB", "bios": "Simulated BIOS", "chassis": "Laptop",
           "check_result": "BLOCKED" if scenario == "blocked" else "CLEAR", "absolute": "ACTIVE" if scenario == "blocked" else "CLEAR", "enrolment": "CLEAN",
           "wipe_state": "WIPED" if wiped else "NOT RUN", "tests": tests if wiped else [],
           "erasures": [{"path": "/dev/nvme0n1", "model": "Simulated NVMe", "capacity_gb": 256, "serial": "SIMULATED0001", "passes": 1,
                         "method": "Purge - cryptographic erase", "level": "Purge", "standard": "NIST SP 800-88 Rev. 2 Purge | IEEE 2883-2022 Purge",
                         "verified": True, "status": "Success", "technician": tech, "started": t0, "finished": "2026-11-02 10:17:32",
                         "erasure_id": str(int(secrets.token_hex(4), 16))}] if wiped else [],
           "observations": {"grade": "B", "issues": ["Light scratches on the lid"], "notes": "Simulation"}, "demo": True,
           "nonce": secrets.token_hex(6)}
    rep["integrity"] = {"algorithm": "SHA-256 + Ed25519", "sha256": fingerprint(rep), "signature": "", "key_id": "", "signed": False,
                        "note": "simulation - not signed"}
    return rep


# ============================================================== route builder
async def _auth(ctx, request, auth, roles, method):
    if auth == "public":
        return
    if auth == "station":
        h = request.headers.get("authorization", "")
        tok = h[7:].strip() if h.lower().startswith("bearer ") else ""
        st = await run_in_threadpool(lambda: get_db().one("SELECT * FROM stations WHERE token_hash = ? AND active = 1",
                                                          (hashlib.sha256(tok.encode()).hexdigest(),))) if tok else None
        if not st:
            raise HTTPError(401, "Invalid or disabled station token.")
        await run_in_threadpool(lambda: get_db().update("stations", st["id"], {"last_seen": utcnow()}))
        ctx.station = st
        return
    user, sess = await run_in_threadpool(load_session, request.cookies.get(COOKIE))
    ctx.user, ctx.session = user, sess
    if auth == "optional":
        return
    if not user:
        raise HTTPError(401, "Please sign in.")
    if method not in SAFE:
        sent = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(sent, sess["csrf"]):
            raise HTTPError(403, "Security token missing. Reload the page.")
    if roles and user["role"] not in roles:
        raise HTTPError(403, "Your role is not allowed to do this.")


def _make_handler(method, fn, roles, auth, maxbody):
    async def handler(request):
        ctx = SimpleNamespace(req=request, body={}, user=None, session=None, station=None, db=get_db(),
                              q=dict(request.query_params), p=request.path_params,
                              ip=(request.client.host if request.client else ""))
        try:
            await _auth(ctx, request, auth, roles, method)
            if method not in SAFE:
                raw = await request.body()
                if len(raw) > maxbody:
                    raise HTTPError(413, "Request is too large.")
                if raw:
                    try:
                        ctx.body = json.loads(raw)
                    except ValueError:
                        raise HTTPError(400, "Body must be JSON.")
                    if not isinstance(ctx.body, dict):
                        raise HTTPError(400, "Body must be a JSON object.")
            result = await run_in_threadpool(fn, ctx)
        except HTTPError as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status)
        if isinstance(result, Response):
            return result
        return JSONResponse(result)
    return handler


def build_routes():
    out = []
    for method, path, fn, roles, auth, maxbody in ROUTES:
        out.append(Route(path, _make_handler(method, fn, roles, auth, maxbody), methods=[method]))
    return out
