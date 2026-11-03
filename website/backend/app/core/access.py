"""Who may see what. Every list and detail page goes through these helpers.

  super_admin    everything, across all companies
  company_admin  everything in their own company
  technician     jobs assigned to them and the laptops they registered or that sit in those jobs
  customer       only laptops, jobs and certificates that belong to their own customer record
"""
from flask import abort, g


def asset_scope(alias="a"):
    """SQL fragment (starting with ' AND ') plus params limiting `assets` rows to the current user."""
    u = g.user
    p = alias
    if u["role"] == "super_admin":
        return "", []
    if u["role"] == "company_admin":
        return " AND %s.company_id = ?" % p, [u["company_id"]]
    if u["role"] == "technician":
        return (" AND %s.company_id = ? AND (%s.technician_id = ? OR %s.job_id IN "
                "(SELECT id FROM jobs WHERE assigned_technician_id = ?))" % (p, p, p),
                [u["company_id"], u["id"], u["id"]])
    return (" AND %s.company_id = ? AND %s.customer_id = ?" % (p, p), [u["company_id"], u["customer_id"]])


def job_scope(alias="j"):
    u = g.user
    p = alias
    if u["role"] == "super_admin":
        return "", []
    if u["role"] == "company_admin":
        return " AND %s.company_id = ?" % p, [u["company_id"]]
    if u["role"] == "technician":
        return (" AND %s.company_id = ? AND (%s.assigned_technician_id = ? OR %s.created_by = ?)"
                % (p, p, p), [u["company_id"], u["id"], u["id"]])
    return (" AND %s.company_id = ? AND %s.customer_id = ?" % (p, p), [u["company_id"], u["customer_id"]])


def cert_scope(alias="c"):
    u = g.user
    p = alias
    if u["role"] == "super_admin":
        return "", []
    if u["role"] == "company_admin":
        return " AND %s.company_id = ?" % p, [u["company_id"]]
    if u["role"] == "technician":
        a_sql, a_params = asset_scope("a")
        return (" AND %s.company_id = ? AND %s.asset_id IN (SELECT a.id FROM assets a WHERE 1=1%s)"
                % (p, p, a_sql), [u["company_id"]] + a_params)
    return " AND %s.company_id = ? AND %s.customer_id = ?" % (p, p), [u["company_id"], u["customer_id"]]


def company_filter(alias, requested=None):
    """Super admins may narrow to one company; everyone else is pinned to their own."""
    u = g.user
    if u["role"] == "super_admin":
        if requested:
            return " AND %s.company_id = ?" % alias, [int(requested)]
        return "", []
    return " AND %s.company_id = ?" % alias, [u["company_id"]]


def require_company_access(company_id):
    u = g.user
    if u["role"] == "super_admin":
        return
    if u["company_id"] != company_id:
        abort(404)
