"""Laptops (assets) and jobs."""
import json
import re

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for)

from ..certificates.service import eligible_erasures, issue_certificate
from ..core import security as S
from ..core.access import asset_scope, job_scope
from ..core.audit import log
from ..core.db import many, one, run, utcnow
from ..core.util import arg, form, int_or_none, like, paginate, query_string
from ..integrations.engine import IngestError, display_tests, ingest_report, real_serial, recheck_report

bp = Blueprint("assets", __name__)

ASSET_STATUSES = [("registered", "Registered"), ("inspected", "Inspected"), ("erased", "Erased, awaiting tests"),
                  ("passed", "Passed"), ("failed", "Failed"), ("incomplete", "Incomplete")]
JOB_STATUSES = [("open", "Open"), ("in_progress", "In progress"), ("completed", "Completed"),
                ("cancelled", "Cancelled")]
INSPECTION_CHECKS = ["Powers on", "Screen undamaged", "Keyboard complete", "Chassis undamaged",
                     "Battery fitted", "Charger present", "Hard drive fitted", "No asset tags from other owners"]


def _company_id():
    """The company new records belong to. Super admins pick one with ?company=."""
    if g.user["role"] == "super_admin":
        cid = int_or_none(request.values.get("company"))
        if not cid or not one("SELECT 1 FROM companies WHERE id = ?", (cid,)):
            abort(400, "Choose a company first.")
        return cid
    return g.user["company_id"]


def get_asset(asset_id):
    sql, params = asset_scope("a")
    row = one("SELECT a.* FROM assets a WHERE a.id = ?" + sql, [asset_id] + params)
    if not row:
        abort(404)
    return row


def get_job(job_id):
    sql, params = job_scope("j")
    row = one("SELECT j.* FROM jobs j WHERE j.id = ?" + sql, [job_id] + params)
    if not row:
        abort(404)
    return row


def _staff_write():
    if g.user["role"] == "customer":
        abort(403)


# ------------------------------------------------------------------- assets
@bp.route("/assets")
@S.login_required
def asset_list():
    where, params = asset_scope("a")
    q, status = arg("q"), arg("status")
    customer, job, tech = int_or_none(arg("customer")), int_or_none(arg("job")), int_or_none(arg("tech"))
    d_from, d_to = arg("from", 10), arg("to", 10)
    if q:
        where += (" AND (a.serial LIKE ? ESCAPE '\\' OR a.asset_tag LIKE ? ESCAPE '\\' OR a.model LIKE ? ESCAPE '\\' "
                  "OR a.manufacturer LIKE ? ESCAPE '\\' OR a.technician_name LIKE ? ESCAPE '\\' "
                  "OR j.job_ref LIKE ? ESCAPE '\\' OR c.name LIKE ? ESCAPE '\\')")
        params += [like(q)] * 7
    if status in dict(ASSET_STATUSES):
        where += " AND a.status = ?"
        params.append(status)
    if customer and g.user["role"] != "customer":
        where += " AND a.customer_id = ?"
        params.append(customer)
    if job:
        where += " AND a.job_id = ?"
        params.append(job)
    if tech:
        where += " AND a.technician_id = ?"
        params.append(tech)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d_from):
        where += " AND substr(a.created_at, 1, 10) >= ?"
        params.append(d_from)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d_to):
        where += " AND substr(a.created_at, 1, 10) <= ?"
        params.append(d_to)
    base = ("FROM assets a LEFT JOIN jobs j ON j.id = a.job_id LEFT JOIN customers c ON c.id = a.customer_id "
            "WHERE 1=1" + where)
    page = paginate("SELECT COUNT(*) " + base,
                    "SELECT a.*, j.job_ref, c.name AS customer_name, "
                    "(SELECT COUNT(*) FROM certificates ce WHERE ce.asset_id = a.id AND ce.revoked = 0) AS certs "
                    + base + " ORDER BY a.id DESC", params)
    return render_template("app/assets.html", page=page, statuses=ASSET_STATUSES, filters=_filter_lists(),
                           qs=query_string)


def _filter_lists():
    csql = "SELECT id, name FROM customers WHERE 1=1"
    cp = []
    if g.user["role"] != "super_admin":
        csql += " AND company_id = ?"
        cp.append(g.user["company_id"])
    if g.user["role"] == "customer":
        csql += " AND id = ?"
        cp.append(g.user["customer_id"])
    techs = []
    if g.user["role"] in ("super_admin", "company_admin"):
        tsql = "SELECT id, full_name FROM users WHERE role IN ('technician','company_admin')"
        tp = []
        if g.user["role"] != "super_admin":
            tsql += " AND company_id = ?"
            tp.append(g.user["company_id"])
        techs = many(tsql + " ORDER BY full_name", tp)
    return {"customers": many(csql + " ORDER BY name", cp), "techs": techs}


