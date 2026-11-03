"""Business rules: assessment, engine report ingest, certificates, statistics.

The rules that keep the platform honest live here:
  * erasure results only come from an engine report (or are clearly demo data)
  * the overall result is CALCULATED from stored records, never typed in
  * a certificate needs a verified erasure, from an intact (and, by default,
    signed by a trusted key) engine report
"""
import json
import re
import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config
from .db import get_db, utcnow
from .integrity import signer, verify_engine_report
from .security import HTTPError, audit

DUBLIN = ZoneInfo(config.TIMEZONE)

# Same component list as the engine (laptop_window.py)
COMPONENTS = ["CPU", "RAM", "Drive Health", "Battery", "Wi-Fi", "Ethernet",
              "Bluetooth", "Webcam", "USB", "Screen", "Keyboard", "Touchpad",
              "Touchscreen", "Speakers", "Microphone"]
TEST_STATUSES = ("PASS", "FAIL", "WARN", "NOT TESTED", "N/A")

# Mirrors METHOD_TEXT in the engine: (text, NIST level, passes)
METHOD_TEXT = {
    "purge": ("Purge - drive firmware sanitize / secure erase", "Purge", 1),
    "purge-format": ("Purge - NVMe secure format", "Purge", 1),
    "crypto": ("Purge - cryptographic erase", "Purge", 1),
    "clear": ("Clear - overwrite", "Clear", 1),
    "random": ("Clear - random overwrite", "Clear", 1),
    "clear-fallback": ("Clear - random overwrite (purge not supported)", "Clear", 1),
    "dod": ("Clear - 3-pass overwrite (DoD 5220.22-M)", "Clear", 3),
    "discard": ("Block discard (TRIM)", "", 1),
}


def dublin(iso):
    """'2026-11-02T10:17:32Z' -> '2026-11-02 10:17:32' in Europe/Dublin."""
    if not iso:
        return ""
    try:
        d = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return d.astimezone(DUBLIN).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return iso


def jloads(s, default=None):
    try:
        return json.loads(s) if s else default
    except ValueError:
        return default


# ------------------------------------------------------------------ assessment
def compute_assessment(asset_id):
    """Overall result from the stored records. Returns (overall, reasons)."""
    db = get_db()
    reasons = []
    insp = db.one("SELECT * FROM inspections WHERE asset_id = ? ORDER BY id DESC LIMIT 1", (asset_id,))
    if insp and insp["check_result"] == "BLOCKED":
        return "BLOCKED", ["Pre-wipe check is BLOCKED (lock found). Do not wipe or ship."]

    er = db.query("SELECT * FROM erasures WHERE asset_id = ? ORDER BY id", (asset_id,))
    tests = {t["component"]: t for t in db.query("SELECT * FROM test_results WHERE asset_id = ?", (asset_id,))}

    failed = False
    if any(e["status"] == "Failed" for e in er):
        failed = True
        reasons.append("Wipe failed")
    for comp, t in tests.items():
        if t["status"] == "FAIL":
            failed = True
            reasons.append("%s failed" % comp)
    if failed:
        return "FAIL", reasons

    if not er:
        reasons.append("Wipe not done")
    else:
        for e in er:
            if not e["verified"] or e["status"] != "Success":
                reasons.append("Erasure of %s is not verified" % (e["device_path"] or e["serial"] or "drive"))
    for comp in COMPONENTS:
        t = tests.get(comp)
        if t is None or t["status"] == "NOT TESTED":
            reasons.append("%s not tested" % comp)
        elif t["status"] == "WARN":
            reasons.append("%s needs checking%s" % (comp, (": " + t["notes"][:70]) if t["notes"] else ""))
    if reasons:
        return "INCOMPLETE", reasons
    return "PASS", []


