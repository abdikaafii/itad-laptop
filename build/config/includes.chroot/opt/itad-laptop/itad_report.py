#!/usr/bin/env python3
"""
itad_report.py - consolidate check/wipe/test JSONs into a single
customer-facing certificate (v1.0.0).

Usage:
  # one job
  python3 itad_report.py --dir /tmp/itad-run --out report.html
  # one job, batch CSV
  python3 itad_report.py --dir /tmp/itad-run --csv batch.csv
  # many jobs (each sub-dir has check/wipe/test JSONs)
  python3 itad_report.py --batch /var/itad/jobs --out-dir /var/itad/reports \\
                         --csv /var/itad/batch.csv
"""
import argparse
import csv
import hashlib
import html
import json
import os
import sys
from datetime import datetime

CSV_FIELDS = [
    "date", "job_id", "asset_id", "tech",
    "manufacturer", "model", "serial",
    "check_result", "wipe_overall", "wipe_method", "wipe_certificate",
    "test_overall", "cpu", "ram", "drive", "battery", "screen",
    "keyboard", "touchpad", "webcam", "speakers", "wifi", "usb",
    "verdict", "verdict_note",
]


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _hash_reports(*reports):
    h = hashlib.sha256()
    for r in reports:
        if r is None:
            h.update(b"<missing>")
        else:
            h.update(json.dumps(r, sort_keys=True,
                                separators=(",", ":")).encode())
    return h.hexdigest()


def _verdict(check, wipe, test):
    if not check or not wipe:
        return "PARTIAL", "check or wipe report missing"
    if check.get("result") == "BLOCKED":
        return "QUARANTINE", "check reported BLOCKED"
    if wipe.get("overall") == "FAIL":
        return "QUARANTINE", "wipe failed"
    if not test:
        return "PARTIAL", "test not run"
    if test.get("overall") == "FAIL":
        return "RECYCLE", "hardware test failed"
    if test.get("overall") == "WARN":
        return "READY-WARN", "passed with warnings"
    return "READY", "all stages passed"


def _esc(x):
    return html.escape(str(x)) if x is not None else ""


def _result_by_component(results, name):
    for r in (results or []):
        if (r.get("component") or "").lower() == name.lower():
            return r
    return None


def _meta(check, wipe, test):
    return {
        "job_id": ((check or {}).get("job_id")
                   or (wipe or {}).get("job_id")
                   or (test or {}).get("job_id") or ""),
        "asset_id": ((check or {}).get("asset_id")
                     or (wipe or {}).get("asset_id")
                     or (test or {}).get("asset_id") or ""),
        "tech": ((check or {}).get("technician")
                 or (wipe or {}).get("technician")
                 or (test or {}).get("technician") or ""),
        "date": ((check or {}).get("time")
                 or datetime.now().isoformat()),
    }


