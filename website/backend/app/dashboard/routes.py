"""Signed-in home page: KPIs, charts, filters, CSV download."""
import csv
import io

from flask import Blueprint, Response, g, jsonify, redirect, render_template, request, url_for

from ..core import security as S
from ..core.db import many
from ..reports.service import _safe_cell
from .stats import compute, parse_filters

bp = Blueprint("dashboard", __name__)


def _lists():
    sql, p = ("", []) if g.user["role"] == "super_admin" else (" WHERE company_id = ?", [g.user["company_id"]])
    out = {"customers": [], "techs": [], "companies": []}
    if g.user["role"] != "customer":
        out["customers"] = many("SELECT id, name FROM customers" + sql + " ORDER BY name", p)
    if g.user["role"] in ("super_admin", "company_admin"):
        out["techs"] = many("SELECT id, full_name FROM users WHERE role IN ('technician','company_admin')"
                            + (" AND company_id = ?" if p else "") + " ORDER BY full_name", p)
    if g.user["role"] == "super_admin":
        out["companies"] = many("SELECT id, name FROM companies ORDER BY name")
    return out


@bp.route("/dashboard")
@S.login_required
def home():
    f = parse_filters(request.args)
    return render_template("app/dashboard.html", data=compute(f), f=f, lists=_lists())


@bp.route("/api/v1/dashboard/stats")
@S.login_required
def stats_json():
    return jsonify(compute(parse_filters(request.args)))


@bp.route("/dashboard.csv")
@S.login_required
def stats_csv():
    data = compute(parse_filters(request.args))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Dashboard summary"])
    for k, v in data["kpi"].items():
        w.writerow([_safe_cell(k.replace("_", " ")), v])
    w.writerow([])
    w.writerow(["Date", "Laptops registered", "Passed", "Failed"])
    for r in data["series"]:
        w.writerow([r["date"], r["total"], r["passed"], r["failed"]])
    if data["technicians"]:
        w.writerow([])
        w.writerow(["Technician", "Laptops", "Passed", "Failed", "Last activity"])
        for t in data["technicians"]:
            w.writerow([_safe_cell(t["name"]), t["n"], t["passed"], t["failed"], t["last"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="dashboard.csv"'})
