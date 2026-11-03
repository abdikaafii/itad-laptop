"""Integration layer for the existing ITAD laptop engine (laptop_check / laptop_wipe / laptop_test).

The engine on the boot USB stays in full control of the physical laptop. This module only
RECEIVES the signed final report it produces (laptop_window.py v1.11 format), verifies it, and
stores it. The website has no way to start a wipe or to switch off any safety check:

  * Live-USB, OS-disk and removable-disk protection, the serial confirmation and dry-run mode
    all live in laptop_wipe.py / itad_run.py and are untouched.
  * A report is only treated as trustworthy when its SHA-256 fingerprint matches its content
    and its Ed25519 signature validates against a key this company has registered.
"""
import hashlib
import json

from ..core.audit import log
from ..core.db import many, one, run, utcnow

MAX_ITEMS = 200
CANONICAL_TESTS = ["CPU", "RAM", "Drive Health", "Battery", "Charging", "Keyboard", "Touchpad",
                   "Screen", "Webcam", "Microphone", "Speakers", "Wi-Fi", "Ethernet", "USB",
                   "BIOS", "Bluetooth", "Touchscreen"]
BAD_SERIALS = {"", "none", "default string", "to be filled by o.e.m.", "to be filled by o.e.m",
               "system serial number", "not specified", "0", "n/a", "unknown", "o.e.m.", "oem",
               "chassis serial number", "0123456789", "1234567890"}


class IngestError(ValueError):
    pass


# ---------------------------------------------------------------- fingerprint
def report_fingerprint(rep):
    """Exactly the algorithm used by laptop_window.report_fingerprint / verify_report.fingerprint."""
    body = dict((k, v) for k, v in rep.items() if k != "integrity")
    data = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def verify_signature(rep, company_id):
    """Return a dict: status, key_id, public_key_hex, computed, claimed.

    status: valid | invalid | unsigned | unknown_key | tampered
    """
    integ = rep.get("integrity") or {}
    computed = report_fingerprint(rep)
    claimed = str(integ.get("sha256") or "")
    out = {"status": "unsigned", "key_id": str(integ.get("key_id") or ""),
           "public_key_hex": str(integ.get("public_key") or ""),
           "computed": computed, "claimed": claimed}
    if not integ:
        return out
    if claimed != computed:
        out["status"] = "tampered"
        return out
    if not integ.get("signed"):
        return out
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub = bytes.fromhex(out["public_key_hex"])
        if len(pub) != 32:
            raise ValueError("bad key length")
        Ed25519PublicKey.from_public_bytes(pub).verify(
            bytes.fromhex(str(integ.get("signature") or "")), computed.encode("ascii"))
    except InvalidSignature:
        out["status"] = "invalid"
        return out
    except Exception:
        out["status"] = "invalid"
        return out
    out["key_id"] = hashlib.sha256(pub).hexdigest()[:16]
    key = one("SELECT * FROM signing_keys WHERE company_id = ? AND public_key_hex = ? AND is_active = 1",
              (company_id, out["public_key_hex"].lower()))
    out["status"] = "valid" if key else "unknown_key"
    out["is_demo_key"] = bool(key and key["is_demo"])
    return out


# ------------------------------------------------------------------ helpers
def _s(v, limit=300):
    return ("" if v is None else str(v)).strip()[:limit]


def real_serial(serial):
    return _s(serial).lower() not in BAD_SERIALS


def norm_test_status(t):
    notes = [str(n) for n in (t.get("notes") or [])]
    st = str(t.get("status") or "").upper()
    if st == "N/A":
        return "NOT_FITTED"
    if st == "SKIP" or any(n.strip().lower() == "not tested" for n in notes):
        return "NOT_TESTED"
    if st in ("PASS", "FAIL", "WARN"):
        return st
    return "WARN"


def validate_shape(rep):
    if not isinstance(rep, dict):
        raise IngestError("The report must be a JSON object.")
    if not isinstance(rep.get("erasures", []), list) or not isinstance(rep.get("tests", []), list):
        raise IngestError("'erasures' and 'tests' must be lists.")
    if len(rep.get("erasures", [])) > MAX_ITEMS or len(rep.get("tests", [])) > MAX_ITEMS:
        raise IngestError("Report has too many entries.")
    for e in rep.get("erasures", []) + rep.get("tests", []):
        if not isinstance(e, dict):
            raise IngestError("Each erasure and test entry must be an object.")
    if not _s(rep.get("serial")):
        raise IngestError("The report has no serial number.")
    if not _s(rep.get("job_id")):
        raise IngestError("The report has no Job ID.")
    if _s(rep.get("overall")) not in ("PASS", "FAIL", "INCOMPLETE", "WARN"):
        raise IngestError("The report has no valid overall result.")


