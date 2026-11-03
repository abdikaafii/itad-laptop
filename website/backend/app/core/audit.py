"""Append-only audit trail. The database itself refuses edits and deletes (see migration 001)."""
from flask import g, has_request_context, request

from .db import run, utcnow


def log(action, entity="", entity_id="", detail="", company_id=None, user=None, actor=""):
    user = user or (g.get("user") if has_request_context() else None)
    if user is not None:
        actor = actor or user["email"]
        if company_id is None:
            company_id = user["company_id"]
        uid = user["id"]
    else:
        uid = None
    ip = request.remote_addr if has_request_context() else ""
    run("INSERT INTO audit_log (ts, company_id, user_id, actor, action, entity, entity_id, detail, ip) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (utcnow(), company_id, uid, actor or "system", action, entity, str(entity_id),
         (detail or "")[:500], ip or ""))