def refresh_asset(asset_id, user=None):
    """Recalculate and store the assessment, update the asset status."""
    db = get_db()
    overall, reasons = compute_assessment(asset_id)
    last = db.one("SELECT overall, reasons FROM assessments WHERE asset_id = ? ORDER BY id DESC LIMIT 1", (asset_id,))
    if not last or last["overall"] != overall or jloads(last["reasons"], []) != reasons:
        db.insert("assessments", {"asset_id": asset_id, "overall": overall,
                                  "reasons": json.dumps(reasons),
                                  "computed_by": (user or {}).get("id"), "computed_at": utcnow()})
    has_er = db.one("SELECT 1 AS x FROM erasures WHERE asset_id = ? AND verified = 1 LIMIT 1", (asset_id,))
    has_tests = db.one("SELECT 1 AS x FROM test_results WHERE asset_id = ? LIMIT 1", (asset_id,))
    has_insp = db.one("SELECT 1 AS x FROM inspections WHERE asset_id = ? LIMIT 1", (asset_id,))
    if overall == "BLOCKED":
        status = "blocked"
    elif overall == "FAIL":
        status = "failed"
    elif overall == "PASS":
        status = "completed"
    elif has_tests and has_er:
        status = "assessed"
    elif has_tests:
        status = "tested"
    elif has_er:
        status = "erased"
    elif has_insp:
        status = "inspected"
    else:
        status = "registered"
    db.update("assets", asset_id, {"status": status, "updated_at": utcnow()})
    return overall, reasons


# ---------------------------------------------------------------- engine ingest
_STATUS_MAP = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN", "N/A": "N/A",
               "SKIP": "NOT TESTED", "NOT TESTED": "NOT TESTED"}


def _norm_status(s):
    return _STATUS_MAP.get(str(s or "").upper(), "WARN")


def _erasure_from_device(d, tech, started, finished, job):
    ach = d.get("achieved") or "fail"
    text, level, passes = METHOD_TEXT.get(ach, (ach, "", 1) if ach != "fail" else ("-", "", 0))
    ver = (d.get("verification") or {}).get("verified")
    ok = ach != "fail"
    status = "Success" if ok and ver else "Erased - not verified" if ok else "Failed"
    return {"path": d.get("path"), "model": d.get("model") or "", "capacity_gb": d.get("size_gb"),
            "serial": d.get("serial") or "", "passes": passes, "method": text, "level": level,
            "standard": ("NIST SP 800-88 Rev. 2 %s | IEEE 2883-2022 %s" % (level, level)) if level and ok else "",
            "verified": bool(ver), "status": status, "technician": tech,
            "started": started, "finished": finished,
            "verification": d.get("verification") or {}, "hidden_areas": d.get("hidden_areas") or {}}


def normalise_report(rep):
    """Accept (a) the merged report written by laptop_window.py or
    (b) a bundle {"check": ..., "wipe": ..., "test": ...} of the three script outputs.
    Returns a neutral dict."""
    if "tests" in rep or "wipe_devices" in rep or rep.get("tool") == "laptop_window.py":
        tests = rep.get("tests") or []
        erasures = []
        for e in rep.get("erasures") or []:
            erasures.append({"path": e.get("path"), "model": e.get("model"), "capacity_gb": e.get("capacity_gb"),
                             "serial": e.get("serial"), "passes": e.get("passes"), "method": e.get("method"),
                             "level": e.get("level"), "standard": e.get("standard"),
                             "verified": bool(e.get("verified")), "status": e.get("status") or "Failed",
                             "technician": e.get("technician") or rep.get("technician"),
                             "started": e.get("started"), "finished": e.get("finished"),
                             "verification": {"read_back": "from engine report", "erasure_id": e.get("erasure_id")},
                             "hidden_areas": e.get("hidden_areas") or {}})
        if not erasures:
            erasures = [_erasure_from_device(d, rep.get("technician"), rep.get("wipe_started"),
                                             rep.get("wipe_finished"), rep.get("job_id"))
                        for d in rep.get("wipe_devices") or []]
        return {"identity": {
                    "make_model": rep.get("make_model"), "cpu": rep.get("cpu"), "ram": rep.get("ram"),
                    "storage": rep.get("storage"), "bios": rep.get("bios"), "chassis": rep.get("chassis"),
                    "serial": rep.get("serial")},
                "job_id": rep.get("job_id"), "asset_id": rep.get("asset_id"), "serial": rep.get("serial"),
                "technician": rep.get("technician"), "tests": tests, "erasures": erasures,
                "check_result": rep.get("check_result"), "absolute": rep.get("absolute"),
                "enrolment": rep.get("enrolment"), "observations": rep.get("observations") or {},
                "demo": bool(rep.get("test_mode") or rep.get("demo"))}
    if "check" in rep or "wipe" in rep or "test" in rep:
        chk, wipe, test = rep.get("check") or {}, rep.get("wipe") or {}, rep.get("test") or {}
        ident = chk.get("identity") or {}
        tech = wipe.get("technician") or chk.get("technician") or test.get("technician")
        return {"identity": {"make_model": ("%s %s" % (ident.get("manufacturer", ""), ident.get("model", ""))).strip(),
                             "cpu": ident.get("cpu"), "ram": ident.get("ram_gb"), "storage": "",
                             "bios": ("%s %s" % (ident.get("bios_vendor", ""), ident.get("bios_version", ""))).strip(),
                             "chassis": ident.get("device_type"), "serial": ident.get("serial")},
                "job_id": rep.get("job_id") or chk.get("job_id") or wipe.get("job_id"),
                "asset_id": rep.get("asset_id") or chk.get("asset_id") or wipe.get("asset_id"),
                "serial": ident.get("serial"), "technician": tech,
                "tests": [{"component": r.get("component"), "status": r.get("status"),
                           "notes": r.get("notes"), "details": r.get("details")} for r in test.get("results") or []],
                "erasures": [] if wipe.get("dry_run") else
                [_erasure_from_device(d, tech, wipe.get("time"), wipe.get("time"), wipe.get("job_id"))
                 for d in wipe.get("devices") or []],
                "check_result": chk.get("result"), "absolute": chk.get("absolute_result"),
                "enrolment": chk.get("autopilot_result"), "observations": {}, "demo": False}
    raise HTTPError(422, "Unrecognised report format (expected the laptop_window.py report or a check/wipe/test bundle).")


