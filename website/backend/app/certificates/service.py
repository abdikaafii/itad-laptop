"""Certificate rules: when one may be issued, how it is sealed, and how it is verified.

A certificate is only ever created from a stored erasure record, and only when ALL of these hold
for the laptop's latest engine report:
  * the report's SHA-256 fingerprint and Ed25519 signature validate against a registered key
  * it is not a test-mode run
  * the pre-wipe check said CLEAR (no Absolute / active enrolment, nothing unreadable)
  * the engine reports the wipe as WIPED
  * every drive's erasure status is "Success" with verification evidence
  * the Job ID / Asset ID / serial cross-checks found no problems
"""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone

from flask import current_app

from ..core.audit import log
from ..core.db import many, one, run, utcnow


def _latest_report(asset_id):
    return one("SELECT * FROM engine_reports WHERE asset_id = ? ORDER BY id DESC LIMIT 1", (asset_id,))


def eligible_erasures(asset_id):
    """Return {ok, erasures, blockers, already}. Never raises."""
    blockers = []
    rep = _latest_report(asset_id)
    if not rep:
        return {"ok": False, "erasures": [], "blockers": ["No engine report has been received yet."],
                "already": 0}
    data = json.loads(rep["report_json"])
    sig = rep["signature_status"]
    if sig != "valid":
        blockers.append({
            "valid": "", "unknown_key": "The report is signed, but its key is not registered for this "
            "company. Register the station key under Company > Station keys, then re-verify.",
            "unsigned": "The report is not digitally signed (the boot stick had no signing key).",
            "invalid": "The report's signature is invalid.",
            "tampered": "The report's content does not match its fingerprint: it was changed after "
            "it was made."}[sig])
    if rep["test_mode"]:
        blockers.append("The report is from TEST MODE (check and wipe were skipped).")
    notes = json.loads(rep["cross_check_notes"] or "[]")
    blockers.extend(notes)
    if data.get("check_result") != "CLEAR":
        blockers.append("The pre-wipe check was %s, not CLEAR." % (data.get("check_result") or "missing"))
    if data.get("wipe_state") != "WIPED":
        blockers.append("The engine did not report the wipe as WIPED (state: %s)."
                        % (data.get("wipe_state") or "missing"))
    ers = many("SELECT * FROM erasures WHERE report_id = ? ORDER BY id", (rep["id"],))
    if not ers:
        blockers.append("The report contains no erasure records.")
    for e in ers:
        if e["status"] != "Success" or not e["verified"]:
            blockers.append("Drive %s (%s): %s." % (e["device_path"] or "?", e["model"] or "unknown",
                                                    e["status"] or "no result"))
    todo, already = [], 0
    for e in ers:
        if one("SELECT 1 FROM certificates WHERE erasure_id = ?", (e["id"],)):
            already += 1
        else:
            todo.append(e)
    if ers and not todo and not blockers:
        blockers.append("Every drive in this report already has a certificate.")
    return {"ok": bool(todo) and not blockers, "erasures": todo, "blockers": blockers,
            "already": already}


def _seal(number, content_json):
    sha = hashlib.sha256(content_json.encode("utf-8")).hexdigest()
    mac = hmac.new(current_app.config["SECRET_KEY"].encode("utf-8"),
                   ("%s|%s" % (number, sha)).encode("utf-8"), hashlib.sha256).hexdigest()
    return sha, mac


