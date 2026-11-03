#!/usr/bin/env python3
"""
upload_report.py - send a finished ITAD laptop report to the ITAD Platform website.

This file lives OUTSIDE the existing engine on purpose: nothing in laptop_check.py,
laptop_wipe.py, laptop_test.py, itad_run.py or laptop_window.py was changed. Run it after a
laptop has finished, from the technician's Windows laptop or from the boot USB:

    python3 upload_report.py SN_ABC123_20261026-101500.json --server https://itad.example.com \
        --token itad_xxxxxxxx

Or set the two values once as environment variables:   ITAD_SERVER   ITAD_TOKEN

What it does:
  1. Reads the report .json file exactly as the engine saved it (it is never edited).
  2. Optionally asks the server for the job ticket first (--check-job) so a wrong Job ID is
     caught before upload.
  3. Sends the report to /api/v1/engine/reports and prints what the server decided:
     signature status, any Job/Asset cross-check problems, and whether a certificate can be issued.

It uses only the Python standard library. Exit codes: 0 stored and trusted, 1 stored but NOT
trusted or has problems, 2 could not send.
"""
import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request


def call(method, url, token, body=None, cafile=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"})
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": "HTTP %d" % e.code}


def main():
    ap = argparse.ArgumentParser(description="Upload an ITAD laptop report to the ITAD Platform")
    ap.add_argument("report", help="the report .json file")
    ap.add_argument("--server", default=os.environ.get("ITAD_SERVER", ""), help="e.g. https://itad.example.com")
    ap.add_argument("--token", default=os.environ.get("ITAD_TOKEN", ""), help="API token from the website")
    ap.add_argument("--check-job", action="store_true", help="look the job up on the server before uploading")
    ap.add_argument("--cafile", default="", help="extra CA certificate file (private servers)")
    a = ap.parse_args()
    if not a.server or not a.token:
        print("Give --server and --token (or set ITAD_SERVER and ITAD_TOKEN).")
        return 2
    server = a.server.rstrip("/")
    try:
        with open(a.report, encoding="utf-8") as f:
            rep = json.load(f)
    except Exception as ex:
        print("Cannot read the report: %s" % ex)
        return 2

    try:
        if a.check_job:
            code, job = call("GET", "%s/api/v1/engine/jobs/%s" % (server, rep.get("job_id", "")), a.token,
                             cafile=a.cafile or None)
            if code != 200:
                print("Job check: %s" % job.get("error", code))
                print("Create the job on the website first, or fix the Job ID. Nothing was uploaded.")
                return 1
            print("Job check OK: %s (%s), customer: %s" % (job["job_ref"], job["status"], job["customer"] or "-"))
        code, res = call("POST", server + "/api/v1/engine/reports", a.token, rep, cafile=a.cafile or None)
    except Exception as ex:
        print("Could not reach the server: %s" % ex)
        return 2
    if code not in (200, 201):
        print("Server refused the report: %s" % res.get("error", code))
        return 2
    print("Stored as report #%s for asset #%s" % (res["report_id"], res["asset_id"]))
    print("Signature        : %s" % res["signature_status"])
    for p in res.get("cross_check_problems", []):
        print("Cross-check      : %s" % p)
    print("Certificate      : %s" % ("can be issued" if res.get("certificate_eligible") else "cannot be issued yet"))
    for b in res.get("certificate_blockers", []):
        print("  - %s" % b)
    for c in res.get("certificates", []):
        print("Issued           : %s" % c["number"])
    ok = res["signature_status"] == "valid" and not res.get("cross_check_problems")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
