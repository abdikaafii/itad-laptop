#!/usr/bin/env python3
"""
laptop_check.py - ITAD pre-wipe check (v2.1.0)  MULTI-PLATFORM

READ ONLY. Never wipes, formats, or writes to any internal drive.

Platforms:
  * Windows x86 PCs       -> FULL check (Absolute firmware + userspace,
                             Autopilot / Intune / SCCM enrolment)
  * Apple Macs            -> BEST EFFORT (needs apfs-fuse, FileVault off)
  * Chromebooks/boxes     -> CANNOT CHECK (inspect via ChromeOS UI)
  * Linux / unknown x86   -> FIRMWARE ONLY

The tool NEVER reports CLEAR unless the primary OS was successfully
inspected AND found clean. Anything it cannot inspect is CANNOT CHECK.

Exit codes:  0 = CLEAR   1 = BLOCKED   2 = CANNOT CHECK   3 = not run as root
"""

import argparse
import glob
import json
import os
import re
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

VERSION = "2.1.0"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BAD_SERIALS = {
    "", "none", "default string", "to be filled by o.e.m.",
    "to be filled by o.e.m", "system serial number", "not specified",
    "0", "n/a", "unknown", "o.e.m.", "oem", "chassis serial number",
    "0123456789", "1234567890",
}

ABSOLUTE_WORDS = [b"computrace", b"absolute software", b"absolute persistence"]

WIN_ABSOLUTE_FILES = [
    "Windows/System32/rpcnetp.exe",
    "Windows/System32/rpcnetp.dll",
    "Windows/System32/rpcnet.exe",
    "Windows/System32/rpcnet.dll",
    "Windows/System32/CTDvTracking*.dll",
    "Windows/SysWOW64/rpcnetp.exe",
    "Windows/SysWOW64/rpcnetp.dll",
    "Windows/SysWOW64/CTDvTracking*.dll",
    "Program Files/Absolute Software",
    "Program Files (x86)/Absolute Software",
    "ProgramData/Absolute Software",
]

# Autopilot / MDM / SCCM file-level indicators
WIN_ENROLMENT_ITEMS = [
    ("Windows/ServiceState/Autopilot/AutopilotDDSZTDFile.json",
     "Autopilot provisioning profile (DDSZTD)"),
    ("Windows/Provisioning/Autopilot/AutopilotConfigurationFile.json",
     "Autopilot self-deploying profile"),
    ("Windows/System32/LogFiles/AutoPilot",
     "Autopilot log folder (past or current provisioning)"),
    ("Windows/System32/LogFiles/AutoPilot/Provisioning",
     "Autopilot provisioning logs"),
    ("ProgramData/Microsoft/IntuneManagementExtension",
     "Intune Management Extension (data)"),
    ("Program Files/Microsoft Intune Management Extension",
     "Intune Management Extension (x64)"),
    ("Program Files (x86)/Microsoft Intune Management Extension",
     "Intune Management Extension (x86)"),
    ("Windows/CCM", "SCCM / Configuration Manager agent"),
]

# Registry raw-byte hints — Autopilot-specific first
WIN_AUTOPILOT_REG_HINTS = [
    (b"Provisioning\\Diagnostics\\Autopilot",
     "Autopilot diagnostics registry key (past or current enrollment)"),
    (b"Provisioning\\AutopilotSettings",
     "Autopilot settings registry key"),
    (b"CloudAssignedTenantId",
     "Autopilot tenant ID string in registry"),
    (b"CloudAssignedDeviceName",
     "Autopilot cloud device-name assignment"),
    (b"AutopilotDeviceRemediation",
     "Autopilot remediation state"),
]

WIN_MDM_REG_HINTS = [
    (b"Microsoft\\Enrollments\\",
     "MDM enrollment key (any MDM — Intune or third-party)"),
    (b"EnterpriseDesktopAppManagement",
     "Intune Win32 app delivery key"),
    (b"Microsoft\\Windows\\CurrentVersion\\MDM",
     "Legacy MDM registry key"),
    (b"CCM\\", "SCCM key in hive"),
    (b"IntuneManagementExtension", "Intune service key"),
]