def _sn_match(a, b):
    return (a or "").strip().lower() == (b or "").strip().lower()


def _reject(db, base, reason):
    base.update({"status": "rejected", "reason": reason})
    sid = db.insert("submissions", base)
    return {"ok": False, "submission_id": sid, "status": "rejected", "reason": reason}


def ingest_report(company_id, rep, station=None, user=None, ip=""):
    """Store an engine report and apply it. Never trusts the report's own 'overall'."""
    db = get_db()
    if not isinstance(rep, dict):
        raise HTTPError(422, "Report must be a JSON object.")
    ver = verify_engine_report(rep)
    existing = db.one("SELECT id, status FROM submissions WHERE company_id = ? AND sha256 = ? AND status = 'accepted'",
                      (company_id, ver["sha256"]))
    if existing:
        return {"ok": True, "submission_id": existing["id"], "status": "duplicate",
                "reason": "This exact report was already received."}
    trusted = False
    if ver["signature_ok"]:
        trusted = bool(db.one("SELECT 1 AS x FROM trusted_keys WHERE company_id = ? AND key_id = ? AND active = 1",
                              (company_id, ver["key_id"])))
    base = {"company_id": company_id, "station_id": (station or {}).get("id"),
            "job_ref": str(rep.get("job_id") or ""), "asset_tag": str(rep.get("asset_id") or ""),
            "serial": str(rep.get("serial") or ""), "payload": json.dumps(rep, sort_keys=True, default=str),
            "sha256": ver["sha256"], "fingerprint_ok": int(ver["fingerprint_ok"]), "signed": int(ver["signed"]),
            "signature_ok": int(ver["signature_ok"]), "key_id": ver["key_id"], "key_trusted": int(trusted),
            "demo": 0, "received_at": utcnow()}
    if not ver["fingerprint_ok"]:
        res = _reject(db, base, "Integrity check failed: %s" % ver["note"])
        audit(user, "engine.rejected", "submission", res["submission_id"], {"reason": res["reason"]}, ip, company_id)
        return res
    try:
        n = normalise_report(rep)
    except HTTPError as e:
        return _reject(db, base, e.detail)
    base["demo"] = int(n["demo"])

    job = db.one("SELECT * FROM jobs WHERE company_id = ? AND job_ref = ?", (company_id, n["job_id"] or ""))
    if not job:
        return _reject(db, base, "Job %s does not exist on the platform. Create the job first." % (n["job_id"] or "(none)"))
    tag = str(n["asset_id"] or "").strip()
    if not tag:
        return _reject(db, base, "The report has no asset ID.")
    asset = db.one("SELECT * FROM assets WHERE company_id = ? AND asset_tag = ?", (company_id, tag))
    if asset:
        if asset["job_id"] != job["id"]:
            return _reject(db, base, "Asset %s belongs to a different job." % tag)
        if not _sn_match(asset["serial"], n["serial"]):
            return _reject(db, base, "Serial number mismatch: platform has %s, report says %s." % (asset["serial"], n["serial"]))
    else:
        if not (n["serial"] or "").strip() or n["serial"].upper() == "UNKNOWN":
            return _reject(db, base, "Cannot register the asset: the report has no usable serial number.")
        ident = n["identity"]
        mm = (ident.get("make_model") or "").split(" ", 1)
        aid = db.insert("assets", {"company_id": company_id, "customer_id": job["customer_id"], "job_id": job["id"],
                                   "asset_tag": tag, "serial": n["serial"], "manufacturer": mm[0] if mm else "",
                                   "model": mm[1] if len(mm) > 1 else "", "cpu": ident.get("cpu"),
                                   "ram_gb": None, "storage_summary": ident.get("storage"), "bios": ident.get("bios"),
                                   "chassis": ident.get("chassis"), "technician_id": job["technician_id"],
                                   "status": "registered", "demo": int(n["demo"]),
                                   "created_at": utcnow(), "updated_at": utcnow()})
        asset = db.one("SELECT * FROM assets WHERE id = ?", (aid,))
        audit(user, "asset.auto_registered", "asset", aid, {"tag": tag, "from": "engine report"}, ip, company_id)

    with db.transaction():
        base["asset_id"] = asset["id"]
        base["status"], base["reason"] = "accepted", None
        sid = db.insert("submissions", base)
        db.insert("inspections", {"asset_id": asset["id"], "submission_id": sid,
                                  "check_result": n["check_result"] or "NOT CHECKED", "absolute_result": n["absolute"],
                                  "enrolment_result": n["enrolment"],
                                  "cosmetic_grade": (n["observations"] or {}).get("grade"),
                                  "issues": json.dumps((n["observations"] or {}).get("issues") or []),
                                  "notes": (n["observations"] or {}).get("notes"),
                                  "recorded_by": (user or {}).get("id"), "recorded_at": utcnow()})
        for e in n["erasures"]:
            db.insert("erasures", {
                "asset_id": asset["id"], "submission_id": sid, "device_path": e.get("path"), "model": e.get("model"),
                "serial": e.get("serial"), "capacity_gb": e.get("capacity_gb"), "method": e.get("method"),
                "level": e.get("level"), "standard": e.get("standard"), "passes": e.get("passes"),
                "status": e.get("status"), "verified": int(bool(e.get("verified"))),
                "verification": json.dumps(e.get("verification") or {}), "started": e.get("started"),
                "finished": e.get("finished"), "technician": e.get("technician"),
                "erasure_ref": (e.get("verification") or {}).get("erasure_id"),
                "source": "engine", "demo": int(n["demo"]), "created_at": utcnow()})
        for t in n["tests"]:
            comp = t.get("component")
            if not comp:
                continue
            db.execute("DELETE FROM test_results WHERE asset_id = ? AND component = ?", (asset["id"], comp))
            db.insert("test_results", {
                "asset_id": asset["id"], "submission_id": sid, "component": comp,
                "status": _norm_status(t.get("status")),
                "notes": "; ".join(t.get("notes") or []) if isinstance(t.get("notes"), list) else (t.get("notes") or ""),
                "details": json.dumps(t.get("details") or {}), "tested_by": t.get("by") or n["technician"],
                "tested_at": utcnow()})
    if n["demo"] and not asset["demo"]:
        db.update("assets", asset["id"], {"demo": 1})        # demonstration evidence marks the asset as demo
    overall, reasons = refresh_asset(asset["id"], user)
    audit(user, "engine.accepted", "submission", sid,
          {"asset": tag, "job": n["job_id"], "signed": bool(ver["signed"]), "trusted_key": trusted,
           "overall": overall}, ip, company_id)
    return {"ok": True, "submission_id": sid, "status": "accepted", "asset_id": asset["id"],
            "overall": overall, "reasons": reasons, "signed": bool(ver["signed"]),
            "signature_valid": bool(ver["signature_ok"]), "key_trusted": trusted}


