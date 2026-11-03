"""Reports: pick a type, filter, preview on screen, export PDF or CSV."""
import re

from flask import Blueprint, Response, abort, g, render_template, request

from ..assets.routes import get_asset
from ..core import security as S
from ..core.audit import log
from ..core.db import many, one
from ..core.util import arg
from . import service
from .asset_pdf import render_asset_pdf

bp = Blueprint("reports", __name__)


def _filters():
    f = {k: arg(k, 40) for k in ("from", "to", "customer", "month", "company")}
    if g.user["role"] == "customer":
        f["customer"] = str(g.user["customer_id"])
    return f


def _lists():
    if g.user["role"] == "customer":
        return {"customers": many("SELECT id, name FROM customers WHERE id = ?", (g.user["customer_id"],)),
                "companies": []}
    sql, p = ("", []) if g.user["role"] == "super_admin" else (" WHERE company_id = ?", [g.user["company_id"]])
    return {"customers": many("SELECT id, name FROM customers" + sql + " ORDER BY name", p),
            "companies": many("SELECT id, name FROM companies ORDER BY name") if g.user["role"] == "super_admin" else []}


@bp.route("/reports")
@S.login_required
def index():
    key = arg("type", 40)
    types = service.allowed_types()
    rep = None
    if key:
        rep = service.run_report(key, _filters())
        if rep is None:
            abort(404)
    return render_template("app/reports.html", types=types, key=key, rep=rep, filters=_filters(), lists=_lists())


def _company_for_branding():
    if g.user["role"] == "super_admin":
        return None
    return one("SELECT * FROM companies WHERE id = ?", (g.user["company_id"],))


@bp.route("/reports/<key>.csv")
@S.login_required
def export_csv(key):
    rep = service.run_report(key, _filters())
    if rep is None:
        abort(404)
    log("report.exported", "report", key, "csv")
    return Response(service.to_csv(rep), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="%s.csv"' % key})


@bp.route("/reports/<key>.pdf")
@S.login_required
def export_pdf(key):
    rep = service.run_report(key, _filters())
    if rep is None:
        abort(404)
    from flask import current_app
    from ..core import pdfkit as K
    co = _company_for_branding()
    pdf = service.to_pdf(rep, co["name"] if co else "ITAD Platform",
                         K.logo_path(current_app.config["UPLOAD_DIR"], co))
    log("report.exported", "report", key, "pdf")
    disp = "inline" if request.args.get("inline") else "attachment"
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": '%s; filename="%s.pdf"' % (disp, key)})


@bp.route("/reports/asset/<int:asset_id>.pdf")
@S.login_required
def asset_report(asset_id):
    a = get_asset(asset_id)
    log("report.exported", "asset", asset_id, "laptop report pdf", company_id=a["company_id"])
    disp = "inline" if request.args.get("inline") else "attachment"
    return Response(render_asset_pdf(a), mimetype="application/pdf",
                    headers={"Content-Disposition": '%s; filename="laptop-%s.pdf"' % (disp, _safe_name(a["serial"]))})


@bp.route("/reports/asset/<int:asset_id>/hardware.pdf")
@S.login_required
def asset_hardware_report(asset_id):
    a = get_asset(asset_id)
    log("report.exported", "asset", asset_id, "hardware test pdf", company_id=a["company_id"])
    disp = "inline" if request.args.get("inline") else "attachment"
    return Response(render_asset_pdf(a, hardware_only=True), mimetype="application/pdf",
                    headers={"Content-Disposition": '%s; filename="hardware-%s.pdf"' % (disp, _safe_name(a["serial"]))})


def _safe_name(text):
    """Serial numbers come from hardware: keep only filename-safe characters in the header."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)[:60] or "laptop"