WIN_REGISTRY_HINTS = [
    ("Windows/System32/config/SOFTWARE", "software"),
    ("Windows/System32/config/SYSTEM", "system"),
]

MAC_ABSOLUTE_PATHS = [
    "Library/Application Support/Absolute",
    "Library/Application Support/Absolute Software",
    "Library/LaunchDaemons/com.absolute.rpcnet.plist",
    "Library/LaunchDaemons/com.absolute.agent.plist",
    "Applications/Absolute",
    "usr/local/absolute",
]

MAC_ENROLMENT_ITEMS = [
    ("Library/Preferences/com.apple.MDM.plist", "MDM preferences file"),
    ("var/db/ConfigurationProfiles", "ConfigurationProfiles folder (MDM)"),
    ("Library/Managed Preferences", "Managed Preferences folder"),
]

LINUX_ABSOLUTE_PATHS = [
    "opt/absolute", "usr/local/absolute",
    "etc/absolute", "var/lib/absolute",
]

ENCRYPTED_FS = {"bitlocker", "crypto_luks", "filevault"}
LIVE_MOUNT_PREFIXES = (
    "/run/live", "/usr/lib/live", "/rofs", "/cdrom", "/lib/live", "/live",
)

ENROLMENT_CAVEAT = (
    "Enrolment check is DISK-ONLY. A laptop whose hardware hash is "
    "registered in Autopilot / Intune / ABM but never powered-on will show "
    "as CLEAR here. Before sale, verify the serial in the Intune / ABM "
    "portal, or in Windows run 'dsregcmd /status' and look for "
    "AzureAdJoined : YES, EnterpriseJoined : YES, DomainJoined : YES."
)

# ---------------------------------------------------------------------------
# Subprocess + IO helpers
# ---------------------------------------------------------------------------
def run(cmd, timeout=30):
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e).splitlines()[0] if str(e) else "error"


def read_file(path):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return ""


def read_bytes(path, max_bytes=8 * 1024 * 1024):
    try:
        with open(path, "rb") as f:
            return f.read(max_bytes)
    except Exception:
        return b""


def _fnmatch_ci(name, pattern):
    import fnmatch
    return fnmatch.fnmatch(name.lower(), pattern.lower())


def find_ci(root, relative):
    """Case-insensitive path lookup; trailing * glob supported on last segment."""
    if "*" in relative:
        head, tail = (relative.rsplit("/", 1) if "/" in relative
                      else ("", relative))
        base = find_ci(root, head) if head else root
        if not base:
            return None
        try:
            for n in os.listdir(base):
                if _fnmatch_ci(n, tail):
                    return os.path.join(base, n)
        except Exception:
            return None
        return None
    current = root
    for part in relative.split("/"):
        if not part:
            continue
        try:
            names = os.listdir(current)
        except Exception:
            return None
        match = next((n for n in names if n.lower() == part.lower()), None)
        if match is None:
            return None
        current = os.path.join(current, match)
    return current


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------
def detect_platform():
    vendor = read_file("/sys/class/dmi/id/sys_vendor").lower()
    board = read_file("/sys/class/dmi/id/board_vendor").lower()
    product = read_file("/sys/class/dmi/id/product_name").lower()
    machine = os.uname().machine

    if "apple" in vendor or "apple" in board:
        return "mac"
    if "google" in vendor or "chromebook" in product or "chromebox" in product:
        return "chromebook"
    if "cros" in read_file("/proc/cmdline").lower():
        return "chromebook"
    if os.path.isdir("/sys/class/dmi/id") and machine in ("x86_64", "i686", "i386"):
        return "pc"
    if machine in ("aarch64", "arm64"):
        return "arm_unknown"
    return "unknown"


PLATFORM_LABEL = {
    "pc": "Windows / x86 PC",
    "mac": "Apple Mac",
    "chromebook": "Chromebook / Chromebox",
    "arm_unknown": "ARM device (unknown)",
    "unknown": "Unknown / Linux",
}


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def _secure_boot_state():
    try:
        if not os.path.isdir("/sys/firmware/efi"):
            return "N/A (Legacy)"
        matches = glob.glob("/sys/firmware/efi/efivars/SecureBoot-*")
        if matches:
            data = read_bytes(matches[0], 8)
            if len(data) >= 5:
                return "Enabled" if data[4] == 1 else "Disabled"
        return "Unknown"
    except Exception:
        return "Unknown"