def issue_certificate(erasure_id, user=None, automatic=False):
    er = one("SELECT * FROM erasures WHERE id = ?", (erasure_id,))
    if not er:
        raise ValueError("Erasure record not found.")
    asset = one("SELECT * FROM assets WHERE id = ?", (er["asset_id"],))
    if user is not None and user["role"] != "super_admin" and user["company_id"] != asset["company_id"]:
        raise PermissionError("Not your company.")
    elig = eligible_erasures(asset["id"])
    if elig["blockers"]:
        raise ValueError("Cannot issue: " + " ".join(elig["blockers"]))
    if erasure_id not in [e["id"] for e in elig["erasures"]]:
        raise ValueError("This erasure is not part of the latest verified report.")

    rep = _latest_report(asset["id"])
    company = one("SELECT * FROM companies WHERE id = ?", (asset["company_id"],))
    customer = (one("SELECT * FROM customers WHERE id = ?", (asset["customer_id"],))
                if asset["customer_id"] else None)
    job = one("SELECT * FROM jobs WHERE id = ?", (asset["job_id"],)) if asset["job_id"] else None
    demo = 1 if rep["is_demo"] else 0

    content = {
        "title": "Certificate of Data Erasure",
        "company": {"name": company["name"], "legal_name": company["legal_name"],
                    "address": company["address"], "email": company["email"],
                    "phone": company["phone"], "website": company["website"]},
        "customer": customer["name"] if customer else "",
        "job_ref": job["job_ref"] if job else rep["job_ref"],
        "asset": {"id": asset["asset_tag"], "manufacturer": asset["manufacturer"],
                  "model": asset["model"], "serial": asset["serial"]},
        "drive": {"path": er["device_path"], "model": er["model"], "serial": er["serial"],
                  "capacity_gb": er["capacity_gb"]},
        "sanitisation": {"method": er["method"], "level": er["level"], "standard": er["standard"],
                         "passes": er["passes"]},
        "erasure": {"id": er["erasure_ref"], "started": er["started"], "finished": er["finished"],
                    "status": er["status"], "verified": bool(er["verified"])},
        "technician": er["technician"] or asset["technician_name"],
        "location": asset["location"],
        "source_report": {"sha256": rep["computed_sha256"], "key_id": rep["key_id"],
                          "signature": rep["signature_status"]},
        "demonstration": bool(demo),
    }
    content_json = json.dumps(content, sort_keys=True, default=str)
    prefix = "DEMO" if demo else "ITAD"
    for _ in range(10):
        number = "%s-%s-%s" % (prefix, datetime.now(timezone.utc).year, secrets.token_hex(6).upper())
        if not one("SELECT 1 FROM certificates WHERE number = ?", (number,)):
            break
    sha, mac = _seal(number, content_json)
    cid = run("INSERT INTO certificates (company_id, asset_id, erasure_id, customer_id, number, issued_at, "
              "issued_by, content_json, content_sha256, mac, is_demo) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (asset["company_id"], asset["id"], erasure_id, asset["customer_id"], number, utcnow(),
               user["id"] if user is not None else None, content_json, sha, mac, demo))
    log("certificate.issued", "certificate", cid,
        "%s for asset %s (%s)" % (number, asset["serial"], "automatic" if automatic else "manual"),
        company_id=asset["company_id"], user=user, actor="" if user is not None else "system")
    return {"id": cid, "number": number}


def verify_certificate(number):
    """Public check. Returns {state, cert, content}. state: valid | revoked | tampered | not_found."""
    cert = one("SELECT * FROM certificates WHERE number = ?", ((number or "").strip().upper(),))
    if not cert:
        return {"state": "not_found", "cert": None, "content": None}
    sha, mac = _seal(cert["number"], cert["content_json"])
    intact = (hmac.compare_digest(sha, cert["content_sha256"])
              and hmac.compare_digest(mac, cert["mac"]))
    content = json.loads(cert["content_json"]) if intact else None
    if not intact:
        state = "tampered"
    elif cert["revoked"]:
        state = "revoked"
    else:
        state = "valid"
    return {"state": state, "cert": cert, "content": content}


def revoke_certificate(cert_id, reason, user):
    cert = one("SELECT * FROM certificates WHERE id = ?", (cert_id,))
    if not cert:
        raise ValueError("Certificate not found.")
    if cert["revoked"]:
        return
    run("UPDATE certificates SET revoked = 1, revoked_reason = ?, revoked_at = ? WHERE id = ?",
        (reason[:300], utcnow(), cert_id))
    log("certificate.revoked", "certificate", cert_id, "%s: %s" % (cert["number"], reason[:200]),
        company_id=cert["company_id"], user=user)