# ----------------------------------------------------------------- certificates
def _next_number(db, company):
    prefix = re.sub(r"[^A-Z0-9]", "", company["cert_prefix"].upper()) or "ITAD"
    year = datetime.now(DUBLIN).year
    like = "%s-%d-%%" % (prefix, year)
    n = db.one("SELECT COUNT(*) AS n FROM certificates WHERE company_id = ? AND number LIKE ?",
               (company["id"], like))["n"] + 1
    while True:
        num = "%s-%d-%06d-%s" % (prefix, year, n, secrets.token_hex(2).upper())
        if not db.one("SELECT 1 AS x FROM certificates WHERE number = ?", (num,)):
            return num
        n += 1


def certificate_blockers(erasure, asset, company):
    """Why a certificate cannot be issued for this erasure (empty list = can issue)."""
    db = get_db()
    why = []
    if erasure["status"] != "Success" or not erasure["verified"]:
        why.append("The erasure is not verified (status: %s)." % erasure["status"])
    if erasure["source"] == "manual":
        why.append("Manually typed erasure records cannot be certified.")
    if db.one("SELECT 1 AS x FROM certificates WHERE erasure_id = ?", (erasure["id"],)):
        why.append("A certificate was already issued for this erasure.")
    if erasure["source"] == "engine" and not erasure["demo"]:
        sub = db.one("SELECT * FROM submissions WHERE id = ?", (erasure["submission_id"],)) if erasure["submission_id"] else None
        if not sub or sub["status"] != "accepted" or not sub["fingerprint_ok"]:
            why.append("There is no intact engine report behind this erasure.")
        elif company["require_signed"] and not (sub["signature_ok"] and sub["key_trusted"]):
            if not sub["signed"]:
                why.append("The engine report is not signed (no ITAD_DATA stick key).")
            elif not sub["signature_ok"]:
                why.append("The engine report signature is not valid.")
            else:
                why.append("The signing key %s is not trusted yet. A company administrator must trust it." % sub["key_id"])
    return why