def build_html(check, wipe, test, meta, out_path):
    verdict, vnote = _verdict(check, wipe, test)
    cert_sha = _hash_reports(check, wipe, test)
    ident = (check or {}).get("identity") or {}
    disks = (check or {}).get("internal_disks") or []
    wipe_devs = (wipe or {}).get("devices") or []
    test_results = (test or {}).get("results") or []

    h = []
    h.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    h.append("<title>ITAD Certificate %s</title>"
             % _esc(meta.get("job_id") or meta.get("asset_id")))
    h.append("""
<style>
  body { font-family: -apple-system, system-ui, sans-serif;
         max-width: 900px; margin: 2em auto; color: #222; }
  h1 { border-bottom: 2px solid #333; padding-bottom: .3em; }
  h2 { margin-top: 1.5em; border-bottom: 1px solid #ccc;
       padding-bottom: .2em; }
  table { border-collapse: collapse; width: 100%; margin: .5em 0; }
  th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; }
  th { background: #f4f4f4; }
  .PASS, .CLEAR, .WIPED, .READY { color: #080; font-weight: bold; }
  .FAIL, .BLOCKED, .QUARANTINE { color: #c00; font-weight: bold; }
  .WARN, .PARTIAL, .CANNOT-CHECK, .READY-WARN { color: #b60;
                                                font-weight: bold; }
  .verdict { font-size: 1.5em; padding: .5em 1em;
             border: 3px solid currentColor; display: inline-block;
             margin: 1em 0; }
  .footer { margin-top: 3em; border-top: 1px solid #ccc;
            padding-top: 1em; font-size: .85em; color: #666; }
  code { font-family: monospace; background: #f4f4f4;
         padding: 2px 6px; border-radius: 3px; }
</style></head><body>
""")
    h.append("<h1>ITAD Certificate of Data Destruction</h1>")
    h.append("<div class='verdict %s'>%s</div>" % (_esc(verdict),
                                                    _esc(verdict)))
    h.append("<p>%s</p>" % _esc(vnote))

    h.append("<h2>Job</h2><table>")
    for k, v in (("Job ID", meta.get("job_id")),
                 ("Asset ID", meta.get("asset_id")),
                 ("Technician", meta.get("tech")),
                 ("Generated", meta.get("date"))):
        h.append("<tr><th>%s</th><td>%s</td></tr>"
                 % (_esc(k), _esc(v or "-")))
    h.append("</table>")

    h.append("<h2>Device</h2><table>")
    for k, v in (("Manufacturer", ident.get("manufacturer")),
                 ("Model", ident.get("model")),
                 ("Version", ident.get("version")),
                 ("System serial", ident.get("serial")),
                 ("BIOS", "%s %s (%s)"
                  % (ident.get("bios_vendor", ""),
                     ident.get("bios_version", ""),
                     ident.get("bios_date", ""))),
                 ("Boot mode", ident.get("boot_mode")),
                 ("Secure Boot", ident.get("secure_boot"))):
        h.append("<tr><th>%s</th><td>%s</td></tr>"
                 % (_esc(k), _esc(v or "-")))
    h.append("</table>")

    if disks:
        h.append("<h2>Disks present at check</h2><table>")
        h.append("<tr><th>Path</th><th>Model</th><th>Size (GB)</th></tr>")
        for d in disks:
            h.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                     % (_esc(d.get("path")),
                        _esc(d.get("model")),
                        _esc(d.get("size_gb"))))
        h.append("</table>")

    h.append("<h2>Stage 1 — Pre-wipe check (read-only)</h2>")
    if check:
        h.append("<p>Result: <span class='%s'>%s</span></p>"
                 % (_esc(check.get("result", "-")),
                    _esc(check.get("result", "-"))))
        h.append("<p>Absolute: <b>%s</b> | Autopilot: <b>%s</b>"
                 % (_esc(check.get("absolute_result", "-")),
                    _esc(check.get("autopilot_result", "-"))))
        if check.get("absolute_signs"):
            h.append("<p>Absolute signs:</p><ul>")
            for s in check["absolute_signs"]:
                h.append("<li>%s</li>" % _esc(s))
            h.append("</ul>")
        if check.get("notes"):
            h.append("<p>Notes:</p><ul>")
            for n in check["notes"]:
                h.append("<li>%s</li>" % _esc(n))
            h.append("</ul>")
    else:
        h.append("<p><em>No check report.</em></p>")

    h.append("<h2>Stage 2 — Wipe</h2>")
    if wipe:
        h.append("<p>Overall: <b>%s</b> | Requested method: <b>%s</b></p>"
                 % (_esc(wipe.get("overall")),
                    _esc(wipe.get("method"))))
        if wipe_devs:
            h.append("<table><tr><th>Device</th><th>Model</th>"
                     "<th>Serial</th><th>Requested</th>"
                     "<th>Achieved</th><th>Verified</th></tr>")
            for dv in wipe_devs:
                ver = dv.get("verification") or {}
                h.append("<tr><td>%s</td><td>%s</td><td>%s</td>"
                         "<td>%s</td><td>%s</td><td>%s</td></tr>"
                         % (_esc(dv.get("path")),
                            _esc(dv.get("model")),
                            _esc(dv.get("serial")),
                            _esc(dv.get("requested")),
                            _esc(dv.get("achieved")),
                            "yes" if ver.get("verified") else "no"))
            h.append("</table>")
        h.append("<p>Wipe certificate SHA-256: <code>%s</code></p>"
                 % _esc(wipe.get("certificate_sha256", "-")))
    else:
        h.append("<p><em>No wipe report.</em></p>")

    h.append("<h2>Stage 3 — Component test</h2>")
    if test:
        h.append("<p>Overall: <b>%s</b></p>"
                 % _esc(test.get("overall")))
        if test_results:
            h.append("<table><tr><th>Component</th>"
                     "<th>Status</th><th>Notes</th></tr>")
            for tr in test_results:
                notes = "; ".join(tr.get("notes", [])) or "-"
                h.append("<tr><td>%s</td><td class='%s'>%s</td>"
                         "<td>%s</td></tr>"
                         % (_esc(tr.get("component")),
                            _esc(tr.get("status")),
                            _esc(tr.get("status")),
                            _esc(notes)))
            h.append("</table>")
    else:
        h.append("<p><em>No test report.</em></p>")

    h.append("<div class='footer'>")
    h.append("<p>Certificate SHA-256 (over all three source reports): "
             "<code>%s</code></p>" % _esc(cert_sha))
    h.append("<p>Generated by itad_report.py from laptop_check.py, "
             "laptop_wipe.py and laptop_test.py reports.</p>")
    h.append("</div></body></html>")

    with open(out_path, "w") as f:
        f.write("\n".join(h))
    return cert_sha, verdict


