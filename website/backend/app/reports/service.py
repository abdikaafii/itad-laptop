"""Report definitions. Each returns the same shape so CSV, PDF and the on-screen preview share one source:

    {"title", "subtitle", "summary": [(label, value)], "columns": [...], "rows": [[...]]}
"""
import calendar
import csv
import io
import re

from flask import g

from ..core.access import asset_scope, cert_scope, job_scope
from ..core.db import many, one

REPORT_TYPES = [
    # key, label, description, who may run it
    ("monthly", "Monthly processing report", "Laptops processed per day in a month, with results.",
     ("super_admin", "company_admin")),
    ("customer_assets", "Customer asset report", "Every laptop for one customer with its status and certificate.",
     ("super_admin", "company_admin", "technician", "customer")),
    ("job_completion", "Job completion report", "Each job with how many laptops are done and the results.",
     ("super_admin", "company_admin", "technician", "customer")),
    ("failed_devices", "Failed device report", "Laptops that failed erasure or a hardware test, with the reason.",
     ("super_admin", "company_admin", "technician")),
    ("technician_performance", "Technician performance report", "Laptops processed and results per technician.",
     ("super_admin", "company_admin")),
    ("erasure_certificates", "Data erasure certificate register", "Every certificate issued, with its status.",
     ("super_admin", "company_admin", "technician", "customer")),
]
TYPE_MAP = {t[0]: t for t in REPORT_TYPES}


def allowed_types():
    return [t for t in REPORT_TYPES if g.user["role"] in t[3]]


def _dates(f, col):
    sql, params = "", []
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.get("from", "")):
        sql += " AND substr(%s, 1, 10) >= ?" % col
        params.append(f["from"])
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.get("to", "")):
        sql += " AND substr(%s, 1, 10) <= ?" % col
        params.append(f["to"])
    return sql, params


def _company(f, alias):
    if g.user["role"] == "super_admin" and str(f.get("company", "")).isdigit():
        return " AND %s.company_id = ?" % alias, [int(f["company"])]
    return "", []


def run_report(key, f):
    if key not in TYPE_MAP or g.user["role"] not in TYPE_MAP[key][3]:
        return None
    return globals()["_" + key](f)


def _period(f):
    parts = [f.get("from"), f.get("to")]
    if f.get("from") or f.get("to"):
        return "%s to %s" % (f.get("from") or "start", f.get("to") or "today")
    return "All dates"


def _status_counts(rows, field="status"):
    out = {}
    for r in rows:
        out[r[field]] = out.get(r[field], 0) + 1
    return out


def _customer_assets(f):
    sql, params = asset_scope("a")
    csql, cp = _company(f, "a")
    cust = int(f["customer"]) if str(f.get("customer", "")).isdigit() else None
    where = sql + csql + (" AND a.customer_id = ?" if cust else "")
    params = params + cp + ([cust] if cust else [])
    dsql, dp = _dates(f, "a.created_at")
    rows = many("SELECT a.*, c.name AS customer, j.job_ref, "
                "(SELECT group_concat(ce.number, ' ') FROM certificates ce WHERE ce.asset_id = a.id AND ce.revoked = 0) AS certs "
                "FROM assets a LEFT JOIN customers c ON c.id = a.customer_id LEFT JOIN jobs j ON j.id = a.job_id "
                "WHERE 1=1" + where + dsql + " ORDER BY c.name, a.id", params + dp)
    counts = _status_counts(rows)
    name = ""
    if cust:
        r = one("SELECT name FROM customers WHERE id = ?", (cust,))
        name = r["name"] if r else ""
    return {"title": "Customer asset report", "subtitle": "%s  |  %s" % (name or "All customers", _period(f)),
            "summary": [("Laptops", len(rows))] + [(k.title(), v) for k, v in sorted(counts.items())],
            "columns": ["Customer", "Job", "Asset ID", "Serial", "Manufacturer", "Model", "Status", "Certificate"],
            "rows": [[r["customer"], r["job_ref"], r["asset_tag"], r["serial"], r["manufacturer"], r["model"],
                      r["status"], r["certs"]] for r in rows]}


