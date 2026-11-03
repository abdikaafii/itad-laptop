"""Administration: company settings and logo, station keys, API tokens, team, customers,
platform-wide company management and the audit log."""
import hashlib
import io
import os
import re
import secrets

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file,
                   url_for)

from .authentication.routes import EMAIL_RE, send_password_link
from .core import security as S
from .core.audit import log
from .core.db import many, one, run, utcnow
from .core.util import arg, form, int_or_none, like, paginate, query_string

bp = Blueprint("admin", __name__)

COMPANY_ROLES = (("technician", "Technician"), ("company_admin", "Company Administrator"),
                 ("customer", "Customer"))


def _cid():
    """Company being managed. Company admins: their own. Super admins choose with ?company=."""
    if g.user["role"] == "super_admin":
        cid = int_or_none(request.values.get("company"))
        if not cid or not one("SELECT 1 FROM companies WHERE id = ?", (cid,)):
            abort(404)
        return cid
    return g.user["company_id"]


def _keep(cid):
    return {"company": cid} if g.user["role"] == "super_admin" else {}


# ---------------------------------------------------------- company settings
@bp.route("/company", methods=["GET", "POST"])
@S.roles_required("super_admin", "company_admin")
def company():
    cid = _cid()
    co = one("SELECT * FROM companies WHERE id = ?", (cid,))
    if request.method == "POST":
        name = form("name", 120)
        if not name:
            flash("The company needs a name.", "error")
        else:
            run("UPDATE companies SET name = ?, legal_name = ?, address = ?, email = ?, phone = ?, website = ?, "
                "certificate_footer = ?, auto_issue_certificates = ? WHERE id = ?",
                (name, form("legal_name", 160), form("address", 300), form("email", 160), form("phone", 60),
                 form("website", 160), form("certificate_footer", 400),
                 1 if request.form.get("auto_issue") else 0, cid))
            log("company.updated", "company", cid, name, company_id=cid)
            flash("Company details saved.", "ok")
        return redirect(url_for("admin.company", **_keep(cid)))
    keys = many("SELECT * FROM signing_keys WHERE company_id = ? ORDER BY id DESC", (cid,))
    pending = many("SELECT public_key_hex, key_id, COUNT(*) AS n, MAX(received_at) AS last FROM engine_reports "
                   "WHERE company_id = ? AND signature_status = 'unknown_key' GROUP BY public_key_hex", (cid,))
    tokens = many("SELECT * FROM api_tokens WHERE company_id = ? ORDER BY id DESC", (cid,))
    return render_template("admin/company.html", co=co, keys=keys, pending=pending, tokens=tokens,
                           new_token=None, keep=_keep(cid))


@bp.route("/company/logo", methods=["POST"])
@S.roles_required("super_admin", "company_admin")
def company_logo():
    cid = _cid()
    up = request.files.get("logo")
    if not up or not up.filename:
        flash("Choose a PNG or JPEG image.", "error")
        return redirect(url_for("admin.company", **_keep(cid)))
    raw = up.read(current_app.config["MAX_LOGO_BYTES"] + 1)
    if len(raw) > current_app.config["MAX_LOGO_BYTES"]:
        flash("The logo must be smaller than 1 MB.", "error")
        return redirect(url_for("admin.company", **_keep(cid)))
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        if img.format not in ("PNG", "JPEG"):
            raise ValueError("format")
        img.load()
        if max(img.size) > 3000:
            raise ValueError("size")
        out = io.BytesIO()
        img.convert("RGBA").save(out, "PNG")  # re-encoding drops any hidden payload or metadata
    except Exception:
        flash("That file is not a usable PNG or JPEG image (max 3000 pixels wide or tall).", "error")
        return redirect(url_for("admin.company", **_keep(cid)))
    folder = os.path.join(current_app.config["UPLOAD_DIR"], "logos")
    os.makedirs(folder, exist_ok=True)
    name = "company-%d-%s.png" % (cid, secrets.token_hex(6))
    with open(os.path.join(folder, name), "wb") as f:
        f.write(out.getvalue())
    old = one("SELECT logo_file FROM companies WHERE id = ?", (cid,))["logo_file"]
    run("UPDATE companies SET logo_file = ? WHERE id = ?", (name, cid))
    if old and re.fullmatch(r"company-\d+-[0-9a-f]+\.png", old):
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    log("company.logo", "company", cid, name, company_id=cid)
    flash("Logo updated. It appears on certificates and reports.", "ok")
    return redirect(url_for("admin.company", **_keep(cid)))