def build_csv_row(check, wipe, test, meta):
    verdict, vnote = _verdict(check, wipe, test)
    ident = (check or {}).get("identity") or {}
    test_results = (test or {}).get("results") or []

    def _comp(name):
        r = _result_by_component(test_results, name)
        return r.get("status", "") if r else ""

    return {
        "date": meta.get("date", ""),
        "job_id": meta.get("job_id", ""),
        "asset_id": meta.get("asset_id", ""),
        "tech": meta.get("tech", ""),
        "manufacturer": ident.get("manufacturer", ""),
        "model": ident.get("model", ""),
        "serial": ident.get("serial", ""),
        "check_result": (check or {}).get("result", ""),
        "wipe_overall": (wipe or {}).get("overall", ""),
        "wipe_method": (wipe or {}).get("method", ""),
        "wipe_certificate": (wipe or {}).get("certificate_sha256", ""),
        "test_overall": (test or {}).get("overall", ""),
        "cpu": _comp("CPU"),
        "ram": _comp("RAM"),
        "drive": _comp("Drive Health"),
        "battery": _comp("Battery"),
        "screen": _comp("Screen"),
        "keyboard": _comp("Keyboard"),
        "touchpad": _comp("Touchpad"),
        "webcam": _comp("Webcam"),
        "speakers": _comp("Speakers"),
        "wifi": _comp("Wi-Fi"),
        "usb": _comp("USB"),
        "verdict": verdict,
        "verdict_note": vnote,
    }


def process_dir(job_dir, out_dir, csv_writer):
    check = _load(os.path.join(job_dir, "laptop_check.json"))
    wipe = _load(os.path.join(job_dir, "laptop_wipe.json"))
    test = _load(os.path.join(job_dir, "laptop_test.json"))
    meta = _meta(check, wipe, test)
    out_html = None
    cert_sha = None
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        stem = meta["job_id"] or meta["asset_id"] or "job"
        out_html = os.path.join(out_dir, "itad_%s.html" % stem)
        cert_sha, verdict = build_html(check, wipe, test, meta, out_html)
    else:
        verdict = _verdict(check, wipe, test)[0]
    if csv_writer:
        csv_writer.writerow(build_csv_row(check, wipe, test, meta))
    return {"dir": job_dir, "html": out_html, "verdict": verdict,
            "certificate_sha256": cert_sha}


def main():
    ap = argparse.ArgumentParser(description="ITAD report generator")
    ap.add_argument("--dir", help="Single job directory with 3 JSONs")
    ap.add_argument("--batch", help="Parent dir of job subdirectories")
    ap.add_argument("--out", default="", help="Single HTML output path")
    ap.add_argument("--out-dir", default="",
                    help="Output directory (batch mode or --dir without --out)")
    ap.add_argument("--csv", default="", help="CSV output path")
    args = ap.parse_args()

    if not args.dir and not args.batch:
        ap.error("specify --dir or --batch")

    csv_f = None
    csv_writer = None
    if args.csv:
        csv_f = open(args.csv, "w", newline="")
        csv_writer = csv.DictWriter(csv_f, fieldnames=CSV_FIELDS)
        csv_writer.writeheader()

    jobs = []
    try:
        if args.dir:
            if not os.path.isdir(args.dir):
                print("Directory not found:", args.dir, file=sys.stderr)
                sys.exit(1)
            if args.out:
                check = _load(os.path.join(args.dir, "laptop_check.json"))
                wipe = _load(os.path.join(args.dir, "laptop_wipe.json"))
                test = _load(os.path.join(args.dir, "laptop_test.json"))
                meta = _meta(check, wipe, test)
                cert_sha, verdict = build_html(check, wipe, test,
                                              meta, args.out)
                jobs.append({"dir": args.dir, "html": args.out,
                             "verdict": verdict,
                             "certificate_sha256": cert_sha})
                print("Wrote:", args.out)
                if csv_writer:
                    csv_writer.writerow(build_csv_row(check, wipe, test,
                                                      meta))
            else:
                info = process_dir(args.dir, args.out_dir, csv_writer)
                jobs.append(info)
                if info["html"]:
                    print("Wrote:", info["html"])
        else:
            if not os.path.isdir(args.batch):
                print("Batch dir not found:", args.batch, file=sys.stderr)
                sys.exit(1)
            for name in sorted(os.listdir(args.batch)):
                sub = os.path.join(args.batch, name)
                if not os.path.isdir(sub):
                    continue
                if not any(os.path.exists(os.path.join(sub, f))
                           for f in ("laptop_check.json",
                                     "laptop_wipe.json",
                                     "laptop_test.json")):
                    continue
                info = process_dir(sub, args.out_dir, csv_writer)
                jobs.append(info)
                print("%-32s %-14s %s"
                      % (name, info["verdict"], info["html"] or ""))
    finally:
        if csv_f:
            csv_f.close()

    if jobs:
        print()
        print("=" * 60)
        counts = {}
        for j in jobs:
            counts[j["verdict"]] = counts.get(j["verdict"], 0) + 1
        for k in sorted(counts):
            print("  %-14s %d" % (k, counts[k]))
        if args.csv:
            print("  CSV:", args.csv)


if __name__ == "__main__":
    main()
