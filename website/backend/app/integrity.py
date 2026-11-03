"""Tamper evidence.

1. verify_engine_report(): checks a report made by the existing laptop engine
   (laptop_window.py). It uses exactly the same fingerprint rule as
   build/.../verify_report.py, so a report that verifies there verifies here.
2. PlatformSigner: signs the certificates this platform issues (Ed25519).
"""
import hashlib
import json
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)

from . import config

SIGN_FIELDS = ("integrity",)


def fingerprint(rep):
    """Same rule as the engine: SHA-256 of the report without 'integrity'."""
    body = dict((k, v) for k, v in rep.items() if k not in SIGN_FIELDS)
    data = json.dumps(body, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def key_id_of(pub_raw):
    return hashlib.sha256(pub_raw).hexdigest()[:16]


def verify_engine_report(rep):
    """Return dict: sha256, fingerprint_ok, signed, signature_ok, key_id,
    public_key_hex, note. Never raises for a bad report."""
    integ = rep.get("integrity") or {}
    fp = fingerprint(rep)
    out = {"sha256": fp, "fingerprint_ok": False, "signed": False,
           "signature_ok": False, "key_id": integ.get("key_id") or "",
           "public_key_hex": integ.get("public_key") or "", "note": ""}
    if not integ:
        out["note"] = "report has no integrity section"
        return out
    if fp != integ.get("sha256"):
        out["note"] = "content changed after the report was made"
        return out
    out["fingerprint_ok"] = True
    if not integ.get("signed"):
        out["note"] = integ.get("note") or "not signed"
        return out
    out["signed"] = True
    try:
        pub_raw = bytes.fromhex(integ.get("public_key", ""))
        Ed25519PublicKey.from_public_bytes(pub_raw).verify(
            bytes.fromhex(integ.get("signature", "")), fp.encode("ascii"))
        out["signature_ok"] = True
        out["key_id"] = key_id_of(pub_raw)
    except (InvalidSignature, ValueError):
        out["note"] = "signature is not valid"
    return out


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class PlatformSigner:
    def __init__(self, path=None):
        self.path = path or config.CERT_KEY_FILE
        self._key = None

    def _load(self):
        if self._key:
            return self._key
        if os.path.exists(self.path):
            with open(self.path, "rb") as f:
                self._key = serialization.load_pem_private_key(f.read(), None)
        else:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self._key = Ed25519PrivateKey.generate()
            with open(self.path, "wb") as f:
                f.write(self._key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption()))
            os.chmod(self.path, 0o600)
        return self._key

    def public_raw(self):
        return self._load().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def key_id(self):
        return key_id_of(self.public_raw())

    def sign(self, payload_obj):
        """-> (payload_json, sha256_hex, signature_hex, key_id)"""
        payload = canonical(payload_obj)
        sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        sig = self._load().sign(sha.encode("ascii")).hex()
        return payload, sha, sig, self.key_id()

    def verify(self, payload_json, sha_hex, sig_hex, key_id):
        """True only if the payload still hashes to sha, the signature is valid and
        was made with THIS platform's key."""
        if hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != sha_hex:
            return False
        if key_id != self.key_id():
            return False
        try:
            self._load().public_key().verify(bytes.fromhex(sig_hex), sha_hex.encode("ascii"))
            return True
        except (InvalidSignature, ValueError):
            return False


signer = PlatformSigner()
