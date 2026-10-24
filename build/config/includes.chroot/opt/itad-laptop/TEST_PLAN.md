# ITAD Pre-Production Test Plan

Run this ONCE on a **sacrificial laptop with a sacrificial drive**
before trusting the tools on real equipment.

Estimated time: **60–90 minutes**.

## Prerequisites

- [ ] Test laptop with a drive you do not care about (5+ years old HDD is ideal)
- [ ] The ITAD USB (built with `build_usb.sh`, `first_boot.sh` already run)
- [ ] A 32+ GB USB stick (labeled **TEST-STICK** — not a customer drive)
- [ ] A second laptop with an encrypted / MDM-enrolled drive (optional, for step 8)
- [ ] Notebook or text file to record results

## Part A — Deploy and smoke test

### A1. Live USB boots

Boot the test laptop from the ITAD USB. Select **Try Ubuntu** (not Install).

- [ ] Desktop appears within 2 minutes
- [ ] Wifi or Ethernet works
- [ ] Open terminal (Ctrl+Alt+T)

### A2. First boot setup

    sudo bash /media/*/ITAD/first_boot.sh

- [ ] All deps show `OK`, no `MISS`
- [ ] All 5 `/opt/itad-laptop/*.py` show `OK`
- [ ] `itad --help` prints usage

### A3. Syntax check

    python3 -m py_compile /opt/itad-laptop/*.py && echo "OK"

- [ ] prints `OK`

## Part B — Safety guards (do these before any wipe)

### B1. Non-root guard

    python3 /opt/itad-laptop/laptop_wipe.py; echo "exit=$?"

- [ ] Prints "Must run as root"
- [ ] `exit=3`

### B2. Dry run

    sudo python3 /opt/itad-laptop/laptop_wipe.py --job-id TEST01

- [ ] Shows `DRY RUN` banner
- [ ] Lists target drive with `WILL be wiped`
- [ ] `exit=2`
- [ ] Drive is untouched (mount the drive, verify files still there)

### B3. USB drive guard

Plug in **TEST-STICK**, then:

    sudo python3 /opt/itad-laptop/laptop_wipe.py --job-id TEST01 --commit

- [ ] TEST-STICK refused with "removable / USB drive"
- [ ] `exit=5`
- [ ] TEST-STICK data intact

### B4. Abort on wrong confirmation

Unplug TEST-STICK, then:

    sudo python3 /opt/itad-laptop/laptop_wipe.py --job-id TEST01 --commit

- [ ] Prompts `WIPE:<serial>`
- [ ] Type something else (e.g. `nope`)
- [ ] Aborted, `exit=4`
- [ ] Drive untouched

## Part C — The real wipe

### C1. Correct confirmation, wipe proceeds

    sudo python3 /opt/itad-laptop/laptop_wipe.py --job-id TEST01 --commit

Type the exact `WIPE:<serial>` shown.

- [ ] `[1/1] /dev/sdX ...` header appears
- [ ] Progress bar advances (may take 5–40 min for an HDD)
- [ ] `WIPED (purge)` or `WIPED (clear-fallback)` shown
- [ ] `VERIFIED  sha256=...` shown
- [ ] `SMART short test passed` shown (or `not installed` / `did not report`)
- [ ] `CERTIFICATE  SHA-256: ...` printed at bottom
- [ ] `exit=0`
- [ ] `/tmp/itad-run/laptop_wipe.json` exists and contains `certificate_sha256`

### C2. Verify the drive is really blank

    sudo mount /dev/sdX1 /mnt 2>&1 | head -3

- [ ] `mount: wrong fs type` OR `mount: unknown filesystem type` — expected, because there's no filesystem anymore
- [ ] Try `sudo file -s /dev/sdX` — should say "data" not "NTFS" / "ext4"

If the drive mounts and shows files, **STOP** — the wipe didn't work. Do not proceed.

## Part D — Component tests

### D1. Test on blank drive

    sudo python3 /opt/itad-laptop/laptop_test.py --job-id TEST01 --tech tester

You will be prompted at 5 tests:

| Test | What you do | Expected |
|---|---|---|
| screen | Look at each colour, press Enter | All colours shown, no dead pixels |
| keyboard | Type letters, numbers, Enter, Space. Type `D O N E` | Keys light green as pressed |
| touchpad | Drag finger, click both buttons | Device detected, cursor moves |
| speakers | Listen to tone, type `y` or `n` | `y` = PASS |
| usb | Plug TEST-STICK | "device seen" |

- [ ] All interactive prompts appeared correctly
- [ ] CPU test ran 30s with progress bar
- [ ] RAM test ran 2min with progress bar (or skipped if `memtester` missing)
- [ ] Drive SMART reported (drive should still be readable even though wiped)
- [ ] Battery health % shown (if laptop has a battery)
- [ ] `exit=0`, `ALL PASS` banner
- [ ] `/tmp/itad-run/laptop_test.json` exists and shows `overall: PASS`

### D2. Test with a dead key (optional, for confidence)

Use a laptop with a broken keyboard key, or mask one key with tape.

- [ ] Tool reports `WARN` if fewer than 10 keys pressed
- [ ] Not `FAIL` — the point is to catch *undeclared* failures, not punish technician

### D3. Test without `--interactive` (confirm it's for batch only)

    sudo python3 /opt/itad-laptop/laptop_test.py --job-id TEST01 --no-interactive

- [ ] Screen / keyboard / speakers show `SKIP` or device-present `PASS`
- [ ] Note: **do not use this mode on real jobs**

## Part E — Full pipeline

### E1. One-command run

    sudo rm -rf /tmp/itad-run
    itad --job-id TEST02 --asset-id AST-TEST --tech tester --yes-i-mean-it

- [ ] Stage 1 (check) runs — may report `CLEAR` or `CANNOT CHECK` (drive is wiped, so check may say nothing to inspect)
- [ ] Stage 2 (wipe) runs — wipes the drive again
- [ ] Stage 3 (test) runs — all interactive prompts appear
- [ ] Pipeline summary at bottom shows all three stages
- [ ] `exit=0`

### E2. Certificate

    python3 /opt/itad-laptop/itad_report.py \
        --dir /tmp/itad-run --out /tmp/test_cert.html

- [ ] HTML file created
- [ ] Open it in Firefox — shows green `READY` verdict
- [ ] Certificate SHA-256 at bottom matches `certificate_sha256` from `laptop_wipe.json`

### E3. `--json -` clean stdout

    sudo python3 /opt/itad-laptop/laptop_check.py --json - 2>/dev/null \
        | python3 -m json.tool > /dev/null && echo "CLEAN"

- [ ] prints `CLEAN` (JSON parsed successfully, no human output mixed in)

## Part F — Optional: BLOCKED path

Only if you have a second laptop that is MDM-enrolled (or you can fake it).

### F1. Fake a BLOCKED check report

    cp /tmp/itad-run/laptop_check.json /tmp/fake_check.json
    python3 -c "
    import json
    d = json.load(open('/tmp/fake_check.json'))
    d['result'] = 'BLOCKED'
    json.dump(d, open('/tmp/fake_check.json','w'))
    "

    sudo python3 /opt/itad-laptop/laptop_wipe.py \
        --job-id TEST03 --check-report /tmp/fake_check.json --commit

- [ ] Prints `BLOCKED` and refusal message
- [ ] `exit=5`
- [ ] `--force` allows proceeding (but you don't have to actually do it)

## Sign-off

| Field | Value |
|---|---|
| USB serial / label | |
| Test laptop model | |
| Test laptop serial | |
| Tester name | |
| Date completed | |
| All items checked? | YES / NO |
| Any deviations? | |

**Do not process real laptops until every box above is checked and this table is signed.**

## If something fails

| Symptom | Fix |
|---|---|
| `MISS` in step A2 | `sudo apt install <pkg>` again, or run first_boot.sh |
| Wipe stays at 0% for >5 min | Drive may be failing. Check `dmesg | tail`. Try different drive. |
| `Purge unavailable` | Drive doesn't support secure erase. `clear-fallback` is still NIST Clear. |
| `SMART not read` | Drive too old / cable issue. Not a blocker unless drive is the product. |
| Screen test shows wrong colour | Monitor issue OR ANSI not supported. Report to dev. |
| Keyboard never registers | Try `evtest` manually. Some laptops need `i8042.nomux=1` boot param. |
| `certificate_sha256` missing | `laptop_wipe.py` failed before completion — check the JSON error field |
| Any unexpected exit code | Check `/tmp/itad-run/laptop_*.json` in a text editor — the `overall` field explains it |