def cross_check(company_id, rep, asset_tag, existing_asset_id=None):
    """Job ID / Asset ID / serial cross-checks. Returns a list of problems (empty = fine)."""
    problems = []
    serial = _s(rep.get("serial"))
    if not real_serial(serial):
        problems.append("Serial number is missing or generic (%s)." % (serial or "empty"))
    job_ref = _s(rep.get("job_id"))
    if not one("SELECT id FROM jobs WHERE company_id = ? AND job_ref = ?", (company_id, job_ref)):
        problems.append("Job %s does not exist on the platform. Create it, then re-run the checks."
                        % job_ref)
    if asset_tag and asset_tag.upper() != "AUTO":
        clash = one("SELECT id, serial FROM assets WHERE company_id = ? AND asset_tag = ? "
                    "AND lower(serial) <> lower(?) AND id <> ?",
                    (company_id, asset_tag, serial, existing_asset_id or 0))
        if clash:
            problems.append("Asset ID %s is already used by a laptop with a different serial (%s)."
                            % (asset_tag, clash["serial"]))
    return problems


def _match_technician(company_id, name):
    if not name:
        return None
    row = one("SELECT id FROM users WHERE company_id = ? AND role IN ('technician','company_admin') "
              "AND (lower(full_name) = lower(?) OR lower(email) = lower(?)) AND is_active = 1",
              (company_id, name, name))
    return row["id"] if row else None


def derive_asset_status(rep, erasures):
    overall = _s(rep.get("overall"))
    if rep.get("test_mode"):
        return "incomplete"
    if overall == "FAIL":
        return "failed"
    if overall == "PASS":
        return "passed"
    if erasures and all(e["status"] == "Success" for e in erasures):
        return "erased"
    return "incomplete"


# --------------------------------------------------------------------- ingest
def ingest_report(company_id, rep, source="engine", user=None, token_name=""):
    """Verify and store a final engine report. Returns a summary dict (never raises on bad trust,
    only on a report that is not in the expected shape)."""
    validate_shape(rep)
    ver = verify_signature(rep, company_id)
    is_demo = 1 if ver.get("is_demo_key") else 0
    job_ref = _s(rep.get("job_id"))
    serial = _s(rep.get("serial"))
    asset_tag = _s(rep.get("asset_id"))
    if asset_tag.upper() == "AUTO":
        asset_tag = ""
    job = one("SELECT * FROM jobs WHERE company_id = ? AND job_ref = ?", (company_id, job_ref))

    existing = None
    if real_serial(serial):
        existing = one("SELECT * FROM assets WHERE company_id = ? AND lower(serial) = lower(?) "
                       "AND (job_id IS ? OR job_id = ?) ORDER BY id DESC",
                       (company_id, serial, job["id"] if job else None, job["id"] if job else -1))
    problems = cross_check(company_id, rep, asset_tag, existing["id"] if existing else None)

    erasures_in = []
    for e in rep.get("erasures", []):
        erasures_in.append({
            "erasure_ref": _s(e.get("erasure_id"), 40), "device_path": _s(e.get("path"), 60),
            "model": _s(e.get("model"), 120), "serial": _s(e.get("serial"), 80),
            "capacity_gb": e.get("capacity_gb") if isinstance(e.get("capacity_gb"), (int, float)) else None,
            "method": _s(e.get("method"), 160), "level": _s(e.get("level"), 20),
            "standard": _s(e.get("standard"), 200),
            "passes": e.get("passes") if isinstance(e.get("passes"), int) else 0,
            "verified": 1 if e.get("verified") is True else 0,
            "status": _s(e.get("status"), 40) or "Failed",
            "technician": _s(e.get("technician"), 80),
            "started": _s(e.get("started"), 40), "finished": _s(e.get("finished"), 40),
            "evidence": json.dumps({"hidden_areas": e.get("hidden_areas") or {},
                                    "wipe_certificate_sha256": _s(rep.get("certificate"), 80)},
                                   sort_keys=True)[:2000]})

    now = utcnow()
    tech_name = _s(rep.get("technician"), 80)
    fields = {
        "customer_id": job["customer_id"] if job else None,
        "job_id": job["id"] if job else None,
        "asset_tag": asset_tag, "serial": serial,
        "manufacturer": _s(rep.get("make_model"), 160).split(" ", 1)[0],
        "model": (_s(rep.get("make_model"), 160).split(" ", 1) + [""])[1],
        "cpu": _s(rep.get("cpu"), 160), "ram": _s(rep.get("ram"), 300),
        "storage": _s(rep.get("storage"), 400), "bios": _s(rep.get("bios"), 160),
        "os_info": _s(rep.get("os_info") or rep.get("enrolment_os"), 160),
        "boot_mode": _s(rep.get("boot_mode"), 40), "location": _s(rep.get("location"), 120),
        "technician_id": _match_technician(company_id, tech_name), "technician_name": tech_name,
        "status": derive_asset_status(rep, erasures_in),
        "check_result": _s(rep.get("check_result"), 40),
        "absolute_status": _s(rep.get("absolute"), 60), "enrolment_status": _s(rep.get("enrolment"), 60),
        "cosmetic_grade": _s((rep.get("observations") or {}).get("grade"), 20),
        "is_demo": is_demo, "updated_at": now, "processed_at": now,
    }
    if existing:
        asset_id = existing["id"]
        sets = ", ".join("%s = ?" % k for k in fields)
        run("UPDATE assets SET %s WHERE id = ?" % sets, list(fields.values()) + [asset_id])
    else:
        cols = list(fields) + ["company_id", "created_at"]
        vals = list(fields.values()) + [company_id, now]
        asset_id = run("INSERT INTO assets (%s) VALUES (%s)" % (", ".join(cols), ",".join("?" * len(cols))),
                       vals)

    report_id = run(
        "INSERT INTO engine_reports (company_id, asset_id, source, received_at, report_json, "
        "claimed_sha256, computed_sha256, signature_status, key_id, public_key_hex, job_ref, serial, "
        "overall, test_mode, cross_check_notes, is_demo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (company_id, asset_id, source, now, json.dumps(rep, sort_keys=True, default=str),
         ver["claimed"], ver["computed"], ver["status"], ver["key_id"], ver["public_key_hex"].lower(),
         job_ref, serial, _s(rep.get("overall")), 1 if rep.get("test_mode") else 0,
         json.dumps(problems), is_demo))

    for e in erasures_in:
        cols = list(e) + ["asset_id", "report_id"]
        run("INSERT INTO erasures (%s) VALUES (%s)" % (", ".join(cols), ",".join("?" * len(cols))),
            list(e.values()) + [asset_id, report_id])
    for t in rep.get("tests", []):
        run("INSERT INTO test_results (asset_id, report_id, component, status, notes) VALUES (?,?,?,?,?)",
            (asset_id, report_id, _s(t.get("component"), 60) or "Unknown", norm_test_status(t),
             "; ".join(str(n) for n in (t.get("notes") or []))[:500]))

    log("report.ingested", "asset", asset_id,
        "signature=%s overall=%s source=%s%s" % (ver["status"], _s(rep.get("overall")), source,
                                                 (" token=" + token_name) if token_name else ""),
        company_id=company_id, user=user, actor=token_name or "engine")

    summary = {"report_id": report_id, "asset_id": asset_id, "signature_status": ver["status"],
               "cross_check_problems": problems, "asset_status": fields["status"],
               "certificates": []}
    from ..certificates.service import eligible_erasures, issue_certificate
    company = one("SELECT * FROM companies WHERE id = ?", (company_id,))
    elig = eligible_erasures(asset_id)
    summary["certificate_eligible"] = bool(elig["ok"])
    summary["certificate_blockers"] = elig["blockers"]
    if company["auto_issue_certificates"] and elig["ok"]:
        for er in elig["erasures"]:
            summary["certificates"].append(issue_certificate(er["id"], user=None, automatic=True))
    return summary


