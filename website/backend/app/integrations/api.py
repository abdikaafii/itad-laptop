"""REST API for the processing stations (the existing ITAD engine).

Authentication: `Authorization: Bearer itad_...` (a token created under Company > API tokens).
The API is deliberately one-way for safety. A station can

  * read a job ticket, to cross-check the Job ID / Asset ID it was given
  * send its finished, signed report

There is no endpoint that starts a wipe, changes a test result, or issues a certificate on a
station's say-so. The physical-device safety checks stay on the device, out of the website's reach.
"""
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request

from ..core.db import many, one, run, utcnow
from ..core.security import rate_limit, sha256_hex
from .engine import IngestError, ingest_report

bp = Blueprint("engine_api", __name__, url_prefix="/api/v1/engine")


def token_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        header = request.headers.get("Authorization", "")
        raw = header[7:].strip() if header.lower().startswith("bearer ") else ""
        row = one("SELECT t.*, c.is_active AS company_active, c.name AS company_name FROM api_tokens t "
                  "JOIN companies c ON c.id = t.company_id WHERE t.token_hash = ? AND t.is_active = 1",
                  (sha256_hex(raw),)) if raw else None
        if not row or not row["company_active"]:
            return jsonify(error="invalid or missing API token"), 401
        run("UPDATE api_tokens SET last_used = ? WHERE id = ?", (utcnow(), row["id"]))
        g.api_token = row
        return fn(*a, **kw)
    return wrapper


@bp.route("/ping")
@rate_limit("engine", 300, 60)
@token_required
def ping():
    return jsonify(ok=True, company=g.api_token["company_name"], server_time=utcnow() + " UTC")


@bp.route("/jobs/<job_ref>")
@rate_limit("engine", 300, 60)
@token_required
def job_ticket(job_ref):
    cid = g.api_token["company_id"]
    j = one("SELECT j.*, c.name AS customer_name FROM jobs j LEFT JOIN customers c ON c.id = j.customer_id "
            "WHERE j.company_id = ? AND j.job_ref = ?", (cid, job_ref[:60]))
    if not j:
        return jsonify(error="job not found"), 404
    assets = many("SELECT asset_tag, serial, status FROM assets WHERE job_id = ? ORDER BY id", (j["id"],))
    return jsonify(job_ref=j["job_ref"], title=j["title"], status=j["status"], customer=j["customer_name"] or "",
                   expected_assets=j["expected_assets"], assets=[dict(a) for a in assets])


@bp.route("/reports", methods=["POST"])
@rate_limit("engine", 300, 60)
@token_required
def receive_report():
    rep = request.get_json(silent=True)
    if rep is None:
        return jsonify(error="body must be the report as JSON"), 400
    try:
        res = ingest_report(g.api_token["company_id"], rep, source="engine", token_name=g.api_token["name"])
    except IngestError as ex:
        return jsonify(error=str(ex)), 422
    return jsonify(res), 201
