"""Report definitions. Each report returns (title, subtitle, columns, rows, summary)
and is rendered as PDF or CSV by pdfgen. Every query is restricted with the same
tenant filter the rest of the API uses, so a user can only report on their own data."""
import json
from collections import OrderedDict

from .db import get_db
from .security import HTTPError, tenant_filter
from .services import dublin, jloads

REPORT_TYPES = OrderedDict([
    ("hardware_testing", {"title": "Hardware testing report", "roles": "staff",
                          "desc": "Every component result for the selected assets."}),
    ("customer_assets", {"title": "Customer asset report", "roles": "all",
                         "desc": "All assets of a customer with status and result."}),
    ("technician_performance", {"title": "Technician performance report", "roles": "admin",
                                "desc": "Assets processed, passed, failed and certificates per technician."}),
    ("job_completion", {"title": "Job completion report", "roles": "all",
                        "desc": "Jobs with the number of assets and how many are finished."}),
    ("failed_devices", {"title": "Failed device report", "roles": "staff",
                        "desc": "Assets that failed or were blocked, with the reasons."}),
    ("monthly_processing", {"title": "Monthly processing report", "roles": "staff",
                            "desc": "Assets processed per day in a month, with totals."}),
])


def _dates(params, col):
    sql, p = "", []
    if params.get("from"):
        sql += " AND %s >= ?" % col
        p.append(str(params["from"])[:10])
    if params.get("to"):
        sql += " AND SUBSTR(%s,1,10) <= ?" % col
        p.append(str(params["to"])[:10])
    return sql, p


def _scope(user, params, alias):
    """Company / customer restriction for tables that have company_id and customer_id."""
    f, p = tenant_filter(user, alias)
    if user["role"] == "super_admin" and params.get("company_id"):
        f, p = "%s.company_id = ?" % alias, [int(params["company_id"])]
    if params.get("customer_id") and user["role"] != "customer":
        f += " AND %s.customer_id = ?" % alias
        p.append(int(params["customer_id"]))
    return f, p


def _asset_scope(user, params):
    f, p = _scope(user, params, "a")
    if params.get("job_id"):
        f += " AND a.job_id = ?"
        p.append(int(params["job_id"]))
    return f, p


def _overall(db, asset_id):
    r = db.one("SELECT overall, reasons FROM assessments WHERE asset_id = ? ORDER BY id DESC LIMIT 1", (asset_id,))
    return (r["overall"], jloads(r["reasons"], [])) if r else ("INCOMPLETE", ["Not assessed yet"])


