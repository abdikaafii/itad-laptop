#!/usr/bin/env python3
"""itad_push.py - send a finished engine report to the ITAD Seal platform.

This is a separate, read-only helper. It does NOT change or replace any existing engine
script (laptop_check.py, laptop_wipe.py, laptop_test.py, laptop_window.py, itad_run.py).
It never starts a wipe and cannot bypass any physical-device safety check.

Usage
  export ITAD_PLATFORM_URL=https://itad.example.com
  export ITAD_PLATFORM_TOKEN=<station token from Settings > Stations>
  python3 itad_push.py report.json            # send a finished, signed report
  python3 itad_push.py --check ASSET_TAG --serial SN --job JOB   # advisory look-up before a wipe
  python3 itad_push.py --ping                 # test URL + token

Python standard library only, so it runs on the boot USB without installing anything.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def call(method, path, body=None):
    url = os.environ.get("ITAD_PLATFORM_URL", "").rstrip("/")
    tok = os.environ.get("ITAD_PLATFORM_TOKEN", "")
    if not url or not tok:
        sys.exit("Set ITAD_PLATFORM_URL and ITAD_PLATFORM_TOKEN first (see integrations/README.md).")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {}
    except urllib.error.URLError as e:
        sys.exit("Cannot reach the platform: %s" % e.reason)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", nargs="?", help="engine report .json file to send")
    ap.add_argument("--ping", action="store_true")
    ap.add_argument("--check", metavar="ASSET_TAG", help="advisory cross-check of an Asset ID")
    ap.add_argument("--serial", default="")
    ap.add_argument("--job", default="")
    a = ap.parse_args()
    if a.ping:
        code, j = call("GET", "/api/engine/ping")
        print(code, j)
        return 0 if code == 200 else 1
    if a.check:
        q = urllib.parse.urlencode({"serial": a.serial, "job": a.job})
        code, j = call("GET", "/api/engine/assets/%s?%s" % (urllib.parse.quote(a.check), q))
        print(json.dumps(j, indent=2))
        print("\nThis answer is advice only. The station still runs its own safety checks before any wipe.")
        return 0 if j.get("wipe_allowed") else 2
    if not a.report:
        ap.print_help()
        return 1
    with open(a.report, encoding="utf-8") as f:
        rep = json.load(f)          # sent exactly as the engine wrote it (never edited)
    code, j = call("POST", "/api/engine/submit", rep)
    print(json.dumps(j, indent=2))
    return 0 if code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
