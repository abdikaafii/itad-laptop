"""Per-laptop PDFs: the full individual laptop report and the hardware testing report."""
import json

from flask import current_app
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Spacer

from ..core import pdfkit as K
from ..core.db import many, one
from ..integrations.engine import display_tests

WIDTH = 182 * mm
SIG_TEXT = {"valid": "Verified: fingerprint and Ed25519 signature check out against a registered station key",
            "unknown_key": "UNVERIFIED: signed, but the key is not registered for this company",
            "unsigned": "UNVERIFIED: the report was not signed",
            "invalid": "UNVERIFIED: the signature is invalid",
            "tampered": "UNVERIFIED: the content was changed after it was made"}


def _section(title, flowable):
    return KeepTogether([K.P(title.upper(), size=8, bold=True, color=K.BLUE), Spacer(1, 1 * mm), flowable,
                         Spacer(1, 4 * mm)])


def _tests_table(asset_id):
    rows = [[t["component"], t["status"], t["notes"]] for t in display_tests(asset_id)]
    return K.data_table(["Component", "Result", "Notes"], rows, [45 * mm, 28 * mm, WIDTH - 73 * mm], status_col=1)


def _banner(asset, rep, data):
    status = asset["status"].upper()
    reasons = (data or {}).get("reasons") or []
    sig = rep["signature_status"] if rep else "none"
    lines = [K.P("Final assessment: %s" % status, size=15, bold=True,
                 color={"PASSED": K.GREEN, "FAILED": K.RED}.get(status, K.AMBER), leading=19)]
    if reasons:
        lines.append(K.P("; ".join(reasons), size=9, color=K.MUTED, leading=12))
    if rep:
        lines.append(K.P(SIG_TEXT.get(sig, sig), size=8.5, bold=sig != "valid",
                         color=K.GREEN if sig == "valid" else K.RED))
    else:
        lines.append(K.P("No engine report has been received for this laptop yet.", size=9, color=K.RED))
    return lines


def render_asset_pdf(asset, hardware_only=False):
    company = one("SELECT * FROM companies WHERE id = ?", (asset["company_id"],))
    job = one("SELECT job_ref FROM jobs WHERE id = ?", (asset["job_id"],)) if asset["job_id"] else None
    cust = one("SELECT name FROM customers WHERE id = ?", (asset["customer_id"],)) if asset["customer_id"] else None
    rep = one("SELECT * FROM engine_reports WHERE asset_id = ? ORDER BY id DESC LIMIT 1", (asset["id"],))
    data = json.loads(rep["report_json"]) if rep else {}
    title = "Hardware testing report" if hardware_only else "Individual laptop report"
    story = [K.P(title, size=20, bold=True, color=K.NAVY, leading=24),
             K.P("%s %s   |   Serial %s" % (asset["manufacturer"], asset["model"], asset["serial"]),
                 size=10, color=K.MUTED), Spacer(1, 3 * mm)] + _banner(asset, rep, data) + [Spacer(1, 4 * mm)]
    ident = [("Customer", cust["name"] if cust else ""), ("Job ID", job["job_ref"] if job else ""),
             ("Asset ID", asset["asset_tag"]), ("Serial number", asset["serial"]),
             ("Manufacturer", asset["manufacturer"]), ("Model", asset["model"]), ("CPU", asset["cpu"]),
             ("RAM", asset["ram"]), ("Storage", asset["storage"]), ("BIOS", asset["bios"]),
             ("Operating system", asset["os_info"]), ("Boot mode", asset["boot_mode"]),
             ("Technician", asset["technician_name"]), ("Location", asset["location"]),
             ("Processed", "%s UTC" % asset["processed_at"] if asset["processed_at"] else "")]
    story.append(_section("Device", K.kv_table(ident, WIDTH)))
    if not hardware_only:
        story.append(_section("Initial inspection", K.kv_table([
            ("Pre-wipe check", asset["check_result"] or "not recorded"),
            ("Absolute", asset["absolute_status"]), ("Enrolment (Autopilot / MDM)", asset["enrolment_status"]),
            ("Cosmetic grade", asset["cosmetic_grade"]),
            ("Technician notes", (asset["inspection_notes"] or "").replace("[x] ", "").replace("\n", "; "))], WIDTH)))
        ers = many("SELECT * FROM erasures WHERE report_id = ? ORDER BY id", (rep["id"],)) if rep else []
        rows = [[e["device_path"], e["model"], e["capacity_gb"], e["method"], e["status"],
                 "Yes" if e["verified"] else "No", e["finished"]] for e in ers]
        story.append(_section("Data erasure", K.data_table(
            ["Drive", "Model", "GB", "Method", "Result", "Verified", "Finished"],
            rows or [["No erasure records", "", "", "", "", "", ""]],
            [26 * mm, 30 * mm, 14 * mm, 46 * mm, 20 * mm, 18 * mm, 22 * mm])))
    story.append(_section("Hardware tests", _tests_table(asset["id"])))
    if rep:
        story.append(_section("Evidence", K.kv_table([
            ("Report received", "%s UTC (%s)" % (rep["received_at"], rep["source"])),
            ("Report fingerprint (SHA-256)", rep["computed_sha256"]),
            ("Signing key ID", rep["key_id"] or "none")], WIDTH)))
    wm = "DEMONSTRATION" if asset["is_demo"] else ""
    return K.build(story, title, company["name"], logo=K.logo_path(current_app.config["UPLOAD_DIR"], company),
                   watermark=wm, footer_note="Serial %s  |  generated from stored, verified engine reports"
                   % asset["serial"])