def get_identity():
    d = "/sys/class/dmi/id/"
    info = {
        "manufacturer": read_file(d + "sys_vendor"),
        "model": read_file(d + "product_name"),
        "version": read_file(d + "product_version"),
        "serial": read_file(d + "product_serial"),
        "board_serial": read_file(d + "board_serial"),
        "uuid": read_file(d + "product_uuid"),
        "bios_vendor": read_file(d + "bios_vendor"),
        "bios_version": read_file(d + "bios_version"),
        "bios_date": read_file(d + "bios_date"),
        "boot_mode": "UEFI" if os.path.isdir("/sys/firmware/efi") else "Legacy BIOS",
        "secure_boot": _secure_boot_state(),
    }
    if info["serial"].strip().lower() in BAD_SERIALS:
        rc, out, _ = run(["dmidecode", "-s", "system-serial-number"])
        if rc == 0 and out.strip().lower() not in BAD_SERIALS:
            info["serial"] = out.strip()
    if info["serial"].strip().lower() in BAD_SERIALS:
        info["serial"] = "UNKNOWN"
    return info


# ---------------------------------------------------------------------------
# Disk scanning (recursive: RAID, LVM, crypt, part)
# ---------------------------------------------------------------------------
def _to_bool(v):
    return v is True or str(v).strip() in ("1", "true", "True")


def _boot_parent_disk():
    try:
        with open("/proc/mounts") as f:
            for line in f:
                p = line.split()
                if len(p) >= 2 and p[1] == "/":
                    return re.sub(
                        r"(/dev/(?:nvme\d+n\d+|mmcblk\d+|sd[a-z]+|"
                        r"vd[a-z]+|hd[a-z]+|xvd[a-z]+))p?\d*$", r"\1", p[0])
    except Exception:
        pass
    return None


def _has_live_mount(node):
    mp = node.get("mountpoint") or ""
    if any(mp.startswith(p) for p in LIVE_MOUNT_PREFIXES):
        return True
    return any(_has_live_mount(c) for c in (node.get("children") or []))


def scan_disks():
    """Returns (internal_disks, partitions, notes)."""
    rc, out, _ = run(["lsblk", "-J", "-b", "-o",
                      "NAME,PATH,TYPE,FSTYPE,SIZE,RM,TRAN,MODEL,MOUNTPOINT"])
    internal, parts, notes = [], [], []
    if rc != 0:
        notes.append("lsblk failed; cannot enumerate block devices")
        return internal, parts, notes
    try:
        data = json.loads(out)
    except Exception:
        notes.append("lsblk output unparsable")
        return internal, parts, notes

    boot_dev = _boot_parent_disk()

    def _skip(disk):
        name = disk.get("name", "")
        if name.startswith(("loop", "sr", "zram", "ram", "dm-")):
            return True
        if disk.get("path") == boot_dev:
            return True
        if _to_bool(disk.get("rm")) or (disk.get("tran") or "") == "usb":
            if int(disk.get("size") or 0) < 32_000_000_000:
                return True
        return _has_live_mount(disk)

    def _walk(node):
        for c in node.get("children") or []:
            t = c.get("type", "")
            if t in ("part", "lvm", "crypt", "raid0", "raid1", "raid4",
                     "raid5", "raid6", "raid10", "mpath", "dm"):
                parts.append({"path": c.get("path"),
                              "fstype": (c.get("fstype") or "").lower()})
            _walk(c)

    for disk in data.get("blockdevices", []):
        if disk.get("type") != "disk" or _skip(disk):
            continue
        internal.append({
            "path": disk.get("path"),
            "model": (disk.get("model") or "").strip(),
            "size_gb": round(int(disk.get("size") or 0) / 1e9, 1),
            "tran": disk.get("tran") or "",
        })
        if disk.get("fstype"):
            parts.append({"path": disk.get("path"),
                          "fstype": (disk.get("fstype") or "").lower()})
        _walk(disk)

    seen, unique = set(), []
    for p in parts:
        if p["path"] in seen:
            continue
        seen.add(p["path"])
        unique.append(p)
    return internal, unique, notes


