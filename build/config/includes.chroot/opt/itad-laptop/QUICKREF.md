# ITAD Quick Reference

## Per laptop — one command

    itad --job-id J2025-XXXX --asset-id AST-NNNNN --tech <your-name>

Follow the prompts. At the wipe step, type `WIPE:<serial-shown-on-screen>` exactly.
Wrong serial → nothing is wiped, you try again.

## What "itad" runs, in order

1. **CHECK** (read-only) — detects Absolute / Autopilot / MDM. Never writes.
2. **WIPE** (destructive) — writes zeros / issues purge. Asks for confirmation.
3. **TEST** (read-mostly) — CPU, RAM, drive, battery, screen, keyboard,
   touchpad, webcam, speakers, WiFi, USB. You interact for 5 of these.

Reports land in `/tmp/itad-run/` by default.

## Result → what to do

| Banner | Exit | Action |
|---|---|---|
| **CLEAR** then **WIPED** then **ALL PASS** | 0 | READY pile. Ship. |
| **CANNOT CHECK** | 2 | QUARANTINE. Manager decision. Do NOT wipe. |
| **BLOCKED** | 1 or 5 | QUARANTINE. Device is MDM-enrolled. Do NOT wipe. |
| **WARNING** (test step) | 2 | READY-WARN pile. Some component unverified. |
| **FAILED** (test step) | 1 | RECYCLE pile. Do not ship. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | PASS |
| 1 | FAIL — do not ship |
| 2 | WARN / partial — manager review |
| 3 | not root — rerun with `sudo` |
| 4 | you aborted (typed wrong serial) |
| 5 | unsafe target (USB / OS disk / BLOCKED) |

## Key commands

    # Full pipeline
    itad --job-id J... --asset-id A... --tech name

    # Dry run (nothing wiped)
    itad --job-id J... --asset-id A... --tech name --skip-wipe

    # Just re-run the check
    sudo python3 /opt/itad-laptop/laptop_check.py --job-id J... --tech name

    # Just re-run the test (no wipe)
    sudo python3 /opt/itad-laptop/laptop_test.py --job-id J... --tech name

    # Generate customer certificate from today's reports
    python3 /opt/itad-laptop/itad_report.py --dir /tmp/itad-run --out ~/cert.html

## Interactive tests — what you do

| Test | Action |
|---|---|
| screen | Look. Press Enter for next colour, `s` to skip, `f` to flag dead pixel |
| keyboard | Type letters, numbers, Enter, Space. Type `D O N E` to finish |
| touchpad | Drag finger across full pad, click both buttons |
| speakers | Listen, type `y` or `n` |
| usb | Plug a **test** USB stick when prompted, wait 2s |

## Do NOT

- Do NOT run `laptop_wipe.py --commit` without a dry run first
- Do NOT use `--no-interactive` on real jobs
- Do NOT wipe a BLOCKED device — escalate
- Do NOT reuse a real customer drive as the USB test stick

## Reports — where they go

    /tmp/itad-run/laptop_check.json
    /tmp/itad-run/laptop_wipe.json
    /tmp/itad-run/laptop_test.json

Copy them off at end of pallet. `/tmp` is lost on reboot.
