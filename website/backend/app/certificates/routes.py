"""Certificate history, search, PDF download and revocation (signed-in area)."""
import re

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from ..core import security as S
from ..core.access import cert_scope
from ..core.audit import log
from ..core.db import many, one
from ..core.util import arg, form, like, paginate, query_string
from .pdf import render_certificate_pdf
from .service import revoke_certificate, verify_certificate

bp = Blueprint("certificates", __name__)


def get_cert(cert_id):
    sql, params = cert_scope("c")
    row = one("SELECT c.* FROM certificates c WHERE c.id = ?" + sql, [cert_id] + params)
    if not row:
        abort(404)
    return row


@bp.route("/certificates")
@S.login_required
def cert_list():
    sql, params = cert_scope("c")
    q, state = arg("q"), arg("state")
    d_from, d_to = arg("from", 10), arg("to", 10)
    where = sql
    if q:
        where += (" AND (c.number LIKE ? ESCAPE '\\' OR a.serial LIKE ? ESCAPE '\\' OR a.asset_tag LIKE ? ESCAPE '\\' "
                  "OR a.model LIKE ? ESCAPE '\\' OR cu.name LIKE ? ESCAPE '\\' OR j.job_ref LIKE ? ESCAPE '\\')")
        params += [like(q)] * 6
    if state == "valid":
        where += " AND c.revoked = 0"
    elif state == "revoked":
        where += " AND c.revoked = 1"
    elif state == "demo":
        where += " AND c.is_demo = 1"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d_from):
        where += " AND substr(c.issued_at, 1, 10) >= ?"
        params.append(d_from)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d_to):
        where += " AND substr(c.issued_at, 1, 10) <= ?"
        params.append(d_to)
    base = ("FROM certificates c JOIN assets a ON a.id = c.asset_id LEFT JOIN customers cu ON cu.id = c.customer_id "
            "LEFT JOIN jobs j ON j.id = a.job_id WHERE 1=1" + where)
    page = paginate("SELECT COUNT(*) " + base,
                    "SELECT c.*, a.serial, a.asset_tag, a.manufacturer, a.model, cu.name AS customer_name, j.job_ref "
                    + base + " ORDER BY c.id DESC", params)
    return render_template("app/certificates.html", page=page, qs=query_string)


@bp.route("/certificates/<int:cert_id>")
@S.login_required
def cert_detail(cert_id):
    c = get_cert(cert_id)
    v = verify_certificate(c["number"])
    asset = one("SELECT id, serial, asset_tag, manufacturer, model FROM assets WHERE id = ?", (c["asset_id"],))
    issuer = one("SELECT full_name FROM users WHERE id = ?", (c["issued_by"],)) if c["issued_by"] else None
    return render_template("app/certificate_detail.html", c=c, v=v, asset=asset, issuer=issuer)


@bp.route("/certificates/<int:cert_id>/pdf")
@S.login_required
def cert_pdf(cert_id):
    c = get_cert(cert_id)
    v = verify_certificate(c["number"])
    if v["state"] == "tampered":
        log("certificate.tamper_detected", "certificate", cert_id, c["number"], company_id=c["company_id"])
        abort(409, "This certificate failed its integrity check, so no PDF is produced. Tell your administrator.")
    company = one("SELECT * FROM companies WHERE id = ?", (c["company_id"],))
    pdf = render_certificate_pdf(c, company)
    disp = "inline" if request.args.get("inline") else "attachment"
    log("certificate.downloaded", "certificate", cert_id, c["number"], company_id=c["company_id"])
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": '%s; filename="%s.pdf"' % (disp, c["number"])})


@bp.route("/certificates/<int:cert_id>/revoke", methods=["POST"])
@S.roles_required("super_admin", "company_admin")
def cert_revoke(cert_id):
    c = get_cert(cert_id)
    reason = form("reason", 300)
    if len(reason) < 5:
        flash("Give a reason of at least a few words. It is kept in the audit log (it is not shown publicly).",
              "error")
    else:
        revoke_certificate(c["id"], reason, g.user)
        flash("Certificate %s has been revoked." % c["number"], "ok")
    return redirect(url_for("certificates.cert_detail", cert_id=cert_id))