# ---------------------------------------------------------------------------
# Encryption signatures + mount
# ---------------------------------------------------------------------------
def _has_bitlocker_signature(dev):
    try:
        with open(dev, "rb") as f:
            head = f.read(4096)
            try:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 4096))
                tail = f.read(4096)
            except Exception:
                tail = b""
        return b"-FVE-FS-" in head or b"-FVE-FS-" in tail
    except Exception:
        return False


def _has_luks_signature(dev):
    try:
        with open(dev, "rb") as f:
            head = f.read(8)
        return head[:4] == b"LUKS" or head[:6] == b"LUKS\xba\xbe"
    except Exception:
        return False


def mount_ro(dev, mnt, fstype):
    if fstype in ("ntfs", "ntfs3"):
        candidates = [
            ["mount", "-t", "ntfs-3g",
             "-o", "ro,noload,nodev,nosuid,noexec", dev, mnt],
            ["mount", "-t", "ntfs3",
             "-o", "ro,nodev,nosuid,noexec", dev, mnt],
            ["mount", "-o", "ro,nodev,nosuid,noexec", dev, mnt],
        ]
    elif fstype == "apfs":
        candidates = []
        if shutil.which("apfs-fuse"):
            candidates.append(["apfs-fuse", "-o", "ro", dev, mnt])
        candidates.append(
            ["mount", "-t", "apfs", "-o", "ro,nodev,nosuid,noexec", dev, mnt])
    elif fstype in ("hfs", "hfs+"):
        candidates = [
            ["mount", "-t", "hfsplus", "-o", "ro,nodev,nosuid,noexec", dev, mnt],
            ["mount", "-t", "hfs",     "-o", "ro,nodev,nosuid,noexec", dev, mnt],
        ]
    else:
        candidates = [["mount", "-o", "ro,nodev,nosuid,noexec", dev, mnt]]

    for cmd in candidates:
        rc, _, _ = run(cmd, timeout=20)
        if rc == 0 or os.path.ismount(mnt):
            return True
    return False


def unmount(mnt):
    rc, _, _ = run(["umount", mnt], timeout=10)
    if rc != 0:
        run(["umount", "-l", mnt], timeout=10)


# ---------------------------------------------------------------------------
# Firmware Absolute check
# ---------------------------------------------------------------------------
def check_firmware_absolute():
    signs, warnings = [], []
    wpbt = os.path.exists("/sys/firmware/acpi/tables/WPBT")

    words_hit = []
    if os.path.isdir("/sys/firmware/acpi/tables"):
        for p in glob.glob("/sys/firmware/acpi/tables/*"):
            if not os.path.isfile(p):
                continue
            data = read_bytes(p, 4 * 1024 * 1024).lower()
            for w in ABSOLUTE_WORDS:
                if w in data:
                    words_hit.append((w.decode(), os.path.basename(p)))
                    break

    efi_vars = []
    if os.path.isdir("/sys/firmware/efi/efivars"):
        for p in glob.glob("/sys/firmware/efi/efivars/*"):
            n = os.path.basename(p).lower()
            if "absolute" in n or "computrace" in n:
                efi_vars.append(os.path.basename(p))

    bios_hit = []
    rc, out, _ = run(["dmidecode"], timeout=20)
    if rc == 0:
        low = out.lower().encode("utf-8", "ignore")
        for w in ABSOLUTE_WORDS:
            if w in low:
                bios_hit.append(w.decode())
                break

    for w, table in words_hit:
        signs.append("Word '%s' found in firmware table %s" % (w, table))
    for v in efi_vars:
        signs.append("UEFI variable: %s" % v)
    for w in bios_hit:
        signs.append("Word '%s' found in BIOS/DMI strings" % w)

    if wpbt:
        if signs:
            signs.append("WPBT ACPI table also present")
        else:
            warnings.append(
                "WPBT ACPI table present but no Absolute strings — "
                "verify Absolute is disabled in BIOS")
    return signs, warnings


# ---------------------------------------------------------------------------
# Windows userspace checks
# ---------------------------------------------------------------------------
def check_windows_absolute(root):
    return ["Absolute agent file: " + rel.replace("/", "\\")
            for rel in WIN_ABSOLUTE_FILES if find_ci(root, rel)]