def issue_certificate(user, erasure_id, ip=""):
    db = get_db()
    er = db.one("SELECT * FROM erasures WHERE id = ?", (erasure_id,))
    if not er:
        raise HTTPError(404, "Erasure record not found.")
    asset = db.one("SELECT * FROM assets WHERE id = ?", (er["asset_id"],))
    if user["role"] != "super_admin" and asset["company_id"] != user["company_id"]:
        raise HTTPError(404, "Erasure record not found.")
    company = db.one("SELECT * FROM companies WHERE id = ?", (asset["company_id"],))
    why = certificate_blockers(er, asset, company)
    if why:
        raise HTTPError(409, " ".join(why))
    customer = db.one("SELECT * FROM customers WHERE id = ?", (asset["customer_id"],)) if asset["customer_id"] else None
    job = db.one("SELECT * FROM jobs WHERE id = ?", (asset["job_id"],)) if asset["job_id"] else None
    sub = db.one("SELECT sha256, key_id, signed, signature_ok, key_trusted FROM submissions WHERE id = ?",
                 (er["submission_id"],)) if er["submission_id"] else None
    demo = bool(er["demo"])
    with db.transaction():
        number = _next_number(db, company)
        payload = {
            "number": number, "demo": demo,
            "company": {"name": company["name"], "address": company["address"], "email": company["email"]},
            "customer": customer["name"] if customer else "",
            "job_ref": job["job_ref"] if job else "",
            "asset": {"tag": asset["asset_tag"], "serial": asset["serial"], "manufacturer": asset["manufacturer"],
                      "model": asset["model"]},
            "storage": {"device": er["device_path"], "model": er["model"], "serial": er["serial"],
                        "capacity_gb": er["capacity_gb"]},
            "erasure": {"method": er["method"], "level": er["level"], "standard": er["standard"], "passes": er["passes"],
                        "started": er["started"], "finished": er["finished"], "status": er["status"],
                        "verified": bool(er["verified"]), "verification": jloads(er["verification"], {}),
                        "engine_ref": er["erasure_ref"]},
            "technician": er["technician"] or user["name"],
            "source_report": ({"sha256": sub["sha256"], "signed": bool(sub["signature_ok"]),
                               "signing_key": sub["key_id"], "key_trusted": bool(sub["key_trusted"])} if sub else None),
            "issued_at_dublin": dublin(utcnow()),
        }
        pj, sha, sig, kid = signer.sign(payload)
        cid = db.insert("certificates", {
            "company_id": company["id"], "number": number, "asset_id": asset["id"], "erasure_id": er["id"],
            "customer_id": asset["customer_id"], "payload": pj, "payload_sha256": sha, "signature": sig, "key_id": kid,
            "demo": int(demo), "issued_by": user["id"], "issued_at": utcnow()})
    audit(user, "certificate.issued", "certificate", cid, {"number": number, "asset": asset["asset_tag"], "demo": demo},
          ip, company["id"])
    return db.one("SELECT * FROM certificates WHERE id = ?", (cid,))


