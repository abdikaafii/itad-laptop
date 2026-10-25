#!/usr/bin/env python3
"""
itad_run.py - run the full ITAD pipeline in one go (v1.1.0)

Runs, in this order:
  1. laptop_check.py  (read-only)   -> laptop_check.json
  2. laptop_wipe.py   (destructive) -> laptop_wipe.json
  3. laptop_test.py   (read-mostly) -> laptop_test.json

v1.1.0 SAFETY CHANGES:
  - The wipe only starts when step 1 says CLEAR. BLOCKED and CANNOT CHECK
    both stop the run (override with --continue-on-blocked).
  - The wipe never starts by itself. The technician must type the last 4
    characters of the laptop serial number first (or "WIPE" if the serial
    is unknown). Anything else skips the wipe.
  - --commit is only sent to laptop_wipe.py after that confirmation.
  - --dry-run runs the wipe step WITHOUT --commit (nothing is erased).
  - With no keyboard (no terminal) the wipe is refused unless
    --yes-i-mean-it is given.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(TOOLS_DIR, "laptop_check.py")
WIPE = os.path.join(TOOLS_DIR, "laptop_wipe.py")
TEST = os.path.join(TOOLS_DIR, "laptop_test.py")

BANNER = "=" * 72
USE_COLOR = sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb" \
    and not os.environ.get("NO_COLOR")
_CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32",
          "yellow": "33", "blue": "34", "cyan": "36", "white": "37",
          "bg_red": "41", "bg_blue": "44"}


def paint(text, *styles):
    if not USE_COLOR or not styles:
        return text
    return "\033[%sm%s\033[0m" % (";".join(_CODES[s] for s in styles), text)


STEPS = ["1 Start", "2 Checks", "3 Wipe & Tests", "4 Report"]


def step_bar(active):
    """Same four steps as the LaptopSeal screen layout."""
    parts = []
    for i, name in enumerate(STEPS):
        if i == active:
            parts.append(paint(" %s " % name, "bold", "white", "bg_blue"))
        else:
            parts.append(paint(" %s " % name, "dim"))
    print()
    print(paint("  LaptopSeal", "bold", "cyan")
          + paint("   Test. Wipe. Verify. Certify.", "dim"))
    print("  " + " ".join(parts))


def _run_tool(path, args):
    print()
    print(BANNER)
    print("RUN:", os.path.basename(path), " ".join(args))
    print(BANNER)
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


def confirm_wipe(serial, check_result):
    """Ask the technician to confirm. True = go ahead and wipe."""
    print()
    print(paint("  " + "=" * 60, "red"))
    print(paint("  WIPE CONFIRMATION - this ERASES the internal drive(s)",
                "bold", "red"))
    print(paint("  " + "=" * 60, "red"))
    print("  Serial number : %s" % (serial or "UNKNOWN"))
    print("  Check result  : %s" % check_result)
    print("  The boot USB is not wiped, only the laptop's own drives.")
    print()
    try:
        if check_result != "CLEAR":
            print(paint("  The check did NOT say CLEAR. Wiping may be unsafe.",
                        "bold", "yellow"))
            a = input("  Type OVERRIDE to continue, or press Enter to skip: ").strip()
            if a != "OVERRIDE":
                return False
        s = (serial or "").strip().upper()
        if len(s) >= 4 and s != "UNKNOWN":
            want = s[-4:]
            a = input("  Type the last 4 characters of the serial (%s) "
                      "to wipe, or press Enter to skip: " % want).strip().upper()
            return a == want
        a = input("  Serial unknown. Type WIPE to wipe, or press Enter "
                  "to skip: ").strip()
        return a == "WIPE"
    except (KeyboardInterrupt, EOFError):
        print()
        return False


def main():
    # Auto-generate Job ID based on current time if not provided
    default_job = "JOB-" + datetime.now().strftime("%Y%m%d-%H%M%S")

    # Check ITAD_DATA first (USB stick), then /tmp, then ~/itad-reports
    default_out = os.environ.get("ITAD_DATA")
    if not default_out or not os.path.isdir(default_out) or not os.access(default_out, os.W_OK):
        default_out = "/tmp/itad-reports"
        os.makedirs(default_out, exist_ok=True)
        if not os.access(default_out, os.W_OK):
            default_out = os.path.expanduser("~/itad-reports")

    ap = argparse.ArgumentParser(description="ITAD full pipeline")
    ap.add_argument("--job-id", default=default_job)
    ap.add_argument("--asset-id", default="AUTO")
    ap.add_argument("--tech", default="AUTO")
    ap.add_argument("--out-dir", default=default_out)
    ap.add_argument("--method", default="auto",
                    choices=["auto", "clear", "random", "dod",
                             "purge", "crypto", "discard"])
    ap.add_argument("--skip-wipe", action="store_true")
    ap.add_argument("--skip-test", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="run the wipe step without --commit (erases nothing)")
    ap.add_argument("--continue-on-blocked", action="store_true",
                    help="go on to the wipe step even if the check is not CLEAR")
    ap.add_argument("--yes-i-mean-it", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--no-interactive", action="store_true")
    ap.add_argument("--cpu-load", type=int, default=30)
    ap.add_argument("--ram-minutes", type=int, default=2)
    args = ap.parse_args()

    global USE_COLOR
    if args.no_color:
        USE_COLOR = False

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

    # --- Ask the technician for details if we are running interactively ---
    step_bar(0)
    if sys.stdin.isatty() and args.asset_id == "AUTO":
        try:
            print("\n" + "=" * 60)
            print(" ITAD TOOL - JOB DETAILS")
            print("=" * 60)
            print(f" Job ID: {args.job_id}")

            user_asset = input(" Enter Asset ID (or press Enter for AUTO): ").strip()
            if user_asset:
                args.asset_id = user_asset

            user_tech = input(" Enter Technician Name (or press Enter for AUTO): ").strip()
            if user_tech:
                args.tech = user_tech

            print("=" * 60 + "\n")
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(4)
    # -----------------------------------------------------------------------

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
    step_bar(1)
    rc1 = _run_tool(CHECK, common + ["--json", check_json])
    rep1 = _load(check_json) or {}
    result1 = rep1.get("result", "?")

    # Only CLEAR may go on to the wipe. BLOCKED, CANNOT CHECK and anything
    # unknown stop here unless the technician used --continue-on-blocked.
    if result1 != "CLEAR" and not args.skip_wipe and not args.continue_on_blocked:
        print()
        print(paint("STAGE 1 (check) reported %s - stopping." % result1,
                    "bold", "red"))
        print(f"Review {check_json}.")
        print("Nothing was wiped. To go on anyway (only if a manager "
              "agrees), re-run with --continue-on-blocked.")
        sys.exit(5)

    # Stage 2: wipe
    rc2 = 0
    wipe_state = "SKIPPED"
    if not args.skip_wipe:
        step_bar(2)
        serial = (rep1.get("identity") or {}).get("serial", "")
        go = False
        commit = False
        if args.dry_run:
            go, commit, wipe_state = True, False, "DRY RUN"
        elif args.yes_i_mean_it:
            go, commit, wipe_state = True, True, "WIPED"
        elif sys.stdin.isatty():
            if confirm_wipe(serial, result1):
                go, commit, wipe_state = True, True, "WIPED"
            else:
                print(paint("\n  Wipe skipped. Nothing was erased.", "bold", "yellow"))
                wipe_state = "SKIPPED (not confirmed)"
        else:
            print(paint("\n  No keyboard available, wipe refused. "
                        "Add --yes-i-mean-it to allow it.", "bold", "yellow"))
            wipe_state = "SKIPPED (no confirmation)"

        if go:
            wargs = common + [
                "--json", wipe_json,
                "--method", args.method,
                "--check-report", check_json,
            ]
            if commit:
                wargs.append("--commit")
            if args.yes_i_mean_it:
                wargs.append("--yes-i-mean-it")
            if args.force:
                wargs.append("--force")
            rc2 = _run_tool(WIPE, wargs)

    # Stage 3: test
    rc3 = 0
    if not args.skip_test:
        step_bar(2)
        targs = common + [
            "--json", test_json,
            "--cpu-load", str(args.cpu_load),
            "--ram-minutes", str(args.ram_minutes),
        ]
        if args.no_interactive:
            targs.append("--no-interactive")
        rc3 = _run_tool(TEST, targs)

    # Aggregate
    ran_wipe = wipe_state in ("WIPED", "DRY RUN")
    rep2 = _load(wipe_json) if ran_wipe else {}
    rep3 = _load(test_json) if not args.skip_test else {}

    step_bar(3)
    print()
    print(BANNER)
    print("PIPELINE SUMMARY")
    print(BANNER)
    print(f"  check  : exit {rc1}  result={result1}")
    print(f"  wipe   : exit {rc2}  state={wipe_state}  overall={(rep2 or {}).get('overall', '-')}")
    print(f"  test   : exit {rc3}  overall={(rep3 or {}).get('overall', '-')}")
    print(f"  reports saved in: {args.out_dir}/")

    codes = [rc1]
    if ran_wipe:
        codes.append(rc2)
    if not args.skip_test:
        codes.append(rc3)
    if any(c == 1 for c in codes):
        sys.exit(1)
    if any(c in (2, 4, 5) for c in codes):
        sys.exit(2)
    if (not args.skip_wipe and not args.dry_run
            and wipe_state.startswith("SKIPPED")):
        sys.exit(2)   # the wipe was asked for but did not happen
    sys.exit(0)


if __name__ == "__main__":
    main()
