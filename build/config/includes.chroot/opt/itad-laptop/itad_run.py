#!/usr/bin/env python3
"""
itad_run.py - run the full ITAD pipeline in one go (v1.0.2)

Runs, in this order:
  1. laptop_check.py  (read-only)   -> laptop_check.json
  2. laptop_wipe.py   (destructive) -> laptop_wipe.json
  3. laptop_test.py   (read-mostly) -> laptop_test.json

Stops on BLOCKED at step 1 (override with --continue-on-blocked).
Wipe step asks for its own confirmation unless --yes-i-mean-it.

Exit codes:
  0 = every stage passed
  1 = at least one stage FAILED
  2 = at least one stage WARN / partial
  3 = not root
  4 = aborted by user
  5 = check stage reported BLOCKED
"""
import argparse
import json
import os
import subprocess
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(TOOLS_DIR, "laptop_check.py")
WIPE = os.path.join(TOOLS_DIR, "laptop_wipe.py")
TEST = os.path.join(TOOLS_DIR, "laptop_test.py")

BANNER = "=" * 72


def _run_tool(path, args):
    print()
    print(BANNER)
    print("RUN:", os.path.basename(path), " ".join(args))
    print(BANNER)
    # Using subprocess.call ensures it waits and passes back the exact exit code
    return subprocess.call([sys.executable, path] + args)


def _load(path):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return None


def _verify_environment():
    for p in (CHECK, WIPE, TEST):
        if not os.path.isfile(p):
            print(f"ERROR: Missing tool: {p}", file=sys.stderr)
            print("Make sure all .py files are in the same directory.", file=sys.stderr)
            sys.exit(1)


def main():
    # --- FIXED: Check ITAD_DATA first (USB stick), then /tmp, then ~/itad-reports ---
    default_out = os.environ.get("ITAD_DATA")
    if not default_out or not os.path.isdir(default_out) or not os.access(default_out, os.W_OK):
        default_out = "/tmp/itad-reports"
        os.makedirs(default_out, exist_ok=True)
        if not os.access(default_out, os.W_OK):
            default_out = os.path.expanduser("~/itad-reports")

    ap = argparse.ArgumentParser(description="ITAD full pipeline")
    ap.add_argument("--job-id", default="")
    ap.add_argument("--asset-id", default="")
    ap.add_argument("--tech", default="")
    ap.add_argument("--out-dir", default=default_out)
    ap.add_argument("--method", default="auto",
                    choices=["auto", "clear", "random", "dod",
                             "purge", "crypto", "discard"])
    ap.add_argument("--skip-wipe", action="store_true")
    ap.add_argument("--skip-test", action="store_true")
    ap.add_argument("--continue-on-blocked", action="store_true")
    ap.add_argument("--yes-i-mean-it", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--no-interactive", action="store_true")
    ap.add_argument("--cpu-load", type=int, default=30)
    ap.add_argument("--ram-minutes", type=int, default=2)
    args = ap.parse_args()

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("ERROR: Must run as root (sudo).", file=sys.stderr)
        sys.exit(3)

    _verify_environment()
    
    # Ensure output directory exists and is writable
    try:
        os.makedirs(args.out_dir, exist_ok=True)
        test_file = os.path.join(args.out_dir, ".write_test")
        with open(test_file, 'w') as f:
            f.write("test")
        os.remove(test_file)
    except Exception as e:
        print(f"ERROR: Cannot write to output directory {args.out_dir}: {e}", file=sys.stderr)
        sys.exit(1)

    check_json = os.path.join(args.out_dir, "laptop_check.json")
    wipe_json = os.path.join(args.out_dir, "laptop_wipe.json")
    test_json = os.path.join(args.out_dir, "laptop_test.json")

    common = []
    if args.job_id:
        common += ["--job-id", args.job_id]
    if args.asset_id:
        common += ["--asset-id", args.asset_id]
    if args.tech:
        common += ["--tech", args.tech]
    if args.no_color:
        common += ["--no-color"]

    # Stage 1: check
    rc1 = _run_tool(CHECK, common + ["--json", check_json])
    rep1 = _load(check_json) or {}
    result1 = rep1.get("result", "?")
    
    if result1 == "BLOCKED" and not args.continue_on_blocked:
        print()
        print("STAGE 1 (check) reported BLOCKED — stopping.")
        print(f"Review {check_json}, then re-run with --continue-on-blocked.")
        sys.exit(5)

    # Stage 2: wipe
    rc2 = 0
    if not args.skip_wipe:
        wargs = common + [
            "--json", wipe_json,
            "--method", args.method,
            "--commit",
            "--check-report", check_json,
        ]
        if args.yes_i_mean_it:
            wargs.append("--yes-i-mean-it")
        if args.force:
            wargs.append("--force")
        rc2 = _run_tool(WIPE, wargs)

    # Stage 3: test
    rc3 = 0
    if not args.skip_test:
        targs = common + [
            "--json", test_json,
            "--cpu-load", str(args.cpu_load),
            "--ram-minutes", str(args.ram_minutes),
        ]
        if args.no_interactive:
            targs.append("--no-interactive")
        rc3 = _run_tool(TEST, targs)

    # Aggregate
    rep2 = _load(wipe_json) if not args.skip_wipe else {}
    rep3 = _load(test_json) if not args.skip_test else {}
    
    print()
    print(BANNER)
    print("PIPELINE SUMMARY")
    print(BANNER)
    print(f"  check  : exit {rc1}  result={result1}")
    print(f"  wipe   : exit {rc2}  overall={(rep2 or {}).get('overall', '-')}")
    print(f"  test   : exit {rc3}  overall={(rep3 or {}).get('overall', '-')}")
    print(f"  reports saved in: {args.out_dir}/")

    codes = [rc1]
    if not args.skip_wipe:
        codes.append(rc2)
    if not args.skip_test:
        codes.append(rc3)
    if any(c == 1 for c in codes):
        sys.exit(1)
    if any(c in (2, 4, 5) for c in codes):
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()