@bp.route("/assets/new", methods=["GET", "POST"])
@S.login_required
def asset_new():
    _staff_write()
    cid = _company_id()
    jobs = _open_jobs(cid)
    if request.method == "POST":
        f = {k: form(k, 160) for k in ("serial", "asset_tag", "manufacturer", "model", "cpu", "ram",
                                         "storage", "bios", "os_info", "location")}
        job_id = int_or_none(form("job_id"))
        errors = []
        if not real_serial(f["serial"]):
            errors.append("Enter the laptop's real serial number.")
        job = None
        if job_id:
            job = (one("SELECT * FROM jobs WHERE id = ? AND company_id = ?", (job_id, cid))
                   if job_id in {j["id"] for j in jobs} else None)
            if not job:
                errors.append("Choose one of the listed jobs.")
        if not errors and one("SELECT 1 FROM assets WHERE company_id = ? AND lower(serial) = lower(?) "
                              "AND job_id IS ?", (cid, f["serial"], job["id"] if job else None)):
            errors.append("This serial number is already registered in that job.")
        if not errors and f["asset_tag"] and one("SELECT 1 FROM assets WHERE company_id = ? AND asset_tag = ? "
                                                 "AND lower(serial) <> lower(?)", (cid, f["asset_tag"], f["serial"])):
            errors.append("That Asset ID is already used by a laptop with a different serial number.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("app/asset_new.html", jobs=jobs, form=request.form, company=cid), 400
        now = utcnow()
        aid = run("INSERT INTO assets (company_id, customer_id, job_id, asset_tag, serial, manufacturer, model, cpu, "
                  "ram, storage, bios, os_info, location, technician_id, technician_name, status, created_at, "
                  "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (cid, job["customer_id"] if job else None, job["id"] if job else None, f["asset_tag"], f["serial"],
                   f["manufacturer"], f["model"], f["cpu"], f["ram"], f["storage"], f["bios"], f["os_info"],
                   f["location"], g.user["id"] if g.user["role"] != "super_admin" else None,
                   g.user["full_name"], "registered", now, now))
        log("asset.registered", "asset", aid, "%s %s (%s)" % (f["manufacturer"], f["model"], f["serial"]),
            company_id=cid)
        flash("Laptop registered.", "ok")
        return redirect(url_for("assets.asset_detail", asset_id=aid))
    return render_template("app/asset_new.html", jobs=jobs, form={}, company=cid)


def _open_jobs(cid):
    sql, params = job_scope("j")
    return many("SELECT j.id, j.job_ref, j.title FROM jobs j WHERE j.status IN ('open','in_progress') "
                "AND j.company_id = ?" + sql + " ORDER BY j.id DESC", [cid] + params)


@bp.route("/assets/<int:asset_id>")
@S.login_required
def asset_detail(asset_id):
    a = get_asset(asset_id)
    job = one("SELECT * FROM jobs WHERE id = ?", (a["job_id"],)) if a["job_id"] else None
    customer = one("SELECT * FROM customers WHERE id = ?", (a["customer_id"],)) if a["customer_id"] else None
    reports = many("SELECT id, received_at, source, signature_status, key_id, overall, test_mode, cross_check_notes, "
                   "computed_sha256, is_demo FROM engine_reports WHERE asset_id = ? ORDER BY id DESC", (asset_id,))
    latest = reports[0] if reports else None
    erasures = many("SELECT * FROM erasures WHERE report_id = ? ORDER BY id", (latest["id"],)) if latest else []
    certs = many("SELECT * FROM certificates WHERE asset_id = ? ORDER BY id DESC", (asset_id,))
    elig = eligible_erasures(asset_id)
    timeline = many("SELECT ts, actor, action, detail FROM audit_log WHERE entity = 'asset' AND entity_id = ? "
                    "ORDER BY id DESC LIMIT 30", (str(asset_id),)) if g.user["role"] != "customer" else []
    checks = [c for c in (a["inspection_notes"] or "").split("\n") if c.startswith("[x] ")]
    return render_template("app/asset_detail.html", a=a, job=job, customer=customer, reports=reports,
                           latest=latest, erasures=erasures, tests=display_tests(asset_id), certs=certs, elig=elig,
                           timeline=timeline, checks=INSPECTION_CHECKS, done_checks={c[4:] for c in checks},
                           notes_text="\n".join(l for l in (a["inspection_notes"] or "").split("\n")
                                                if not l.startswith("[x] ")),
                           cross_notes=json.loads(latest["cross_check_notes"]) if latest else [])