def certificate_status(cert):
    """Re-check a stored certificate: untouched, signed by this platform, not revoked."""
    db = get_db()
    intact = signer.verify(cert["payload"], cert["payload_sha256"], cert["signature"], cert["key_id"])
    rev = db.one("SELECT * FROM certificate_revocations WHERE certificate_id = ?", (cert["id"],))
    if rev:
        state = "REVOKED"
    elif not intact:
        state = "INVALID"
    elif cert["demo"]:
        state = "DEMONSTRATION"
    else:
        state = "VALID"
    return {"state": state, "intact": intact, "revoked": bool(rev),
            "revoked_reason": rev["reason"] if rev else None,
            "revoked_at": dublin(rev["revoked_at"]) if rev else None}


def revoke_certificate(user, cert_id, reason, ip=""):
    db = get_db()
    cert = db.one("SELECT * FROM certificates WHERE id = ?", (cert_id,))
    if not cert or (user["role"] != "super_admin" and cert["company_id"] != user["company_id"]):
        raise HTTPError(404, "Certificate not found.")
    if not (reason or "").strip():
        raise HTTPError(422, "A reason is required.")
    if db.one("SELECT 1 AS x FROM certificate_revocations WHERE certificate_id = ?", (cert_id,)):
        raise HTTPError(409, "Already revoked.")
    db.insert("certificate_revocations", {"certificate_id": cert_id, "reason": reason.strip(),
                                          "revoked_by": user["id"], "revoked_at": utcnow()})
    audit(user, "certificate.revoked", "certificate", cert_id, {"number": cert["number"], "reason": reason}, ip,
          cert["company_id"])


# -------------------------------------------------------------------- statistics
def dashboard_stats(user, scope_sql, scope_params):
    """scope_sql / params come from security.tenant_filter(user, 'a') for assets."""
    db = get_db()
    q = lambda sql, p=(): db.one(sql, p)["n"]
    total = q("SELECT COUNT(*) AS n FROM assets a WHERE %s" % scope_sql, scope_params)
    by_status = {r["status"]: r["n"] for r in db.query(
        "SELECT status, COUNT(*) AS n FROM assets a WHERE %s GROUP BY status" % scope_sql, scope_params)}
    erased = q("SELECT COUNT(DISTINCT e.asset_id) AS n FROM erasures e JOIN assets a ON a.id = e.asset_id "
               "WHERE e.verified = 1 AND e.status = 'Success' AND %s" % scope_sql, scope_params)
    jt = scope_sql.replace("a.", "j.")
    jobs_done = q("SELECT COUNT(*) AS n FROM jobs j WHERE j.status = 'completed' AND %s" % jt, scope_params)
    jobs_open = q("SELECT COUNT(*) AS n FROM jobs j WHERE j.status IN ('open','in_progress') AND %s" % jt, scope_params)
    certs = q("SELECT COUNT(*) AS n FROM certificates a WHERE %s" % scope_sql, scope_params)
    awaiting = total - sum(by_status.get(k, 0) for k in ("completed", "failed", "blocked", "assessed"))
    per_day = db.query("SELECT SUBSTR(a.created_at,1,10) AS day, COUNT(*) AS n FROM assets a WHERE %s "
                       "GROUP BY SUBSTR(a.created_at,1,10) ORDER BY day DESC LIMIT 30" % scope_sql, scope_params)
    per_day.reverse()
    tech = db.query("SELECT u.name AS name, COUNT(a.id) AS n FROM assets a JOIN users u ON u.id = a.technician_id "
                    "WHERE %s GROUP BY u.name ORDER BY n DESC LIMIT 10" % scope_sql, scope_params)
    recent = db.query("SELECT l.created_at, l.action, l.entity, l.entity_id, l.detail, u.name AS user_name "
                      "FROM audit_log l LEFT JOIN users u ON u.id = l.user_id WHERE %s "
                      "ORDER BY l.id DESC LIMIT 12" % (
                          "1 = 1" if user["role"] == "super_admin" else "l.company_id = ?"),
                      [] if user["role"] == "super_admin" else [user["company_id"]]) if user["role"] != "customer" else []
    for r in recent:
        r["created_at"] = dublin(r["created_at"])
        r["detail"] = jloads(r["detail"], {})
    return {"total_assets": total, "erased_assets": erased, "failed_assets": by_status.get("failed", 0) + by_status.get("blocked", 0),
            "awaiting_testing": awaiting, "certificates": certs, "jobs_completed": jobs_done, "jobs_open": jobs_open,
            "by_status": by_status, "assets_per_day": per_day, "technician_activity": tech, "recent_activity": recent}