@bp.route("/company/logo/<int:cid>")
@S.login_required
def logo_file(cid):
    """Company logos are shown inside the signed-in area, only to people of that company."""
    if g.user["role"] != "super_admin" and g.user["company_id"] != cid:
        abort(404)
    co = one("SELECT logo_file FROM companies WHERE id = ?", (cid,))
    if not co or not re.fullmatch(r"company-\d+-[0-9a-f]+\.png", co["logo_file"] or ""):
        abort(404)
    path = os.path.join(current_app.config["UPLOAD_DIR"], "logos", co["logo_file"])
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype="image/png", max_age=300)


# --------------------------------------------------------------- station keys
def _parse_public_key(text):
    text = (text or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return text.lower()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key = serialization.load_pem_public_key(text.encode("utf-8"))
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("not an Ed25519 key")
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


@bp.route("/company/keys", methods=["POST"])
@S.roles_required("super_admin", "company_admin")
def key_add():
    cid = _cid()
    label = form("label", 80) or "Station key"
    try:
        pub = _parse_public_key(request.form.get("public_key"))
    except Exception:
        flash("That is not a valid Ed25519 public key. Paste the whole contents of itad_signing_public.pem "
              "(or the 64-character key in hex).", "error")
        return redirect(url_for("admin.company", **_keep(cid)))
    key_id = hashlib.sha256(bytes.fromhex(pub)).hexdigest()[:16]
    exp = form("confirm_key_id", 40).lower()
    if exp and exp != key_id:
        flash("The key ID you typed (%s) does not match this key (%s). Nothing was saved." % (exp, key_id), "error")
        return redirect(url_for("admin.company", **_keep(cid)))
    if one("SELECT 1 FROM signing_keys WHERE company_id = ? AND key_id = ?", (cid, key_id)):
        flash("That key is already registered.", "error")
        return redirect(url_for("admin.company", **_keep(cid)))
    run("INSERT INTO signing_keys (company_id, key_id, public_key_hex, label, created_at) VALUES (?,?,?,?,?)",
        (cid, key_id, pub, label, utcnow()))
    log("key.registered", "signing_key", key_id, label, company_id=cid)
    flash("Station key %s registered. Use 'Re-check' on a laptop to re-verify its stored report." % key_id, "ok")
    return redirect(url_for("admin.company", **_keep(cid)))


@bp.route("/company/keys/<int:key_pk>/toggle", methods=["POST"])
@S.roles_required("super_admin", "company_admin")
def key_toggle(key_pk):
    cid = _cid()
    k = one("SELECT * FROM signing_keys WHERE id = ? AND company_id = ?", (key_pk, cid))
    if not k:
        abort(404)
    run("UPDATE signing_keys SET is_active = ? WHERE id = ?", (0 if k["is_active"] else 1, key_pk))
    log("key.toggled", "signing_key", k["key_id"], "active=%d" % (0 if k["is_active"] else 1), company_id=cid)
    return redirect(url_for("admin.company", **_keep(cid)))


# ----------------------------------------------------------------- API tokens
@bp.route("/company/tokens", methods=["POST"])
@S.roles_required("super_admin", "company_admin")
def token_create():
    cid = _cid()
    name = form("name", 80) or "Processing station"
    raw = "itad_" + secrets.token_urlsafe(32)
    run("INSERT INTO api_tokens (company_id, name, token_hash, prefix, created_by, created_at) VALUES (?,?,?,?,?,?)",
        (cid, name, S.sha256_hex(raw), raw[:9], g.user["id"], utcnow()))
    log("token.created", "api_token", name, company_id=cid)
    co = one("SELECT * FROM companies WHERE id = ?", (cid,))
    keys = many("SELECT * FROM signing_keys WHERE company_id = ? ORDER BY id DESC", (cid,))
    tokens = many("SELECT * FROM api_tokens WHERE company_id = ? ORDER BY id DESC", (cid,))
    return render_template("admin/company.html", co=co, keys=keys, pending=[], tokens=tokens, new_token=raw,
                           keep=_keep(cid))


@bp.route("/company/tokens/<int:tok>/revoke", methods=["POST"])
@S.roles_required("super_admin", "company_admin")
def token_revoke(tok):
    cid = _cid()
    t = one("SELECT * FROM api_tokens WHERE id = ? AND company_id = ?", (tok, cid))
    if not t:
        abort(404)
    run("UPDATE api_tokens SET is_active = 0 WHERE id = ?", (tok,))
    log("token.revoked", "api_token", t["name"], company_id=cid)
    flash("Token revoked.", "ok")
    return redirect(url_for("admin.company", **_keep(cid)))


# ----------------------------------------------------------------------- team
@bp.route("/team")
@S.roles_required("super_admin", "company_admin")
def team():
    where, params = "", []
    if g.user["role"] != "super_admin":
        where, params = " AND u.company_id = ?", [g.user["company_id"]]
    q = arg("q")
    if q:
        where += " AND (u.full_name LIKE ? ESCAPE '\\' OR u.email LIKE ? ESCAPE '\\')"
        params += [like(q)] * 2
    page = paginate("SELECT COUNT(*) FROM users u WHERE 1=1" + where,
                    "SELECT u.*, c.name AS company_name, cu.name AS customer_name FROM users u "
                    "LEFT JOIN companies c ON c.id = u.company_id LEFT JOIN customers cu ON cu.id = u.customer_id "
                    "WHERE 1=1" + where + " ORDER BY u.id DESC", params)
    return render_template("admin/team.html", page=page, qs=query_string)


@bp.route("/team/new", methods=["GET", "POST"])
@S.roles_required("super_admin", "company_admin")
def team_new():
    if g.user["role"] == "super_admin":
        companies = many("SELECT id, name FROM companies ORDER BY name")
        cid = int_or_none(request.values.get("company_id")) or (companies[0]["id"] if companies else None)
    else:
        companies, cid = [], g.user["company_id"]
    customers = many("SELECT id, name FROM customers WHERE company_id = ? ORDER BY name", (cid,)) if cid else []
    if request.method == "POST":
        name, email, role = form("full_name", 120), form("email", 254).lower(), form("role", 20)
        cust = int_or_none(form("customer_id"))
        errors = []
        if not name:
            errors.append("Enter a name.")
        if not EMAIL_RE.match(email):
            errors.append("Enter a valid email address.")
        elif one("SELECT 1 FROM users WHERE email = ?", (email,)):
            errors.append("That email address already has an account.")
        if role not in dict(COMPANY_ROLES):
            errors.append("Choose a role.")
        if not cid or not one("SELECT 1 FROM companies WHERE id = ?", (cid,)):
            errors.append("Choose a company.")
        if role == "customer" and not one("SELECT 1 FROM customers WHERE id = ? AND company_id = ?", (cust, cid)):
            errors.append("Customer users must be linked to one of the company's customers.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/team_new.html", companies=companies, customers=customers, cid=cid,
                                   form=request.form, roles=COMPANY_ROLES), 400
        uid = run("INSERT INTO users (company_id, customer_id, email, full_name, role, password_hash, created_at) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (cid, cust if role == "customer" else None, email, name, role,
                   S.hash_password(secrets.token_urlsafe(24)), utcnow()))
        send_password_link(uid, email, name, invited=True)
        log("user.invited", "user", uid, "%s (%s)" % (email, role), company_id=cid)
        flash("%s has been invited. They will get an email to choose a password." % name, "ok")
        return redirect(url_for("admin.team"))
    return render_template("admin/team_new.html", companies=companies, customers=customers, cid=cid, form={},
                           roles=COMPANY_ROLES)