@bp.route("/assets/<int:asset_id>/inspection", methods=["POST"])
@S.login_required
def asset_inspection(asset_id):
    _staff_write()
    a = get_asset(asset_id)
    ticked = [c for c in INSPECTION_CHECKS if request.form.get("chk_" + str(INSPECTION_CHECKS.index(c)))]
    notes = form("notes", 1500)
    text = "\n".join(["[x] " + c for c in ticked] + ([notes] if notes else []))
    status = "inspected" if a["status"] == "registered" else a["status"]
    run("UPDATE assets SET inspection_notes = ?, status = ?, updated_at = ? WHERE id = ?",
        (text, status, utcnow(), asset_id))
    log("asset.inspected", "asset", asset_id, "%d checks ticked" % len(ticked), company_id=a["company_id"])
    flash("Initial inspection saved.", "ok")
    return redirect(url_for("assets.asset_detail", asset_id=asset_id))


@bp.route("/assets/<int:asset_id>/report", methods=["POST"])
@S.login_required
def asset_upload_report(asset_id):
    """Technician uploads the signed .json report file from the boot stick."""
    _staff_write()
    a = get_asset(asset_id)
    up = request.files.get("report")
    if not up or not up.filename:
        flash("Choose the report .json file from the ITAD_DATA stick.", "error")
        return redirect(url_for("assets.asset_detail", asset_id=asset_id))
    try:
        rep = json.loads(up.read().decode("utf-8"))
        if str(rep.get("serial", "")).strip().lower() != a["serial"].strip().lower():
            raise IngestError("The report is for serial %s, but this laptop is %s. Nothing was stored."
                              % (rep.get("serial"), a["serial"]))
        res = ingest_report(a["company_id"], rep, source="upload", user=g.user)
    except (IngestError, ValueError, UnicodeDecodeError, AttributeError) as ex:
        flash("Report not accepted: %s" % ex, "error")
        return redirect(url_for("assets.asset_detail", asset_id=asset_id))
    flash("Report stored. Signature: %s." % res["signature_status"].replace("_", " "), "ok")
    return redirect(url_for("assets.asset_detail", asset_id=res["asset_id"]))


@bp.route("/assets/<int:asset_id>/recheck", methods=["POST"])
@S.login_required
def asset_recheck(asset_id):
    _staff_write()
    a = get_asset(asset_id)
    rep = one("SELECT id FROM engine_reports WHERE asset_id = ? ORDER BY id DESC LIMIT 1", (asset_id,))
    if not rep:
        abort(404)
    res = recheck_report(rep["id"], g.user)
    flash("Re-checked. Signature: %s. %d problem(s) found." % (res["signature_status"].replace("_", " "),
                                                              len(res["problems"])), "ok" if not res["problems"] else "error")
    return redirect(url_for("assets.asset_detail", asset_id=a["id"]))


@bp.route("/assets/<int:asset_id>/issue", methods=["POST"])
@S.login_required
def asset_issue(asset_id):
    if g.user["role"] not in ("super_admin", "company_admin", "technician"):
        abort(403)
    a = get_asset(asset_id)
    elig = eligible_erasures(asset_id)
    if not elig["ok"]:
        flash("Certificate not issued: " + " ".join(elig["blockers"]), "error")
        return redirect(url_for("assets.asset_detail", asset_id=asset_id))
    made = []
    try:
        for er in elig["erasures"]:
            made.append(issue_certificate(er["id"], user=g.user)["number"])
    except (ValueError, PermissionError) as ex:
        flash(str(ex), "error")
        return redirect(url_for("assets.asset_detail", asset_id=asset_id))
    flash("Certificate issued: " + ", ".join(made), "ok")
    return redirect(url_for("assets.asset_detail", asset_id=a["id"]))


# --------------------------------------------------------------------- jobs
@bp.route("/jobs")
@S.login_required
def job_list():
    sql, params = job_scope("j")
    q, status = arg("q"), arg("status")
    where = sql
    if q:
        where += " AND (j.job_ref LIKE ? ESCAPE '\\' OR j.title LIKE ? ESCAPE '\\' OR c.name LIKE ? ESCAPE '\\')"
        params += [like(q)] * 3
    if status in dict(JOB_STATUSES):
        where += " AND j.status = ?"
        params.append(status)
    base = ("FROM jobs j LEFT JOIN customers c ON c.id = j.customer_id LEFT JOIN users u ON u.id = "
            "j.assigned_technician_id WHERE 1=1" + where)
    page = paginate("SELECT COUNT(*) " + base,
                    "SELECT j.*, c.name AS customer_name, u.full_name AS tech_name, "
                    "(SELECT COUNT(*) FROM assets a WHERE a.job_id = j.id) AS asset_count, "
                    "(SELECT COUNT(*) FROM assets a WHERE a.job_id = j.id AND a.status IN ('passed','failed','incomplete')) "
                    "AS done_count " + base + " ORDER BY j.id DESC", params)
    return render_template("app/jobs.html", page=page, statuses=JOB_STATUSES, qs=query_string)