def _parse_autopilot_profile(path):
    """Extract tenant + serial from the Autopilot profile blob. Safe on junk."""
    try:
        with open(path, "r", errors="replace") as f:
            raw = f.read(256 * 1024)
        data = json.loads(raw)
    except Exception:
        return {}
    def _find(d, *keys):
        for k in keys:
            if isinstance(d, dict) and k in d:
                return d[k]
        return None
    sub = data.get("OobeSettings", {}) if isinstance(data, dict) else {}
    device = data.get("DeviceInfo", {}) if isinstance(data, dict) else {}
    tenant = (_find(data, "CloudAssignedTenantId", "TenantId")
              or _find(sub, "CloudAssignedTenantId"))
    serial = (_find(data, "CloudAssignedSerialNumber", "SerialNumber")
              or _find(device, "SerialNumber"))
    name = _find(data, "CloudAssignedDeviceName", "DeviceName")
    return {"tenant_id": tenant, "serial": serial, "device_name": name}


def check_windows_enrolment(root):
    """Returns (signs_list, details_dict)."""
    signs = []
    details = {"autopilot_profiles": [], "mdm_keys": []}

    for rel, label in WIN_ENROLMENT_ITEMS:
        hit = find_ci(root, rel)
        if not hit:
            continue
        signs.append("%s (%s)" % (label, rel.replace("/", "\\")))
        if rel.lower().endswith(".json"):
            parsed = _parse_autopilot_profile(hit)
            if any(parsed.values()):
                details["autopilot_profiles"].append(parsed)
                if parsed.get("tenant_id"):
                    signs.append("    -> Autopilot tenant: %s"
                                 % parsed["tenant_id"])
                if parsed.get("device_name"):
                    signs.append("    -> Assigned name: %s"
                                 % parsed["device_name"])
                if parsed.get("serial"):
                    signs.append("    -> Profile serial: %s"
                                 % parsed["serial"])

    for hive_rel, _label in WIN_REGISTRY_HINTS:
        hive = find_ci(root, hive_rel)
        if not hive:
            continue
        data = read_bytes(hive).lower()
        for needle, label in WIN_AUTOPILOT_REG_HINTS:
            if needle.lower() in data:
                signs.append(label)
        for needle, label in WIN_MDM_REG_HINTS:
            if needle.lower() in data:
                signs.append(label)
                details["mdm_keys"].append(label)

    return signs, details


# ---------------------------------------------------------------------------
# Mac / Linux userspace checks
# ---------------------------------------------------------------------------
def check_mac_absolute(root):
    return ["Absolute path: " + rel
            for rel in MAC_ABSOLUTE_PATHS if find_ci(root, rel)]


def check_mac_enrolment(root):
    return ["%s (%s)" % (label, rel)
            for rel, label in MAC_ENROLMENT_ITEMS if find_ci(root, rel)]


def check_linux_absolute(root):
    return ["Absolute path (Linux): " + rel
            for rel in LINUX_ABSOLUTE_PATHS if find_ci(root, rel)]


