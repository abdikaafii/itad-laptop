#!/usr/bin/env python3
"""
laptop_check.py  -  ITAD Laptop Tool, STEP 1: pre-wipe check (v1.0.0)

READ ONLY. This file never wipes, formats or writes to any internal drive.
It only looks.

What it does:
  1. Reads make, model, serial number, BIOS, boot mode, internal drives
  2. Looks for signs of Absolute (Computrace) in the firmware and on Windows
  3. Looks for signs of enrolment (Autopilot, Intune, SCCM) on Windows
  4. Prints a clear result: CLEAR, BLOCKED or CANNOT CHECK
  5. Saves everything to a JSON file for the next steps (wipe, tests, report)

Exit codes:  0 = CLEAR   1 = BLOCKED   2 = CANNOT CHECK   3 = not run as root

Run (on the laptop, booted from the USB, in a terminal):
  sudo python3 /opt/itad-laptop/laptop_check.py --job-id J123 --asset-id A456 --tech Ali
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    NOW = datetime.now(ZoneInfo("Europe/Dublin"))
except Exception:
    NOW = datetime.now()

VERSION = "1.0.0"

BAD_SERIALS = {
    "", "none", "default string", "to be filled by o.e.m.", "system serial number",
    "not specified", "0", "n/a", "unknown", "o.e.m.", "oem",
}

# Words that point to Absolute inside firmware tables / BIOS text
ABSOLUTE_WORDS = [b"computrace", b"absolute software", b"absolute persistence"]

# Files the Absolute agent leaves on Windows
ABSOLUTE_FILES = [
    "Windows/System32/rpcnetp.exe",
    "Windows/System32/rpcnetp.dll",
    "Windows/System32/rpcnet.exe",
    "Windows/System32/rpcnet.dll",
    "Windows/SysWOW64/rpcnetp.exe",
    "Windows/SysWOW64/rpcnetp.dll",
]

# Files and folders that point to company enrolment on Windows
ENROLMENT_ITEMS = [
    ("Windows/ServiceState/Autopilot/AutopilotDDSZTDFile.json", "Autopilot profile file found"),
    ("Windows/Provisioning/Autopilot/AutopilotConfigurationFile.json", "Autopilot configuration file found"),
    ("ProgramData/Microsoft/IntuneManagementExtension", "Intune Management Extension data found"),
    ("Program Files (x86)/Microsoft Intune Management Extension", "Intune Management Extension installed"),
    ("Windows/CCM", "SCCM / Configuration Manager agent found"),
]


# ---------------------------------------------------------------- helpers
def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


def read_file(path):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return ""


def find_ci(root, relative):
    """Find a path inside root, ignoring upper/lower case. Returns the path or None."""
    current = root
    for part in relative.split("/"):
        try:
            names = os.listdir(current)
        except Exception:
            return None
        match = None
        for n in names:
            if n.lower() == part.lower():
                match = n
                break
        if match is None:
            return None
        current = os.path.join(current, match)
    return current


# ---------------------------------------------------------------- 1. identity
def get_identity():
    d = "/sys/class/dmi/id/"
    info = {
        "manufacturer": read_file(d + "sys_vendor"),
        "model": read_file(d + "product_name"),
        "version": read_file(d + "product_version"),
        "serial": read_file(d + "product_serial"),
        "board_serial": read_file(d + "board_serial"),
        "bios_vendor": read_file(d + "bios_vendor"),
        "bios_version": read_file(d + "bios_version"),
        "bios_date": read_file(d + "bios_date"),
        "boot_mode": "UEFI" if os.path.isdir("/sys/firmware/efi") else "Legacy BIOS",
    }
    if info["serial"].strip().lower() in BAD_SERIALS:
        rc, out, _ = run(["dmidecode", "-s", "system-serial-number"])
        if rc == 0 and out.strip().lower() not in BAD_SERIALS:
            info["serial"] = out.strip()
    if info["serial"].strip().lower() in BAD_SERIALS:
        info["serial"] = "UNKNOWN"
    return info


# ---------------------------------------------------------------- 2. disks
def to_bool(v):
    return v is True or str(v).strip() in ("1", "true", "True")


def scan_disks():
    """Returns (internal_disks, partitions_to_check, skipped_note)."""
    rc, out, _ = run(["lsblk", "-J", "-b", "-o",
                      "NAME,PATH,TYPE,FSTYPE,SIZE,RM,TRAN,MODEL,MOUNTPOINT"])
    internal, parts = [], []
    if rc != 0:
        return internal, parts
    try:
        data = json.loads(out)
    except Exception:
        return internal, parts

    def has_live_mount(node):
        mp = node.get("mountpoint") or ""
        if mp.startswith("/run/live") or mp.startswith("/usr/lib/live"):
            return True
        for c in node.get("children", []) or []:
            if has_live_mount(c):
                return True
        return False

    for disk in data.get("blockdevices", []):
        if disk.get("type") != "disk":
            continue
        name = disk.get("name", "")
        if name.startswith(("loop", "sr", "zram", "ram")):
            continue
        if to_bool(disk.get("rm")) or (disk.get("tran") or "") == "usb" or has_live_mount(disk):
            continue  # this is the boot USB or another external stick - never touched
        size_gb = round(int(disk.get("size") or 0) / 1e9, 1)
        internal.append({
            "path": disk.get("path"),
            "model": (disk.get("model") or "").strip(),
            "size_gb": size_gb,
            "tran": disk.get("tran") or "",
        })
        children = disk.get("children") or []
        if not children and disk.get("fstype"):
            parts.append({"path": disk.get("path"), "fstype": disk.get("fstype")})
        for c in children:
            if c.get("type") == "part":
                parts.append({"path": c.get("path"), "fstype": c.get("fstype") or ""})
    return internal, parts


def mount_ro(dev, mnt):
    """Mount READ ONLY. Tries the common NTFS drivers."""
    for cmd in (
        ["mount", "-t", "ntfs3", "-o", "ro", dev, mnt],
        ["mount", "-t", "ntfs-3g", "-o", "ro", dev, mnt],
        ["mount", "-o", "ro", dev, mnt],
    ):
        rc, _, _ = run(cmd)
        if rc == 0:
            return True
    return False


# ---------------------------------------------------------------- 3. checks
def check_absolute(windows_roots):
    signs = []

    # a) WPBT table: the door Absolute uses to start before Windows
    if os.path.exists("/sys/firmware/acpi/tables/WPBT"):
        signs.append("Firmware has a WPBT table (Absolute uses this to start with Windows)")

    # b) Words inside firmware tables
    for p in glob.glob("/sys/firmware/acpi/tables/*"):
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "rb") as f:
                data = f.read(4 * 1024 * 1024).lower()
        except Exception:
            continue
        for w in ABSOLUTE_WORDS:
            if w in data:
                signs.append("Word '%s' found in firmware table %s" % (w.decode(), os.path.basename(p)))
                break

    # c) UEFI variable names
    for p in glob.glob("/sys/firmware/efi/efivars/*"):
        n = os.path.basename(p).lower()
        if "absolute" in n or "computrace" in n:
            signs.append("UEFI variable found: " + os.path.basename(p))

    # d) BIOS text
    rc, out, _ = run(["dmidecode"])
    if rc == 0:
        low = out.lower().encode("utf-8", "ignore")
        for w in ABSOLUTE_WORDS:
            if w in low:
                signs.append("Word '%s' found in BIOS information" % w.decode())
                break

    # e) Absolute files on Windows
    for root in windows_roots:
        for rel in ABSOLUTE_FILES:
            if find_ci(root, rel):
                signs.append("Absolute agent file found on Windows: " + rel.replace("/", "\\"))

    return signs


def check_enrolment(windows_roots):
    signs = []
    for root in windows_roots:
        for rel, text in ENROLMENT_ITEMS:
            if find_ci(root, rel):
                signs.append("%s (%s)" % (text, rel.replace("/", "\\")))
    return signs


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="ITAD laptop pre-wipe check (read only)")
    ap.add_argument("--job-id", default="")
    ap.add_argument("--asset-id", default="")
    ap.add_argument("--tech", default="")
    ap.add_argument("--json", default="/tmp/laptop_check.json",
                    help="where to save the result for the next steps")
    args = ap.parse_args()

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("This check must run as root. Use:  sudo python3 laptop_check.py ...")
        sys.exit(3)

    ident = get_identity()
    internal, parts = scan_disks()

    tmp_base = tempfile.mkdtemp(prefix="itad_lc_")
    mounted = []
    windows_roots = []
    windows_found = []
    encrypted = []
    unreadable = []

    try:
        for i, p in enumerate(parts):
            fs = (p["fstype"] or "").lower()
            if fs == "bitlocker":
                encrypted.append("%s (BitLocker)" % p["path"])
            elif fs == "crypto_luks":
                encrypted.append("%s (LUKS)" % p["path"])
            elif fs in ("ntfs", "ntfs3"):
                mnt = os.path.join(tmp_base, "p%d" % i)
                os.makedirs(mnt, exist_ok=True)
                if mount_ro(p["path"], mnt):
                    mounted.append(mnt)
                    if find_ci(mnt, "Windows/System32"):
                        windows_roots.append(mnt)
                        windows_found.append(p["path"])
                else:
                    unreadable.append(p["path"])

        absolute_signs = check_absolute(windows_roots)
        enrol_signs = check_enrolment(windows_roots)
    finally:
        for mnt in mounted:
            run(["umount", mnt])
        shutil.rmtree(tmp_base, ignore_errors=True)

    # Decide the result
    if absolute_signs or enrol_signs:
        result = "BLOCKED"
        code = 1
    elif encrypted or unreadable:
        result = "CANNOT CHECK"
        code = 2
    else:
        result = "CLEAR"
        code = 0

    absolute_result = "BLOCKED" if absolute_signs else "CLEAR"
    enrol_result = "BLOCKED" if enrol_signs else ("CANNOT CHECK" if (encrypted or unreadable) else "CLEAR")

    report = {
        "tool": "laptop_check.py",
        "version": VERSION,
        "time": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        "job_id": args.job_id,
        "asset_id": args.asset_id,
        "technician": args.tech,
        "identity": ident,
        "internal_disks": internal,
        "windows_partitions": windows_found,
        "encrypted_partitions": encrypted,
        "unreadable_partitions": unreadable,
        "absolute_result": absolute_result,
        "absolute_signs": absolute_signs,
        "enrolment_result": enrol_result,
        "enrolment_signs": enrol_signs,
        "result": result,
    }
    try:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)
    except Exception as e:
        print("Could not save JSON: %s" % e)

    line = "=" * 56
    print(line)
    print(" ITAD LAPTOP TOOL - Step 1: Pre-wipe check  (v%s)" % VERSION)
    print(line)
    print(" Time        : %s (Irish time)" % report["time"])
    print(" Job ID      : %s" % (args.job_id or "-"))
    print(" Asset ID    : %s" % (args.asset_id or "-"))
    print(" Technician  : %s" % (args.tech or "-"))
    print("-" * 56)
    print(" Make        : %s" % (ident["manufacturer"] or "-"))
    print(" Model       : %s %s" % (ident["model"] or "-", ident["version"] if ident["version"] not in ("", ident["model"]) else ""))
    print(" Serial      : %s" % ident["serial"])
    print(" BIOS        : %s %s (%s)" % (ident["bios_vendor"], ident["bios_version"], ident["bios_date"]))
    print(" Boot mode   : %s" % ident["boot_mode"])
    if internal:
        for d in internal:
            print(" Drive       : %s  %s  %s GB" % (d["path"], d["model"] or "-", d["size_gb"]))
    else:
        print(" Drive       : none found")
    print("-" * 56)
    print(" ABSOLUTE CHECK  : %s" % absolute_result)
    for s in absolute_signs:
        print("    - %s" % s)
    print(" ENROLMENT CHECK : %s" % enrol_result)
    for s in enrol_signs:
        print("    - %s" % s)
    for s in encrypted:
        print("    - Drive is encrypted, cannot look inside: %s" % s)
    for s in unreadable:
        print("    - Could not open drive: %s" % s)
    if not windows_found:
        print("    (no Windows installation found on the internal drives)")
    print("-" * 56)
    print(" FINAL RESULT    : %s" % result)
    if result == "BLOCKED":
        print(" DO NOT WIPE. Tell your manager. Reasons are listed above.")
    elif result == "CANNOT CHECK":
        print(" DO NOT WIPE YET. A manager must decide.")
    else:
        print(" OK to continue to the wipe step.")
    print("-" * 56)
    print(" Note: this tool finds SIGNS only. It cannot read the BIOS")
    print(" Absolute setting. Always check the BIOS if you are unsure.")
    print(line)
    print(" Saved: %s" % args.json)

    sys.exit(code)


if __name__ == "__main__":
    main()