@bp.route("/jobs/new", methods=["GET", "POST"])
@S.roles_required("super_admin", "company_admin")
def job_new():
    cid = _company_id()
    customers = many("SELECT id, name FROM customers WHERE company_id = ? ORDER BY name", (cid,))
    techs = many("SELECT id, full_name FROM users WHERE company_id = ? AND role IN ('technician','company_admin') "
                 "AND is_active = 1 ORDER BY full_name", (cid,))
    if request.method == "POST":
        ref, title, notes = form("job_ref", 60), form("title", 160), form("notes", 1500)
        cust, tech = int_or_none(form("customer_id")), int_or_none(form("technician_id"))
        expected = int_or_none(form("expected_assets")) or 0
        errors = []
        if not re.fullmatch(r"[A-Za-z0-9._-]{2,60}", ref):
            errors.append("Job reference: 2 to 60 letters, numbers, dots, dashes or underscores (no spaces).")
        elif one("SELECT 1 FROM jobs WHERE company_id = ? AND job_ref = ?", (cid, ref)):
            errors.append("That job reference already exists.")
        if cust and not one("SELECT 1 FROM customers WHERE id = ? AND company_id = ?", (cust, cid)):
            errors.append("Choose one of the listed customers.")
        if tech and not one("SELECT 1 FROM users WHERE id = ? AND company_id = ?", (tech, cid)):
            errors.append("Choose one of the listed technicians.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("app/job_new.html", customers=customers, techs=techs, form=request.form,
                                   company=cid), 400
        jid = run("INSERT INTO jobs (company_id, customer_id, job_ref, title, status, assigned_technician_id, "
                  "expected_assets, notes, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (cid, cust, ref, title, "open", tech, expected, notes, g.user["id"], utcnow()))
        log("job.created", "job", jid, ref, company_id=cid)
        flash("Job created.", "ok")
        return redirect(url_for("assets.job_detail", job_id=jid))
    return render_template("app/job_new.html", customers=customers, techs=techs, form={}, company=cid)


@bp.route("/jobs/<int:job_id>")
@S.login_required
def job_detail(job_id):
    j = get_job(job_id)
    customer = one("SELECT * FROM customers WHERE id = ?", (j["customer_id"],)) if j["customer_id"] else None
    tech = one("SELECT full_name FROM users WHERE id = ?", (j["assigned_technician_id"],)) \
        if j["assigned_technician_id"] else None
    sql, params = asset_scope("a")
    assets = many("SELECT a.* FROM assets a WHERE a.job_id = ?" + sql + " ORDER BY a.id", [job_id] + params)
    techs = many("SELECT id, full_name FROM users WHERE company_id = ? AND role IN ('technician','company_admin') "
                 "AND is_active = 1 ORDER BY full_name", (j["company_id"],)) \
        if g.user["role"] in ("super_admin", "company_admin") else []
    done = sum(1 for a in assets if a["status"] in ("passed", "failed", "incomplete"))
    return render_template("app/job_detail.html", j=j, customer=customer, tech=tech, assets=assets, techs=techs,
                           done=done, statuses=dict(JOB_STATUSES))


@bp.route("/jobs/<int:job_id>/status", methods=["POST"])
@S.login_required
def job_status(job_id):
    _staff_write()
    j = get_job(job_id)
    new = form("status", 20)
    if new not in dict(JOB_STATUSES):
        abort(400)
    if g.user["role"] == "technician" and new == "cancelled":
        abort(403)
    run("UPDATE jobs SET status = ?, completed_at = ? WHERE id = ?",
        (new, utcnow() if new == "completed" else "", job_id))
    log("job.status", "job", job_id, "%s -> %s" % (j["status"], new), company_id=j["company_id"])
    flash("Job marked %s." % dict(JOB_STATUSES)[new].lower(), "ok")
    return redirect(url_for("assets.job_detail", job_id=job_id))


@bp.route("/jobs/<int:job_id>/assign", methods=["POST"])
@S.roles_required("super_admin", "company_admin")
def job_assign(job_id):
    j = get_job(job_id)
    tech = int_or_none(form("technician_id"))
    if tech and not one("SELECT 1 FROM users WHERE id = ? AND company_id = ?", (tech, j["company_id"])):
        abort(400)
    run("UPDATE jobs SET assigned_technician_id = ? WHERE id = ?", (tech, job_id))
    log("job.assigned", "job", job_id, "technician %s" % tech, company_id=j["company_id"])
    flash("Technician assignment saved.", "ok")
    return redirect(url_for("assets.job_detail", job_id=job_id))
