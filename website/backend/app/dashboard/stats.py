"""Dashboard numbers. One function feeds both the page and the JSON endpoint."""
import re
from datetime import datetime, timedelta, timezone

from flask import g

from ..core.access import asset_scope, cert_scope, job_scope
from ..core.db import many, one


def parse_filters(args):
    days = args.get("days", "30")
    days = days if days in ("7", "30", "90", "365", "all") else "30"
    tech = args.get("tech", "")
    cust = args.get("customer", "")
    comp = args.get("company", "")
    return {"days": days, "tech": int(tech) if tech.isdigit() else None,
            "customer": int(cust) if cust.isdigit() and g.user["role"] != "customer" else None,
            "company": int(comp) if comp.isdigit() and g.user["role"] == "super_admin" else None}


def _asset_where(f, with_dates=True):
    sql, params = asset_scope("a")
    if f["tech"]:
        sql += " AND a.technician_id = ?"
        params.append(f["tech"])
    if f["customer"]:
        sql += " AND a.customer_id = ?"
        params.append(f["customer"])
    if f["company"]:
        sql += " AND a.company_id = ?"
        params.append(f["company"])
    if with_dates and f["days"] != "all":
        since = (datetime.now(timezone.utc) - timedelta(days=int(f["days"]))).strftime("%Y-%m-%d")
        sql += " AND substr(a.created_at, 1, 10) >= ?"
        params.append(since)
    return sql, params


def compute(f):
    sql, params = _asset_where(f)
    c = lambda extra="": one("SELECT COUNT(*) FROM assets a WHERE 1=1" + sql + extra, params)[0]
    latest_ok = (" AND EXISTS (SELECT 1 FROM erasures e WHERE e.asset_id = a.id AND e.report_id = "
                 "(SELECT MAX(id) FROM engine_reports r WHERE r.asset_id = a.id))"
                 " AND NOT EXISTS (SELECT 1 FROM erasures e WHERE e.asset_id = a.id AND e.status <> 'Success' "
                 "AND e.report_id = (SELECT MAX(id) FROM engine_reports r WHERE r.asset_id = a.id))")
    kpi = {
        "total_processed": c(" AND a.status IN ('erased','passed','failed','incomplete')"),
        "registered": c(),
        "erased_ok": c(latest_ok),
        "failed": c(" AND a.status = 'failed'"),
        "awaiting_testing": c(" AND a.status = 'erased'"),
        "passed": c(" AND a.status = 'passed'"),
    }
    cs, cp = cert_scope("c")
    if f["customer"]:
        cs += " AND c.customer_id = ?"
        cp = cp + [f["customer"]]
    if f["company"]:
        cs += " AND c.company_id = ?"
        cp = cp + [f["company"]]
    kpi["certificates"] = one("SELECT COUNT(*) FROM certificates c WHERE c.revoked = 0" + cs, cp)[0]
    js, jp = job_scope("j")
    if f["customer"]:
        js += " AND j.customer_id = ?"
        jp = jp + [f["customer"]]
    if f["company"]:
        js += " AND j.company_id = ?"
        jp = jp + [f["company"]]
    kpi["jobs_completed"] = one("SELECT COUNT(*) FROM jobs j WHERE j.status = 'completed'" + js, jp)[0]
    kpi["jobs_open"] = one("SELECT COUNT(*) FROM jobs j WHERE j.status IN ('open','in_progress')" + js, jp)[0]

    # processing statistics: laptops per day
    span = 30 if f["days"] == "all" else min(int(f["days"]), 90)
    start = datetime.now(timezone.utc).date() - timedelta(days=span - 1)
    sql2, p2 = _asset_where(f, with_dates=False)
    per_day = {r["d"]: r for r in many(
        "SELECT substr(a.created_at, 1, 10) AS d, COUNT(*) AS n, SUM(a.status = 'passed') AS p, "
        "SUM(a.status = 'failed') AS f FROM assets a WHERE substr(a.created_at, 1, 10) >= ?" + sql2 +
        " GROUP BY d", [start.isoformat()] + p2)}
    series = []
    for i in range(span):
        d = (start + timedelta(days=i)).isoformat()
        r = per_day.get(d)
        series.append({"date": d, "total": r["n"] if r else 0, "passed": r["p"] if r else 0,
                       "failed": r["f"] if r else 0})

    statuses = {r["status"]: r["n"] for r in many(
        "SELECT a.status, COUNT(*) AS n FROM assets a WHERE 1=1" + sql + " GROUP BY a.status", params)}
    makers = [{"name": r["m"] or "Unknown", "count": r["n"]} for r in many(
        "SELECT a.manufacturer AS m, COUNT(*) AS n FROM assets a WHERE 1=1" + sql +
        " GROUP BY a.manufacturer ORDER BY n DESC LIMIT 6", params)]

    techs = []
    if g.user["role"] in ("super_admin", "company_admin"):
        techs = [dict(r) for r in many(
            "SELECT CASE WHEN a.technician_name = '' THEN '(unknown)' ELSE a.technician_name END AS name, "
            "COUNT(*) AS n, SUM(a.status = 'passed') AS passed, SUM(a.status = 'failed') AS failed, "
            "MAX(a.updated_at) AS last FROM assets a WHERE 1=1" + sql + " GROUP BY name ORDER BY n DESC LIMIT 8",
            params)]

    recent = []
    if g.user["role"] != "customer":
        rsql, rp = "", []
        if g.user["role"] != "super_admin":
            rsql, rp = " WHERE company_id = ?", [g.user["company_id"]]
        if g.user["role"] == "technician":
            rsql += " AND user_id = ?"
            rp.append(g.user["id"])
        recent = [dict(r) for r in many(
            "SELECT ts, actor, action, detail FROM audit_log" + rsql + " ORDER BY id DESC LIMIT 12", rp)]
    else:
        recent = [dict(ts=r["issued_at"], actor="", action="certificate.issued", detail=r["number"]) for r in many(
            "SELECT c.issued_at, c.number FROM certificates c WHERE 1=1" + cs + " ORDER BY c.id DESC LIMIT 8", cp)]
    return {"kpi": kpi, "series": series, "statuses": statuses, "manufacturers": makers,
            "technicians": techs, "recent": recent, "filters": {k: v for k, v in f.items()}}