def recheck_report(report_id, user=None):
    """Re-run signature verification and the Job/Asset cross-checks for a stored report.

    Used after a company registers a station key or creates the missing job. The stored report text is
    never changed."""
    row = one("SELECT * FROM engine_reports WHERE id = ?", (report_id,))
    if not row:
        raise IngestError("Report not found.")
    rep = json.loads(row["report_json"])
    ver = verify_signature(rep, row["company_id"])
    asset = one("SELECT * FROM assets WHERE id = ?", (row["asset_id"],))
    asset_tag = asset["asset_tag"] if asset else ""
    problems = cross_check(row["company_id"], rep, asset_tag, row["asset_id"])
    is_demo = 1 if ver.get("is_demo_key") else 0
    run("UPDATE engine_reports SET signature_status = ?, key_id = ?, cross_check_notes = ?, is_demo = ? "
        "WHERE id = ?", (ver["status"], ver["key_id"], json.dumps(problems), is_demo, report_id))
    job = one("SELECT * FROM jobs WHERE company_id = ? AND job_ref = ?",
              (row["company_id"], _s(rep.get("job_id"))))
    if asset and job and not asset["job_id"]:
        run("UPDATE assets SET job_id = ?, customer_id = ?, is_demo = ? WHERE id = ?",
            (job["id"], job["customer_id"], is_demo, asset["id"]))
    if asset:
        run("UPDATE assets SET is_demo = ? WHERE id = ?", (is_demo, asset["id"]))
    log("report.rechecked", "asset", row["asset_id"], "signature=%s problems=%d" % (ver["status"], len(problems)),
        company_id=row["company_id"], user=user)
    return {"signature_status": ver["status"], "problems": problems}


def display_tests(asset_id):
    """Latest test results for an asset, with the standard component list filled in as NOT_TESTED."""
    last = one("SELECT MAX(report_id) AS r FROM test_results WHERE asset_id = ?", (asset_id,))
    got = {}
    if last and last["r"]:
        for r in many("SELECT * FROM test_results WHERE asset_id = ? AND report_id = ? ORDER BY id",
                      (asset_id, last["r"])):
            got[r["component"]] = {"component": r["component"], "status": r["status"], "notes": r["notes"]}
    rows = list(got.values())
    for comp in CANONICAL_TESTS:
        if comp not in got:
            rows.append({"component": comp, "status": "NOT_TESTED", "notes": ""})
    order = {c: i for i, c in enumerate(CANONICAL_TESTS)}
    rows.sort(key=lambda r: (order.get(r["component"], 99), r["component"]))
    return rows