def build(kind, user, params):
    if kind not in REPORT_TYPES:
        raise HTTPError(422, "Unknown report type.")
    meta = REPORT_TYPES[kind]
    if meta["roles"] == "staff" and user["role"] == "customer":
        raise HTTPError(403, "Customers cannot run this report.")
    if meta["roles"] == "admin" and user["role"] not in ("super_admin", "company_admin"):
        raise HTTPError(403, "Only administrators can run this report.")
    db = get_db()
    title = meta["title"]
    f, p = _asset_scope(user, params)

    if kind == "hardware_testing":
        ds, dp = _dates(params, "t.tested_at")
        rows = db.query("SELECT a.asset_tag, a.serial, a.model, t.component, t.status, t.notes, t.tested_at "
                        "FROM test_results t JOIN assets a ON a.id = t.asset_id WHERE %s%s ORDER BY a.asset_tag, t.component" % (f, ds),
                        p + dp)
        counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("PASS", "FAIL", "WARN", "NOT TESTED", "N/A")}
        return (title, "Component results", ["Asset ID", "Serial", "Model", "Component", "Notes", "Tested (Dublin)", "Result"],
                [[r["asset_tag"], r["serial"], r["model"], r["component"], r["notes"], dublin(r["tested_at"]), r["status"]] for r in rows],
                [("Results", len(rows)), ("Pass", counts["PASS"]), ("Fail", counts["FAIL"]), ("Not tested / warn", counts["NOT TESTED"] + counts["WARN"])])

    if kind == "customer_assets":
        rows = db.query("SELECT a.id, a.asset_tag, a.serial, a.manufacturer, a.model, a.status, a.updated_at, c.name AS customer "
                        "FROM assets a LEFT JOIN customers c ON c.id = a.customer_id WHERE %s ORDER BY a.asset_tag" % f, p)
        out = [[r["asset_tag"], r["serial"], ("%s %s" % (r["manufacturer"] or "", r["model"] or "")).strip(), r["customer"] or "",
                r["status"], dublin(r["updated_at"]), _overall(db, r["id"])[0]] for r in rows]
        return (title, "All assets", ["Asset ID", "Serial", "Device", "Customer", "Status", "Updated (Dublin)", "Result"], out,
                [("Assets", len(out)), ("Passed", sum(1 for r in out if r[-1] == "PASS")), ("Failed / blocked", sum(1 for r in out if r[-1] in ("FAIL", "BLOCKED")))])

    if kind == "technician_performance":
        ds, dp = _dates(params, "a.created_at")
        rows = db.query("SELECT u.name AS tech, COUNT(a.id) AS total, "
                        "SUM(CASE WHEN a.status = 'completed' THEN 1 ELSE 0 END) AS passed, "
                        "SUM(CASE WHEN a.status IN ('failed','blocked') THEN 1 ELSE 0 END) AS failed "
                        "FROM assets a JOIN users u ON u.id = a.technician_id WHERE %s%s GROUP BY u.name ORDER BY total DESC" % (f, ds), p + dp)
        cf, cp = _scope(user, params, "c")
        certs = {r["name"]: r["n"] for r in db.query(
            "SELECT u.name AS name, COUNT(c.id) AS n FROM certificates c JOIN users u ON u.id = c.issued_by WHERE %s GROUP BY u.name" % cf, cp)}
        out = [[r["tech"], r["total"], r["passed"], r["failed"], certs.get(r["tech"], 0),
                "%d%%" % (100 * r["passed"] // r["total"] if r["total"] else 0)] for r in rows]
        return (title, "Per technician", ["Technician", "Assets", "Passed", "Failed / blocked", "Certificates issued", "Pass rate"], out,
                [("Technicians", len(out)), ("Assets", sum(r[1] for r in out))])

    if kind == "job_completion":
        jf, jp = _scope(user, params, "j")
        rows = db.query("SELECT j.id, j.job_ref, j.title, j.status, j.due_date, j.completed_at, c.name AS customer, "
                        "(SELECT COUNT(*) FROM assets x WHERE x.job_id = j.id) AS n_assets, "
                        "(SELECT COUNT(*) FROM assets x WHERE x.job_id = j.id AND x.status = 'completed') AS n_done "
                        "FROM jobs j LEFT JOIN customers c ON c.id = j.customer_id WHERE %s ORDER BY j.id DESC" % jf,
                        jp)
        out = [[r["job_ref"], r["title"], r["customer"] or "", r["n_assets"], r["n_done"], dublin(r["completed_at"]) or "-", r["status"]] for r in rows]
        return (title, "All jobs", ["Job", "Title", "Customer", "Assets", "Assets passed", "Completed (Dublin)", "Status"], out,
                [("Jobs", len(out)), ("Completed", sum(1 for r in out if r[-1] == "completed"))])

    if kind == "failed_devices":
        rows = db.query("SELECT a.id, a.asset_tag, a.serial, a.model, a.status, a.updated_at FROM assets a "
                        "WHERE %s AND a.status IN ('failed','blocked') ORDER BY a.updated_at DESC" % f, p)
        out = []
        for r in rows:
            ov, reasons = _overall(db, r["id"])
            out.append([r["asset_tag"], r["serial"], r["model"], "; ".join(reasons), dublin(r["updated_at"]), ov])
        return (title, "Failed and blocked devices", ["Asset ID", "Serial", "Model", "Reasons", "Updated (Dublin)", "Result"], out,
                [("Devices", len(out))])

    if kind == "monthly_processing":
        month = str(params.get("month") or "")[:7]
        if len(month) != 7:
            raise HTTPError(422, "month is required, for example 2026-11.")
        rows = db.query("SELECT SUBSTR(a.created_at,1,10) AS day, COUNT(*) AS n, "
                        "SUM(CASE WHEN a.status = 'completed' THEN 1 ELSE 0 END) AS passed, "
                        "SUM(CASE WHEN a.status IN ('failed','blocked') THEN 1 ELSE 0 END) AS failed "
                        "FROM assets a WHERE %s AND SUBSTR(a.created_at,1,7) = ? GROUP BY SUBSTR(a.created_at,1,10) ORDER BY day" % f, p + [month])
        out = [[r["day"], r["n"], r["passed"], r["failed"], r["n"] - r["passed"] - r["failed"]] for r in rows]
        return (title, month, ["Day", "Assets registered", "Passed", "Failed / blocked", "In progress"], out,
                [("Month", month), ("Assets", sum(r[1] for r in out)), ("Passed", sum(r[2] for r in out)), ("Failed / blocked", sum(r[3] for r in out))])