def _job_completion(f):
    sql, params = job_scope("j")
    csql, cp = _company(f, "j")
    dsql, dp = _dates(f, "j.created_at")
    rows = many("SELECT j.*, c.name AS customer, u.full_name AS tech, "
                "(SELECT COUNT(*) FROM assets a WHERE a.job_id = j.id) AS n, "
                "(SELECT COUNT(*) FROM assets a WHERE a.job_id = j.id AND a.status = 'passed') AS p, "
                "(SELECT COUNT(*) FROM assets a WHERE a.job_id = j.id AND a.status = 'failed') AS f, "
                "(SELECT COUNT(*) FROM assets a WHERE a.job_id = j.id AND a.status IN ('registered','inspected','erased','incomplete')) AS o "
                "FROM jobs j LEFT JOIN customers c ON c.id = j.customer_id "
                "LEFT JOIN users u ON u.id = j.assigned_technician_id WHERE 1=1" + sql + csql + dsql +
                " ORDER BY j.id DESC", params + cp + dp)
    return {"title": "Job completion report", "subtitle": _period(f),
            "summary": [("Jobs", len(rows)), ("Completed", sum(1 for r in rows if r["status"] == "completed")),
                        ("Laptops", sum(r["n"] for r in rows))],
            "columns": ["Job", "Customer", "Status", "Technician", "Expected", "Laptops", "Passed", "Failed",
                        "Open", "Created", "Completed"],
            "rows": [[r["job_ref"], r["customer"], r["status"], r["tech"], r["expected_assets"], r["n"], r["p"],
                      r["f"], r["o"], r["created_at"][:10], r["completed_at"][:10]] for r in rows]}


def _failed_devices(f):
    sql, params = asset_scope("a")
    csql, cp = _company(f, "a")
    dsql, dp = _dates(f, "a.processed_at")
    rows = many("SELECT a.*, c.name AS customer, j.job_ref, "
                "(SELECT group_concat(t.component || ': ' || t.notes, '; ') FROM test_results t WHERE t.asset_id = a.id "
                "AND t.status = 'FAIL' AND t.report_id = (SELECT MAX(report_id) FROM test_results WHERE asset_id = a.id)) AS test_fail, "
                "(SELECT group_concat(e.device_path || ' ' || e.status, '; ') FROM erasures e WHERE e.asset_id = a.id "
                "AND e.status <> 'Success' AND e.report_id = (SELECT MAX(id) FROM engine_reports WHERE asset_id = a.id)) AS wipe_fail "
                "FROM assets a LEFT JOIN customers c ON c.id = a.customer_id LEFT JOIN jobs j ON j.id = a.job_id "
                "WHERE a.status = 'failed'" + sql + csql + dsql + " ORDER BY a.processed_at DESC", params + cp + dp)
    return {"title": "Failed device report", "subtitle": _period(f),
            "summary": [("Failed laptops", len(rows))],
            "columns": ["Processed", "Customer", "Job", "Asset ID", "Serial", "Model", "Technician",
                        "Erasure problem", "Hardware failure"],
            "rows": [[r["processed_at"], r["customer"], r["job_ref"], r["asset_tag"], r["serial"],
                      ("%s %s" % (r["manufacturer"], r["model"])).strip(), r["technician_name"],
                      r["wipe_fail"], r["test_fail"]] for r in rows]}


def _technician_performance(f):
    sql, params = asset_scope("a")
    csql, cp = _company(f, "a")
    dsql, dp = _dates(f, "a.processed_at")
    rows = many("SELECT CASE WHEN a.technician_name = '' THEN '(unknown)' ELSE a.technician_name END AS tech, "
                "COUNT(*) AS n, SUM(a.status = 'passed') AS p, SUM(a.status = 'failed') AS f, "
                "SUM(a.status IN ('incomplete','erased')) AS o, "
                "(SELECT COUNT(*) FROM certificates ce WHERE ce.issued_by IN "
                "(SELECT u.id FROM users u WHERE u.full_name = a.technician_name)) AS certs "
                "FROM assets a WHERE a.processed_at <> ''" + sql + csql + dsql +
                " GROUP BY tech ORDER BY n DESC", params + cp + dp)
    return {"title": "Technician performance report", "subtitle": _period(f),
            "summary": [("Technicians", len(rows)), ("Laptops processed", sum(r["n"] for r in rows))],
            "columns": ["Technician", "Laptops processed", "Passed", "Failed", "Incomplete / awaiting tests",
                        "Pass rate", "Certificates issued"],
            "rows": [[r["tech"], r["n"], r["p"], r["f"], r["o"],
                      "%d%%" % round(100 * r["p"] / r["n"]) if r["n"] else "-", r["certs"]] for r in rows]}