# ---------------------------------------------------------------------------
# JSON output path
# ---------------------------------------------------------------------------
def _default_json_path():
    for cand in ("/run/live/medium", "/cdrom", "/media/usb", "/mnt/usb"):
        if os.path.isdir(cand) and os.access(cand, os.W_OK):
            return os.path.join(cand, "laptop_check.json")
    return "/tmp/laptop_check.json"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="ITAD pre-wipe check (read only, multi-platform)")
    ap.add_argument("--job-id", default="")
    ap.add_argument("--asset-id", default="")
    ap.add_argument("--tech", default="")
    ap.add_argument("--json", default="",
                    help="JSON output path, or '-' for stdout")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("This check must run as root. Use:  sudo python3 "
              "laptop_check.py ...")
        sys.exit(3)

    if not args.json:
        args.json = _default_json_path()

    platform = detect_platform()
    ident = get_identity()
    internal, parts, disk_notes = scan_disks()

    tmp_base = tempfile.mkdtemp(prefix="itad_lc_")
    mounted = []
    unreadable, encrypted = [], []
    win_roots, mac_roots = [], []
    win_partitions, mac_partitions = [], []

    abs_signs, enrol_signs, warnings = [], [], []
    enrol_details = {"autopilot_profiles": [], "mdm_keys": []}

    try:
        fw_signs, fw_warnings = check_firmware_absolute()
        abs_signs.extend(fw_signs)
        warnings.extend(fw_warnings)

        for i, p in enumerate(parts):
            dev = p["path"]
            fs = p["fstype"] or ""

            if fs in ENCRYPTED_FS:
                encrypted.append("%s (%s)" % (dev, fs))
                continue
            if _has_bitlocker_signature(dev):
                encrypted.append("%s (BitLocker signature)" % dev)
                continue
            if _has_luks_signature(dev):
                encrypted.append("%s (LUKS signature)" % dev)
                continue

            if platform == "pc":
                try_fs = {"ntfs", "ntfs3", "fat32", "vfat", "exfat",
                          "ext4", "btrfs", "xfs"}
                if fs:
                    try_fs.add(fs)
            elif platform == "mac":
                try_fs = {"apfs", "hfs", "hfs+"}
            elif platform == "chromebook":
                try_fs = set()
            else:
                try_fs = {"ntfs", "ntfs3", "ext4", "btrfs", "xfs",
                          "fat32", "vfat"}

            if fs not in try_fs:
                continue

            mnt = os.path.join(tmp_base, "p%d" % i)
            os.makedirs(mnt, exist_ok=True)
            if not mount_ro(dev, mnt, fs):
                unreadable.append("%s (fs=%s)" % (dev, fs or "?"))
                continue
            mounted.append(mnt)

            if platform == "pc" and find_ci(mnt, "Windows/System32"):
                win_roots.append(mnt)
                win_partitions.append(dev)
                abs_signs.extend(check_windows_absolute(mnt))
                _signs, _det = check_windows_enrolment(mnt)
                enrol_signs.extend(_signs)
                for k, v in _det.items():
                    enrol_details.setdefault(k, []).extend(v)
            elif platform == "mac":
                if find_ci(mnt, "Library") or find_ci(mnt, "System/Library"):
                    mac_roots.append(mnt)
                    mac_partitions.append(dev)
                    abs_signs.extend(check_mac_absolute(mnt))
                    enrol_signs.extend(check_mac_enrolment(mnt))
            elif platform == "unknown":
                abs_signs.extend(check_linux_absolute(mnt))

    finally:
        for mnt in mounted:
            unmount(mnt)
        shutil.rmtree(tmp_base, ignore_errors=True)

    # ------------------------------------------------------------------
    # Decide result
    # ------------------------------------------------------------------
    os_checked = ((platform == "pc" and win_roots)
                  or (platform == "mac" and mac_roots))
    blocked = bool(abs_signs or enrol_signs)

    cannot = list(encrypted) + list(unreadable) + list(disk_notes)

    if platform == "chromebook":
        cannot.append(
            "ChromeOS device — check 'Settings > About ChromeOS > "
            "Additional details > Enterprise enrollment', or run 'vpd -l'")
    if platform == "mac" and not os_checked:
        cannot.append(
            "Mac — no APFS/HFS volume could be read "
            "(FileVault on, or apfs-fuse not installed). "
            "Use macOS Recovery or Apple Business Manager.")
    if platform == "pc" and not os_checked:
        cannot.append("No Windows volume could be inspected on internal drives")
    if platform == "unknown":
        cannot.append("Unrecognised platform — only firmware strings checked")
    if not internal:
        cannot.append("No internal drives detected")

    if blocked:
        result, code = "BLOCKED", 1
    elif cannot:
        result, code = "CANNOT CHECK", 2
    else:
        result, code = "CLEAR", 0

    # Three-state ABSOLUTE
    if abs_signs:
        abs_result = "BLOCKED"
    elif os_checked and not cannot:
        abs_result = "CLEAR"
    else:
        abs_result = "CANNOT CHECK"

    # Three-state ENROLMENT
    if enrol_signs:
        enrol_result = "BLOCKED"
    elif os_checked and platform == "pc":
        enrol_result = "CLEAR (disk only)"
    else:
        enrol_result = "CANNOT CHECK"

    report = {
        "tool": "laptop_check.py", "version": VERSION,
        "time": NOW.isoformat(), "platform": platform,
        "job_id": args.job_id, "asset_id": args.asset_id,
        "technician": args.tech, "identity": ident,
        "internal_disks": internal,
        "windows_partitions": win_partitions,
        "mac_partitions": mac_partitions,
        "encrypted_partitions": encrypted,
        "unreadable_partitions": unreadable,
        "notes": cannot,
        "absolute_result": abs_result,
        "absolute_signs": abs_signs,
        "enrolment_result": enrol_result,
        "enrolment_signs": enrol_signs,
        "enrolment_details": enrol_details,
        "enrolment_caveat": ENROLMENT_CAVEAT,
        "warnings": warnings,
        "result": result,
    }

    if args.json == "-":
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        try:
            with open(args.json, "w") as f:
                json.dump(report, f, indent=2)
        except Exception as e:
            print("Could not save JSON: %s" % e)

    # ------------------------------------------------------------------
    # Human output
    # ------------------------------------------------------------------
    line = "=" * 60
    print(line)
    print(" ITAD PRE-WIPE CHECK  v%s  (READ ONLY)" % VERSION)
    print(line)
    print(" Time        : %s" % report["time"])
    print(" Job ID      : %s" % (args.job_id or "-"))
    print(" Asset ID    : %s" % (args.asset_id or "-"))
    print(" Technician  : %s" % (args.tech or "-"))
    print(" Platform    : %s" % PLATFORM_LABEL.get(platform, platform))
    print("-" * 60)
    print(" Make        : %s" % (ident["manufacturer"] or "-"))
    model_line = ident["model"] or "-"
    if ident["version"] and ident["version"] != ident["model"]:
        model_line += " " + ident["version"]
    print(" Model       : %s" % model_line)
    print(" Serial      : %s" % ident["serial"])
    print(" BIOS        : %s %s (%s)" % (
        ident["bios_vendor"], ident["bios_version"], ident["bios_date"]))
    print(" Boot mode   : %s  SecureBoot: %s" % (
        ident["boot_mode"], ident["secure_boot"]))
    if internal:
        for d in internal:
            print(" Drive       : %s  %s  %s GB" % (
                d["path"], d["model"] or "-", d["size_gb"]))
    else:
        print(" Drive       : none found")
    print("-" * 60)
    print(" ABSOLUTE CHECK    : %s" % abs_result)
    for s in abs_signs:
        print("    - %s" % s)
    for s in warnings:
        print("    (i) %s" % s)
    print(" ENROLMENT CHECK   : %s" % enrol_result)
    for s in enrol_signs:
        print("    - %s" % s)
    if enrol_details.get("autopilot_profiles"):
        print("    ! Autopilot profile data:")
        for ap in enrol_details["autopilot_profiles"]:
            print("        tenant=%s  device=%s  serial=%s" % (
                ap.get("tenant_id"), ap.get("device_name"), ap.get("serial")))
    for s in encrypted:
        print("    - Encrypted volume, cannot inspect: %s" % s)
    for s in unreadable:
        print("    - Could not mount: %s" % s)
    for s in cannot:
        if s in encrypted or s in unreadable:
            continue
        print("    - %s" % s)
    print("-" * 60)
    print(" FINAL RESULT      : %s" % result)
    if result == "BLOCKED":
        print(" DO NOT WIPE. Escalate. Reasons listed above.")
    elif result == "CANNOT CHECK":
        print(" DO NOT WIPE YET. A manager must decide (or inspect manually).")
    else:
        print(" OK to continue to the wipe step — but read the caveat below.")
    print("-" * 60)
    print(" ! IMPORTANT — ENROLMENT CAVEAT")
    for ln in _wrap(ENROLMENT_CAVEAT, 58):
        print("   %s" % ln)
    print("-" * 60)
    print(" Note: this tool finds SIGNS only. It cannot read the BIOS")
    print(" Absolute setting. Always verify in the BIOS if unsure.")
    print(line)
    if args.json != "-":
        print(" Saved: %s" % args.json)

    sys.exit(code)


def _wrap(text, width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


if __name__ == "__main__":
    main()