@bp.route("/team/<int:uid>/toggle", methods=["POST"])
@S.roles_required("super_admin", "company_admin")
def team_toggle(uid):
    u = one("SELECT * FROM users WHERE id = ?", (uid,))
    if not u or (g.user["role"] != "super_admin" and u["company_id"] != g.user["company_id"]):
        abort(404)
    if u["id"] == g.user["id"]:
        flash("You cannot switch off your own account.", "error")
    elif u["role"] == "super_admin" and g.user["role"] != "super_admin":
        abort(403)
    else:
        new = 0 if u["is_active"] else 1
        run("UPDATE users SET is_active = ?, session_epoch = session_epoch + 1 WHERE id = ?", (new, uid))
        log("user.toggled", "user", uid, "active=%d" % new, company_id=u["company_id"])
        flash("%s is now %s." % (u["full_name"], "active" if new else "switched off"), "ok")
    return redirect(url_for("admin.team"))


@bp.route("/team/<int:uid>/invite", methods=["POST"])
@S.roles_required("super_admin", "company_admin")
def team_reinvite(uid):
    u = one("SELECT * FROM users WHERE id = ?", (uid,))
    if not u or (g.user["role"] != "super_admin" and u["company_id"] != g.user["company_id"]):
        abort(404)
    send_password_link(uid, u["email"], u["full_name"], invited=True)
    flash("A new password link has been sent to %s." % u["email"], "ok")
    return redirect(url_for("admin.team"))