def _erasure_certificates(f):
    sql, params = cert_scope("c")
    csql, cp = _company(f, "c")
    dsql, dp = _dates(f, "c.issued_at")
    rows = many("SELECT c.*, a.serial, a.asset_tag, a.manufacturer, a.model, cu.name AS customer, j.job_ref, "
                "e.method, e.finished FROM certificates c JOIN assets a ON a.id = c.asset_id "
                "JOIN erasures e ON e.id = c.erasure_id LEFT JOIN customers cu ON cu.id = c.customer_id "
                "LEFT JOIN jobs j ON j.id = a.job_id WHERE 1=1" + sql + csql + dsql + " ORDER BY c.id DESC",
                params + cp + dp)
    return {"title": "Data erasure certificate register", "subtitle": _period(f),
            "summary": [("Certificates", len(rows)), ("Revoked", sum(1 for r in rows if r["revoked"]))],
            "columns": ["Certificate", "Issued", "Customer", "Job", "Asset ID", "Serial", "Model", "Method", "Status"],
            "rows": [[r["number"], r["issued_at"][:10], r["customer"], r["job_ref"], r["asset_tag"], r["serial"],
                      ("%s %s" % (r["manufacturer"], r["model"])).strip(), r["method"],
                      "REVOKED" if r["revoked"] else ("demonstration" if r["is_demo"] else "valid")]
                     for r in rows]}


def _monthly(f):
    month = f.get("month", "")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        from datetime import datetime, timezone
        month = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m")
    y, m = int(month[:4]), int(month[5:])
    last = calendar.monthrange(y, m)[1]
    sql, params = asset_scope("a")
    csql, cp = _company(f, "a")
    rows = many("SELECT substr(a.processed_at, 1, 10) AS day, COUNT(*) AS n, SUM(a.status = 'passed') AS p, "
                "SUM(a.status = 'failed') AS f, SUM(a.status IN ('incomplete','erased')) AS o "
                "FROM assets a WHERE substr(a.processed_at, 1, 7) = ?" + sql + csql + " GROUP BY day ORDER BY day",
                [month] + params + cp)
    certs = one("SELECT COUNT(*) FROM certificates c WHERE substr(c.issued_at, 1, 7) = ?" + cert_scope("c")[0] +
                _company(f, "c")[0], [month] + cert_scope("c")[1] + _company(f, "c")[1])[0]
    total = sum(r["n"] for r in rows)
    return {"title": "Monthly processing report", "subtitle": "%s %d" % (calendar.month_name[m], y),
            "summary": [("Laptops processed", total), ("Passed", sum(r["p"] for r in rows)),
                        ("Failed", sum(r["f"] for r in rows)), ("Certificates issued", certs),
                        ("Days worked", len(rows)), ("Days in month", last)],
            "columns": ["Date", "Laptops processed", "Passed", "Failed", "Incomplete / awaiting tests"],
            "rows": [[r["day"], r["n"], r["p"], r["f"], r["o"]] for r in rows]}


# ----------------------------------------------------------------- exporters
def _safe_cell(v):
    """Stop spreadsheet programs treating text as a formula (CSV injection)."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def to_csv(rep):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([_safe_cell(rep["title"])])
    w.writerow([_safe_cell(rep["subtitle"])])
    for k, v in rep["summary"]:
        w.writerow([_safe_cell(k), _safe_cell(v)])
    w.writerow([])
    w.writerow([_safe_cell(c) for c in rep["columns"]])
    for r in rep["rows"]:
        w.writerow([_safe_cell(c) for c in r])
    return buf.getvalue()


def to_pdf(rep, company_name, logo):
    from reportlab.lib.units import mm
    from reportlab.platypus import Spacer

    from ..core import pdfkit as K
    story = [K.P(rep["title"], size=18, bold=True, color=K.NAVY, leading=22),
             K.P(rep["subtitle"], size=9.5, color=K.MUTED), Spacer(1, 3 * mm)]
    if rep["summary"]:
        cells = [[K.P(str(v), size=15, bold=True, color=K.BLUE), K.P(k, size=8, color=K.MUTED)]
                 for k, v in rep["summary"]]
        from reportlab.platypus import Table, TableStyle
        t = Table([[c[0] for c in cells], [c[1] for c in cells]])
        t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, 0), 0)]))
        story += [t, Spacer(1, 4 * mm)]
    ncol = len(rep["columns"])
    total_w = 269 * mm
    widths = [total_w / ncol] * ncol
    status_col = next((i for i, c in enumerate(rep["columns"]) if c.lower() in ("status",)), None)
    story.append(K.data_table(rep["columns"], rep["rows"] or [["No records for these filters"] + [""] * (ncol - 1)],
                              widths, status_col=None if status_col is None else status_col, font_size=7.5))
    return K.build(story, rep["title"], company_name, logo=logo, landscape_mode=True,
                   footer_note="Generated by the ITAD Platform. Figures come from stored, verified engine reports.")
