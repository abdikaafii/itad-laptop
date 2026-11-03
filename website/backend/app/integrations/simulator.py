"""Development simulator: builds reports in the same format the real engine produces, signed with a
DEMO key. Everything that comes from here is flagged is_demo, stamped DEMONSTRATION on certificates
and can never be mistaken for a real erasure.

Used when real hardware is not available (seed data, tests, UI demos).
"""
import hashlib
import json
import random
from datetime import datetime, timedelta

from .engine import CANONICAL_TESTS, report_fingerprint

MODELS = [("Dell", "Latitude 5420", "Intel Core i5-1135G7", "16 GB DDR4 3200", "Samsung PM991 256 GB NVMe"),
          ("HP", "EliteBook 840 G8", "Intel Core i7-1165G7", "16 GB DDR4 3200", "SK hynix BC711 512 GB NVMe"),
          ("Lenovo", "ThinkPad T14 Gen 2", "AMD Ryzen 5 PRO 5650U", "8 GB DDR4 3200", "WDC SN530 256 GB NVMe"),
          ("Apple", "MacBook Pro 13", "Apple M1", "8 GB unified", "APPLE SSD AP0256Q 256 GB"),
          ("Microsoft", "Surface Laptop 4", "Intel Core i5-1145G7", "8 GB LPDDR4x", "Samsung PM991 256 GB NVMe")]


def new_demo_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.generate()


def public_hex(key):
    from cryptography.hazmat.primitives import serialization
    return key.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw).hex()


def sign_report(rep, key):
    """Same structure as laptop_window.sign_report."""
    fp = report_fingerprint(rep)
    pub = bytes.fromhex(public_hex(key))
    rep["integrity"] = {"algorithm": "SHA-256 + Ed25519", "sha256": fp,
                        "signature": key.sign(fp.encode("ascii")).hex(),
                        "key_id": hashlib.sha256(pub).hexdigest()[:16],
                        "public_key": pub.hex(), "signed": True}
    return rep


def make_report(key, job_ref, asset_tag, serial, technician, scenario="pass", model_index=0,
                location="Warehouse DPA3 Test Bay 1", when=None, company="DEMO"):
    """scenario: pass | wipe_failed | not_verified | incomplete | test_fail | test_mode | unsigned"""
    rng = random.Random(serial)
    make, model, cpu, ram, drive = MODELS[model_index % len(MODELS)]
    t0 = (when or datetime(2026, 10, 28, 9, 0)) + timedelta(minutes=rng.randint(0, 240))
    t1 = t0 + timedelta(minutes=rng.randint(25, 55))
    fmt = "%Y-%m-%d %H:%M:%S"
    ok = scenario not in ("wipe_failed",)
    verified = scenario not in ("wipe_failed", "not_verified")
    status = "Success" if ok and verified else ("Erased - not verified" if ok else "Failed")
    erasure = {
        "index": 0, "path": "/dev/nvme0n1", "model": drive.split(" GB")[0].rsplit(" ", 1)[0], "capacity_gb": int(drive.split(" GB")[0].rsplit(" ", 1)[1]),
        "serial": "DEMO%s" % hashlib.sha256(serial.encode()).hexdigest()[:8].upper(), "passes": 1,
        "method": "Purge - drive firmware sanitize / secure erase" if ok else "-",
        "level": "Purge" if ok else "",
        "standard": "NIST SP 800-88 Rev. 2 Purge  |  IEEE 2883-2022 Purge" if ok else "",
        "hidden_areas": {}, "verified": verified, "status": status, "technician": technician,
        "started": t0.strftime("%H:%M:%S"), "finished": t1.strftime("%H:%M:%S"),
        "erasure_id": str(int(hashlib.sha256(("%s|%s" % (job_ref, serial)).encode()).hexdigest()[:10], 16) % 10 ** 10)}

    tests = []
    for comp in CANONICAL_TESTS:
        st, notes = "PASS", []
        if comp in ("Charging", "BIOS", "Bluetooth", "Touchscreen"):
            continue
        if scenario == "test_fail" and comp == "Battery":
            st, notes = "FAIL", ["Battery health 38% of design capacity"]
        if scenario == "incomplete" and comp in ("Keyboard", "Touchpad", "Screen", "Webcam", "Speakers"):
            st, notes = "WARN", ["Not tested"]
        tests.append({"component": comp, "status": st, "notes": notes, "details": {}})

    if scenario == "test_mode":
        overall, reasons = "INCOMPLETE", ["TEST MODE - check / wipe skipped, NOT for customer or resale use"]
    elif scenario in ("wipe_failed", "test_fail"):
        overall = "FAIL"
        reasons = ["Wipe failed"] if scenario == "wipe_failed" else ["Battery failed"]
    elif scenario == "incomplete":
        overall, reasons = "INCOMPLETE", ["Keyboard not tested", "Touchpad not tested"]
    elif scenario == "not_verified":
        overall, reasons = "INCOMPLETE", []
    else:
        overall, reasons = "PASS", []

    rep = {
        "tool": "laptop_window.py", "version": "1.11.0-DEMO-SIMULATOR",
        "time": t1.strftime(fmt) + " (Europe/Dublin)", "started": t0.strftime(fmt), "finished": t1.strftime(fmt),
        "job_id": job_ref, "asset_id": asset_tag, "serial": serial, "technician": technician,
        "location": location, "make_model": "%s %s" % (make, model), "cpu": cpu, "ram": ram,
        "storage": "%s (SSD) %s GB" % (erasure["model"], erasure["capacity_gb"]),
        "bios": "%s %s" % (make, "1.%d.0" % rng.randint(4, 21)), "boot_mode": "UEFI",
        "company": company, "erasures": [] if scenario == "test_mode" else [erasure],
        "absolute": "NOT CHECKED (TEST MODE)" if scenario == "test_mode" else "CLEAR",
        "enrolment": "NOT CHECKED (TEST MODE)" if scenario == "test_mode" else "CLEAN",
        "test_mode": scenario == "test_mode",
        "check_result": "?" if scenario == "test_mode" else "CLEAR",
        "wipe_state": "SKIPPED" if scenario == "test_mode" else ("FAILED" if scenario == "wipe_failed" else "WIPED"),
        "certificate": hashlib.sha256(("wipe|%s" % serial).encode()).hexdigest(),
        "tests": tests, "observations": {"grade": rng.choice(["A", "B", "B", "C"]), "issues": [], "notes": ""},
        "overall": overall, "reasons": reasons,
    }
    if scenario == "unsigned":
        rep["integrity"] = {"algorithm": "SHA-256 + Ed25519", "sha256": report_fingerprint(rep),
                            "signature": "", "key_id": "", "signed": False,
                            "note": "no ITAD_DATA stick - not signed"}
        return rep
    return sign_report(rep, key)


def dump(rep):
    return json.dumps(rep, indent=2, sort_keys=True)
