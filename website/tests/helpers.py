"""Build engine reports exactly like laptop_window.py does (same fingerprint rule, Ed25519 signature)."""
import hashlib
import json
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

COMPONENTS = ["CPU", "RAM", "Drive Health", "Battery", "Wi-Fi", "Ethernet", "Bluetooth", "Webcam", "USB", "Screen",
              "Keyboard", "Touchpad", "Touchscreen", "Speakers", "Microphone"]


def fingerprint(rep):
    body = dict((k, v) for k, v in rep.items() if k != "integrity")
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class Engine:
    """Stands in for one boot-USB signing key."""

    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.pub = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.key_id = hashlib.sha256(self.pub).hexdigest()[:16]

    def report(self, job, asset, serial, sign=True, tests=None, wipe_ok=True, verified=True, check="CLEAR", **extra):
        t = tests or {}
        rep = {
            "tool": "laptop_window.py", "version": "test", "time": "2026-11-02 10:30:00 (Europe/Dublin)",
            "job_id": job, "asset_id": asset, "serial": serial, "technician": "Ali",
            "make_model": "HP EliteBook 840 G5", "cpu": "Intel Core i5-8350U", "ram": "16 GB", "storage": "nvme0n1 256 GB", "bios": "HP Q78",
            "chassis": "Laptop", "check_result": check, "absolute": "CLEAR", "enrolment": "CLEAN", "wipe_state": "WIPED",
            "tests": [{"component": c, "status": t.get(c, "N/A" if c == "Touchscreen" else "PASS"), "notes": [], "details": {}} for c in COMPONENTS],
            "erasures": [{"path": "/dev/nvme0n1", "model": "Samsung PM981", "capacity_gb": 256, "serial": "S4YKNX0M", "passes": 1,
                          "method": "Purge - cryptographic erase", "level": "Purge", "standard": "NIST SP 800-88 Rev. 2 Purge",
                          "verified": verified, "status": ("Success" if verified else "Erased - not verified") if wipe_ok else "Failed",
                          "technician": "Ali", "started": "2026-11-02 10:16:10", "finished": "2026-11-02 10:17:32",
                          "erasure_id": str(secrets.randbelow(10 ** 9))}],
            "observations": {"grade": "B", "issues": [], "notes": ""}, "nonce": secrets.token_hex(4),
        }
        rep.update(extra)
        fp = fingerprint(rep)
        integ = {"algorithm": "SHA-256 + Ed25519", "sha256": fp, "signature": "", "key_id": "", "signed": False, "note": "no ITAD_DATA stick - not signed"}
        if sign:
            integ.update(signature=self.key.sign(fp.encode()).hex(), key_id=self.key_id, public_key=self.pub.hex(), signed=True)
            integ.pop("note")
        rep["integrity"] = integ
        return rep