# ------------------------------------------------------------------ customers
@bp.route("/customers", methods=["GET", "POST"])
@S.roles_required("super_admin", "company_admin")
def customers():
    cid = g.user["company_id"] if g.user["role"] != "super_admin" else int_or_none(request.values.get("company"))
    if request.method == "POST":
        if not cid:
            abort(400, "Choose a company first.")
        name = form("name", 160)
        if not name:
            flash("Enter the customer's name.", "error")
        elif one("SELECT 1 FROM customers WHERE company_id = ? AND name = ?", (cid, name)):
            flash("That customer already exists.", "error")
        else:
            cu = run("INSERT INTO customers (company_id, name, contact_name, contact_email, address, created_at) "
                     "VALUES (?,?,?,?,?,?)", (cid, name, form("contact_name", 120), form("contact_email", 160),
                                              form("address", 300), utcnow()))
            log("customer.created", "customer", cu, name, company_id=cid)
            flash("Customer added.", "ok")
        return redirect(url_for("admin.customers", **_keep(cid)) if cid else url_for("admin.customers"))
    sql, p = ("", []) if g.user["role"] == "super_admin" and not cid else (" WHERE c.company_id = ?", [cid])
    rows = many("SELECT c.*, co.name AS company_name, "
                "(SELECT COUNT(*) FROM assets a WHERE a.customer_id = c.id) AS assets, "
                "(SELECT COUNT(*) FROM jobs j WHERE j.customer_id = c.id) AS jobs "
                "FROM customers c JOIN companies co ON co.id = c.company_id" + sql + " ORDER BY c.name", p)
    companies = many("SELECT id, name FROM companies ORDER BY name") if g.user["role"] == "super_admin" else []
    return render_template("admin/customers.html", rows=rows, companies=companies, cid=cid)


# ------------------------------------------------------------ platform (super)
@bp.route("/admin/companies", methods=["GET", "POST"])
@S.roles_required("super_admin")
def companies():
    if request.method == "POST":
        name = form("name", 120)
        if not name:
            flash("Enter a company name.", "error")
        else:
            cid = run("INSERT INTO companies (name, legal_name, created_at) VALUES (?,?,?)", (name, name, utcnow()))
            log("company.created", "company", cid, name, company_id=cid)
            flash("Company created. Now invite its first administrator under Team.", "ok")
        return redirect(url_for("admin.companies"))
    rows = many("SELECT c.*, (SELECT COUNT(*) FROM users u WHERE u.company_id = c.id) AS users, "
                "(SELECT COUNT(*) FROM assets a WHERE a.company_id = c.id) AS assets, "
                "(SELECT COUNT(*) FROM certificates ce WHERE ce.company_id = c.id) AS certs "
                "FROM companies c ORDER BY c.id DESC")
    requests_ = many("SELECT * FROM demo_requests ORDER BY id DESC LIMIT 10")
    return render_template("admin/companies.html", rows=rows, demo_requests=requests_)


@bp.route("/admin/companies/<int:cid>/toggle", methods=["POST"])
@S.roles_required("super_admin")
def company_toggle(cid):
    co = one("SELECT * FROM companies WHERE id = ?", (cid,))
    if not co:
        abort(404)
    new = 0 if co["is_active"] else 1
    run("UPDATE companies SET is_active = ? WHERE id = ?", (new, cid))
    log("company.toggled", "company", cid, "active=%d" % new, company_id=cid)
    flash("%s is now %s." % (co["name"], "active" if new else "switched off"), "ok")
    return redirect(url_for("admin.companies"))


# ------------------------------------------------------------------ audit log
@bp.route("/audit")
@S.roles_required("super_admin", "company_admin")
def audit():
    where, params = "", []
    if g.user["role"] != "super_admin":
        where, params = " AND company_id = ?", [g.user["company_id"]]
    q = arg("q")
    if q:
        where += " AND (actor LIKE ? ESCAPE '\\' OR action LIKE ? ESCAPE '\\' OR detail LIKE ? ESCAPE '\\')"
        params += [like(q)] * 3
    page = paginate("SELECT COUNT(*) FROM audit_log WHERE 1=1" + where,
                    "SELECT * FROM audit_log WHERE 1=1" + where + " ORDER BY id DESC", params, per_page=40)
    return render_template("admin/audit.html", page=page, qs=query_string)
