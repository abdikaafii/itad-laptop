#!/usr/bin/env python3
"""
laptop_check.py - ITAD pre-wipe check (v3.0.0)  MULTI-PLATFORM
READ ONLY. Never wipes or writes to any internal drive.
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, tempfile
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    NOW = datetime.now(ZoneInfo("Europe/Dublin"))
except Exception:
    NOW = datetime.now()

VERSION = "3.0.0"

BAD_SERIALS = {
    "", "none", "default string", "to be filled by o.e.m.",
    "to be filled by o.e.m", "system serial number", "not specified",
    "0", "n/a", "unknown", "o.e.m.", "oem", "chassis serial number",
    "0123456789", "1234567890",
}

ABSOLUTE_WORDS = [b"computrace", b"absolute software", b"absolute persistence"]

WIN_ABSOLUTE_FILES = [
    "Windows/System32/rpcnetp.exe", "Windows/System32/rpcnetp.dll",
    "Windows/System32/rpcnet.exe", "Windows/System32/rpcnet.dll",
    "Windows/System32/CTDvTracking*.dll",
    "Windows/SysWOW64/rpcnetp.exe", "Windows/SysWOW64/rpcnetp.dll",
    "Windows/SysWOW64/CTDvTracking*.dll",
    "Program Files/Absolute Software",
    "Program Files (x86)/Absolute Software",
    "ProgramData/Absolute Software",
]

# (path, substate, description, parse_json)
AUTOPILOT_ITEMS = [
    ("Windows/ServiceState/Autopilot/AutopilotDDSZTDFile.json",
     "ACTIVE", "Autopilot provisioning profile (DDSZTD)", True),
    ("Windows/Provisioning/Autopilot/AutopilotConfigurationFile.json",
     "ACTIVE", "Autopilot self-deploying profile", True),
    ("Windows/Provisioning/Autopilot",
     "ACTIVE", "Autopilot provisioning folder", False),
    ("Windows/System32/LogFiles/AutoPilot",
     "PAST", "Autopilot log folder", False),
    ("Windows/System32/LogFiles/AutoPilot/Provisioning",
     "PAST", "Autopilot provisioning logs", False),
    ("ProgramData/Microsoft/IntuneManagementExtension",
     "ACTIVE", "Intune Management Extension (data)", False),
    ("Program Files/Microsoft Intune Management Extension",
     "ACTIVE", "Intune Management Extension (x64)", False),
    ("Program Files (x86)/Microsoft Intune Management Extension",
     "ACTIVE", "Intune Management Extension (x86)", False),
    ("Windows/CCM",
     "ACTIVE", "SCCM / Configuration Manager agent", False),
    ("Windows/System32/Tasks/Microsoft/Windows/EnterpriseMgmt",
     "ACTIVE", "MDM scheduled tasks", False),
    ("Windows/System32/Tasks/Microsoft/Windows/Autopilot",
     "ACTIVE", "Autopilot scheduled tasks", False),
]

AUTOPILOT_REG_HINTS = [
    (b"Provisioning\\Diagnostics\\Autopilot", "Autopilot diagnostics registry key"),
    (b"Provisioning\\AutopilotSettings", "Autopilot settings registry key"),
    (b"CloudAssignedTenantId", "Autopilot tenant assignment"),
    (b"CloudAssignedDeviceName", "Autopilot device name assignment"),
    (b"CloudAssignedOobeConfig", "Autopilot OOBE configuration"),
    (b"CloudAssignedDomainJoinMethod", "Autopilot domain join method"),
    (b"AutopilotDeviceRemediation", "Autopilot remediation state"),
]

MDM_REG_HINTS = [
    (b"Microsoft\\Enrollments\\", "MDM enrollment registry key"),
    (b"Microsoft\\Windows\\CurrentVersion\\MDM", "Legacy MDM key"),
    (b"EnterpriseDesktopAppManagement", "Intune Win32 app management"),
    (b"EnterpriseResourceManager", "Intune resource manager"),
    (b"Microsoft\\Provisioning\\OMADM", "OMA-DM provisioning"),
    (b"CCM\\", "SCCM registry key"),
]

AZURE_AD_ITEMS = [
    ("ProgramData/Microsoft/IdentityCRL", "Azure AD token cache"),
    ("Windows/System32/config/systemprofile/AppData/Local/Microsoft/IdentityCache",
     "Identity cache"),
    ("Windows/System32/config/systemprofile/AppData/Local/Microsoft/Windows/CloudExperienceHost",
     "Cloud Experience Host data"),
]

REGISTRY_HIVES = [
    "Windows/System32/config/SOFTWARE",
    "Windows/System32/config/SYSTEM",
]

MAC_ABSOLUTE_PATHS = [
    "Library/Application Support/Absolute",
    "Library/Application Support/Absolute Software",
    "Library/LaunchDaemons/com.absolute.rpcnet.plist",
    "Library/LaunchDaemons/com.absolute.agent.plist",
    "Applications/Absolute", "usr/local/absolute",
]
MAC_ENROLMENT_ITEMS = [
    ("Library/Preferences/com.apple.MDM.plist", "MDM preferences file"),
    ("var/db/ConfigurationProfiles", "ConfigurationProfiles folder (MDM)"),
    ("Library/Managed Preferences", "Managed Preferences folder"),
]
LINUX_ABSOLUTE_PATHS = ["opt/absolute", "usr/local/absolute",
                        "etc/absolute", "var/lib/absolute"]

ENCRYPTED_FS = {"bitlocker", "crypto_luks", "filevault"}
LIVE_MOUNT_PREFIXES = ("/run/live", "/usr/lib/live", "/rofs", "/cdrom",
                       "/lib/live", "/live")


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


def read_file(p):
    try:
        return open(p, "r", errors="replace").read().strip()
    except Exception:
        return ""


def read_bytes(p, n=8 * 1024 * 1024):
    try:
        return open(p, "rb").read(n)
    except Exception:
        return b""


def _fnmatch_ci(name, pat):
    import fnmatch
    return fnmatch.fnmatch(name.lower(), pat.lower())


def find_ci(root, relative):
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
    cur = root
    for part in relative.split("/"):
        if not part:
            continue
        try:
            names = os.listdir(cur)
        except Exception:
            return None
        match = next((n for n in names if n.lower() == part.lower()), None)
        if not match:
            return None
        cur = os.path.join(cur, match)
    return cur


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
    return "unknown"


PLATFORM_LABEL = {
    "pc": "Windows / x86 PC",
    "mac": "Apple Mac",
    "chromebook": "Chromebook / Chromebox",
    "unknown": "Unknown / Linux",
}


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
        "secure_boot": _secure_boot(),
    }
    if info["serial"].lower() in BAD_SERIALS:
        rc, out, _ = run(["dmidecode", "-s", "system-serial-number"])
        if rc == 0 and out.strip().lower() not in BAD_SERIALS:
            info["serial"] = out.strip()
    if info["serial"].lower() in BAD_SERIALS:
        info["serial"] = "UNKNOWN"
    return info


def _secure_boot():
    try:
        if not os.path.isdir("/sys/firmware/efi"):
            return "N/A (Legacy)"
        hits = glob.glob("/sys/firmware/efi/efivars/SecureBoot-*")
        if hits:
            d = read_bytes(hits[0], 8)
            if len(d) >= 5:
                return "Enabled" if d[4] == 1 else "Disabled"
    except Exception:
        pass
    return "Unknown"


def _boot_parent_disk():
    try:
        for line in open("/proc/mounts"):
            p = line.split()
            if len(p) >= 2 and p[1] == "/":
                return re.sub(
                    r"(/dev/(?:nvme\d+n\d+|mmcblk\d+|sd[a-z]+|"
                    r"vd[a-z]+|hd[a-z]+))p?\d*$", r"\1", p[0])
    except Exception:
        pass
    return None


def _has_live_mount(node):
    mp = node.get("mountpoint") or ""
    if any(mp.startswith(p) for p in LIVE_MOUNT_PREFIXES):
        return True
    return any(_has_live_mount(c) for c in (node.get("children") or []))


def scan_disks():
    rc, out, _ = run(["lsblk", "-J", "-b", "-o",
                      "NAME,PATH,TYPE,FSTYPE,SIZE,RM,TRAN,MODEL,MOUNTPOINT"])
    internal, parts, notes = [], [], []
    if rc != 0:
        notes.append("lsblk failed")
        return internal, parts, notes
    try:
        data = json.loads(out)
    except Exception:
        notes.append("lsblk output unparsable")
        return internal, parts, notes

    boot = _boot_parent_disk()

    def _skip(disk):
        if disk.get("name", "").startswith(("loop", "sr", "zram", "ram", "dm-")):
            return True
        if disk.get("path") == boot:
            return True
        if (disk.get("rm") or disk.get("tran") == "usb"):
            if int(disk.get("size") or 0) < 32_000_000_000:
                return True
        return _has_live_mount(disk)

    def _walk(n):
        for c in n.get("children") or []:
            if c.get("type") in ("part", "lvm", "crypt", "raid0", "raid1",
                                 "raid4", "raid5", "raid6", "raid10",
                                 "mpath", "dm"):
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

    seen, uniq = set(), []
    for p in parts:
        if p["path"] in seen:
            continue
        seen.add(p["path"])
        uniq.append(p)
    return internal, uniq, notes


def _has_bitlocker_sig(dev):
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


def _has_luks_sig(dev):
    try:
        return open(dev, "rb").read(6)[:4] == b"LUKS"
    except Exception:
        return False


def mount_ro(dev, mnt, fs):
    if fs in ("ntfs", "ntfs3"):
        cands = [["mount", "-t", "ntfs-3g", "-o", "ro,noload,nodev,nosuid,noexec", dev, mnt],
                 ["mount", "-t", "ntfs3", "-o", "ro,nodev,nosuid,noexec", dev, mnt],
                 ["mount", "-o", "ro,nodev,nosuid,noexec", dev, mnt]]
    elif fs == "apfs":
        cands = []
        if shutil.which("apfs-fuse"):
            cands.append(["apfs-fuse", "-o", "ro", dev, mnt])
        cands.append(["mount", "-t", "apfs", "-o", "ro,nodev,nosuid,noexec", dev, mnt])
    elif fs in ("hfs", "hfs+"):
        cands = [["mount", "-t", "hfsplus", "-o", "ro,nodev,nosuid,noexec", dev, mnt],
                 ["mount", "-t", "hfs", "-o", "ro,nodev,nosuid,noexec", dev, mnt]]
    else:
        cands = [["mount", "-o", "ro,nodev,nosuid,noexec", dev, mnt]]
    for cmd in cands:
        rc, _, _ = run(cmd, timeout=20)
        if rc == 0 or os.path.ismount(mnt):
            return True
    return False


def unmount(mnt):
    rc, _, _ = run(["umount", mnt], timeout=10)
    if rc != 0:
        run(["umount", "-l", mnt], timeout=10)


def check_firmware_absolute():
    signs, warns = [], []
    wpbt = os.path.exists("/sys/firmware/acpi/tables/WPBT")
    words_hit = []
    if os.path.isdir("/sys/firmware/acpi/tables"):
        for p in glob.glob("/sys/firmware/acpi/tables/*"):
            if not os.path.isfile(p):
                continue
            d = read_bytes(p, 4 * 1024 * 1024).lower()
            for w in ABSOLUTE_WORDS:
                if w in d:
                    words_hit.append((w.decode(), os.path.basename(p)))
                    break
    efi = []
    if os.path.isdir("/sys/firmware/efi/efivars"):
        for p in glob.glob("/sys/firmware/efi/efivars/*"):
            n = os.path.basename(p).lower()
            if "absolute" in n or "computrace" in n:
                efi.append(os.path.basename(p))
    bios_hit = []
    rc, out, _ = run(["dmidecode"], timeout=20)
    if rc == 0:
        low = out.lower().encode("utf-8", "ignore")
        for w in ABSOLUTE_WORDS:
            if w in low:
                bios_hit.append(w.decode())
                break
    for w, t in words_hit:
        signs.append("Word '%s' in firmware table %s" % (w, t))
    for v in efi:
        signs.append("UEFI variable: %s" % v)
    for w in bios_hit:
        signs.append("Word '%s' in BIOS/DMI strings" % w)
    if wpbt:
        if signs:
            signs.append("WPBT ACPI table also present")
        else:
            warns.append("WPBT present but no Absolute strings — verify in BIOS")
    return signs, warns


def check_win_absolute(root):
    return ["Absolute agent file: " + r.replace("/", "\\")
            for r in WIN_ABSOLUTE_FILES if find_ci(root, r)]


def _parse_autopilot_json(path):
    try:
        data = json.loads(open(path, "r", errors="replace").read(256 * 1024))
    except Exception:
        return {}
    def _find(d, *keys):
        for k in keys:
            if isinstance(d, dict) and k in d:
                return d[k]
        return None
    sub = data.get("OobeSettings", {}) if isinstance(data, dict) else {}
    dev = data.get("DeviceInfo", {}) if isinstance(data, dict) else {}
    return {
        "tenant_id": (_find(data, "CloudAssignedTenantId", "TenantId")
                      or _find(sub, "CloudAssignedTenantId")),
        "serial": (_find(data, "CloudAssignedSerialNumber", "SerialNumber")
                   or _find(dev, "SerialNumber")),
        "device_name": _find(data, "CloudAssignedDeviceName", "DeviceName"),
    }


def analyze_autopilot(root):
    """Returns a structured analysis with clear state."""
    a = {"state": "NONE", "active": [], "past": [], "tenant": [],
         "mdm": [], "azure_ad": []}

    for rel, substate, desc, parse in AUTOPILOT_ITEMS:
        hit = find_ci(root, rel)
        if not hit:
            continue
        entry = {"description": desc, "path": rel.replace("/", "\\")}
        if parse and rel.lower().endswith(".json"):
            parsed = _parse_autopilot_json(hit)
            if any(parsed.values()):
                entry["profile"] = parsed
                a["tenant"].append(parsed)
        (a["active"] if substate == "ACTIVE" else a["past"]).append(entry)

    for hive_rel in REGISTRY_HIVES:
        hive = find_ci(root, hive_rel)
        if not hive:
            continue
        data = read_bytes(hive).lower()
        for needle, label in AUTOPILOT_REG_HINTS:
            if needle.lower() in data:
                a["active"].append({"description": label + " (registry)",
                                    "path": hive_rel.replace("/", "\\")})
        for needle, label in MDM_REG_HINTS:
            if needle.lower() in data:
                a["mdm"].append(label)

    for rel, label in AZURE_AD_ITEMS:
        if find_ci(root, rel):
            a["azure_ad"].append({"description": label,
                                  "path": rel.replace("/", "\\")})

    if a["active"]:
        a["state"] = "ACTIVE"
    elif a["past"] or a["mdm"]:
        a["state"] = "PAST"
    return a


def check_mac_absolute(root):
    return ["Absolute path: " + r for r in MAC_ABSOLUTE_PATHS if find_ci(root, r)]


def check_mac_enrolment(root):
    return ["%s (%s)" % (lbl, rel) for rel, lbl in MAC_ENROLMENT_ITEMS
            if find_ci(root, rel)]


def check_linux_absolute(root):
    return ["Absolute path (Linux): " + r for r in LINUX_ABSOLUTE_PATHS
            if find_ci(root, r)]


def _default_json_path():
    for c in ("/run/live/medium", "/cdrom", "/media/usb", "/mnt/usb"):
        if os.path.isdir(c) and os.access(c, os.W_OK):
            return os.path.join(c, "laptop_check.json")
    return "/tmp/laptop_check.json"


def _wrap(text, w):
    words, lines, cur = text.split(), [], ""
    for x in words:
        if len(cur) + len(x) + 1 > w and cur:
            lines.append(cur)
            cur = x
        else:
            cur = (cur + " " + x).strip()
    if cur:
        lines.append(cur)
    return lines


def print_autopilot_section(analysis, os_checked, platform):
    print("-" * 60)
    print(" AUTOPILOT / MDM ENROLLMENT STATUS")
    print("-" * 60)

    if not os_checked:
        print(" STATE: UNKNOWN — could not inspect the Windows drive")
        print()
        print(" The Windows volume could not be read (encrypted, missing,")
        print(" or unsupported filesystem). Autopilot and MDM enrollment")
        print(" cannot be checked. A manager must decide, or the drive")
        print(" must be unlocked / decrypted first.")
        return "UNKNOWN"

    state = analysis["state"]

    if state == "NONE":
        print(" STATE: CLEAN — no on-disk evidence of Autopilot or MDM")
        print()
        print(" What we found:")
        print("   Nothing. No Autopilot profile, no MDM enrollment key,")
        print("   no Intune/SCCM agent, no Azure AD artifact on disk.")
        print()
        print(" What this CANNOT rule out:")
        print("   A laptop whose hardware hash has been registered in a")
        print("   customer's Intune/ABM portal but which has NEVER been")
        print("   powered on leaves NO disk trace. Only the portal knows.")
        print()
        print(" Before resale: verify the serial number in the customer's")
        print(" Intune / Apple Business Manager portal. If it shows the")
        print(" device as 'registered' or 'assigned', do not sell it until")
        print(" removed from the portal.")
        return "CLEAN"

    if state == "PAST":
        print(" STATE: PAST ENROLLMENT — leftover traces, no active keys")
        print()
        print(" What we found:")
        for e in analysis["past"]:
            print("   [PAST] %s" % e["description"])
            print("          %s" % e["path"])
        for k in analysis["mdm"]:
            print("   [PAST] %s" % k)
        print()
        print(" What this means:")
        print("   This laptop was enrolled at some point. The active")
        print("   enrollment is gone, but logs / keys remain. This is")
        print("   usually a reimaged or unenrolled device.")
        print()
        print(" Before resale: verify the serial in the Intune / ABM")
        print(" portal. It MAY still be hash-registered even though the")
        print(" disk is clean.")
        return "PAST"

    if state == "ACTIVE":
        print(" STATE: ACTIVE ENROLLMENT — this laptop IS enrolled NOW")
        print()
        print(" What we found:")
        for e in analysis["active"]:
            print("   [ACTIVE] %s" % e["description"])
            print("            %s" % e["path"])
            if e.get("profile"):
                p = e["profile"]
                if p.get("tenant_id"):
                    print("            Tenant: %s" % p["tenant_id"])
                if p.get("device_name"):
                    print("            Device: %s" % p["device_name"])
                if p.get("serial"):
                    print("            Serial: %s" % p["serial"])
        for k in analysis["mdm"]:
            print("   [ACTIVE] %s" % k)
        print()
        print(" What this means:")
        print("   This laptop is under ACTIVE management by an MDM")
        print("   provider. Wiping the disk does NOT remove the cloud")
        print("   registration. The customer MUST remove it from their")
        print("   Intune / ABM / SCCM portal.")
        print()
        print(" ACTION REQUIRED:")
        print("   1. Contact the customer to remove this device from")
        print("      their portal, OR")
        print("   2. If you have portal access, remove it yourself.")
        print("   3. Re-run this check on the same laptop to confirm.")
        print()
        print(" DO NOT WIPE until the portal shows this device as")
        print(" removed or unassigned.")
        return "BLOCKED"

    return "UNKNOWN"


def main():
    ap = argparse.ArgumentParser(description="ITAD pre-wipe check")
    ap.add_argument("--job-id", default="")
    ap.add_argument("--asset-id", default="")
    ap.add_argument("--tech", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("Must run as root. Use: sudo python3 laptop_check.py ...")
        sys.exit(3)

    if not args.json:
        args.json = _default_json_path()

    platform = detect_platform()
    ident = get_identity()
    internal, parts, disk_notes = scan_disks()

    tmp = tempfile.mkdtemp(prefix="itad_lc_")
    mounted = []
    unreadable, encrypted = [], []
    win_roots, mac_roots = [], []
    win_parts, mac_parts = [], []
    abs_signs, warns = [], []
    autopilot_analysis = {"state": "NONE", "active": [], "past": [],
                          "tenant": [], "mdm": [], "azure_ad": []}
    mac_enrol = []

    try:
        fw_s, fw_w = check_firmware_absolute()
        abs_signs.extend(fw_s)
        warns.extend(fw_w)

        for i, p in enumerate(parts):
            dev, fs = p["path"], p["fstype"] or ""
            if fs in ENCRYPTED_FS:
                encrypted.append("%s (%s)" % (dev, fs))
                continue
            if _has_bitlocker_sig(dev):
                encrypted.append("%s (BitLocker signature)" % dev)
                continue
            if _has_luks_sig(dev):
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
                try_fs = {"ntfs", "ntfs3", "ext4", "btrfs", "xfs", "fat32"}
            if fs not in try_fs:
                continue

            mnt = os.path.join(tmp, "p%d" % i)
            os.makedirs(mnt, exist_ok=True)
            if not mount_ro(dev, mnt, fs):
                unreadable.append("%s (fs=%s)" % (dev, fs or "?"))
                continue
            mounted.append(mnt)

            if platform == "pc" and find_ci(mnt, "Windows/System32"):
                win_roots.append(mnt)
                win_parts.append(dev)
                abs_signs.extend(check_win_absolute(mnt))
                ap = analyze_autopilot(mnt)
                if ap["state"] == "ACTIVE":
                    autopilot_analysis["state"] = "ACTIVE"
                elif ap["state"] == "PAST" and autopilot_analysis["state"] != "ACTIVE":
                    autopilot_analysis["state"] = "PAST"
                for k in ("active", "past", "tenant", "mdm", "azure_ad"):
                    autopilot_analysis[k].extend(ap[k])
            elif platform == "mac":
                if find_ci(mnt, "Library") or find_ci(mnt, "System/Library"):
                    mac_roots.append(mnt)
                    mac_parts.append(dev)
                    abs_signs.extend(check_mac_absolute(mnt))
                    mac_enrol.extend(check_mac_enrolment(mnt))
            elif platform == "unknown":
                abs_signs.extend(check_linux_absolute(mnt))
    finally:
        for m in mounted:
            unmount(m)
        shutil.rmtree(tmp, ignore_errors=True)

    os_checked = ((platform == "pc" and win_roots) or
                  (platform == "mac" and mac_roots))
    cannot = list(encrypted) + list(unreadable) + list(disk_notes)
    if platform == "chromebook":
        cannot.append("ChromeOS — check Settings > About ChromeOS > "
                      "Additional details > Enterprise enrollment")
    if platform == "mac" and not os_checked:
        cannot.append("Mac — no APFS/HFS volume readable (FileVault on "
                      "or apfs-fuse missing). Use macOS Recovery or ABM.")
    if platform == "pc" and not os_checked:
        cannot.append("No Windows volume could be inspected")
    if not internal:
        cannot.append("No internal drives detected")

    blocked = bool(abs_signs) or autopilot_analysis["state"] == "ACTIVE"
    if blocked:
        result, code = "BLOCKED", 1
    elif cannot:
        result, code = "CANNOT CHECK", 2
    else:
        result, code = "CLEAR", 0

    if abs_signs:
        abs_result = "BLOCKED"
    elif os_checked and not cannot:
        abs_result = "CLEAR"
    else:
        abs_result = "CANNOT CHECK"

    ap_state = autopilot_analysis["state"]
    if ap_state == "ACTIVE":
        ap_result = "BLOCKED"
    elif ap_state == "PAST":
        ap_result = "WARN (past enrollment)"
    elif os_checked:
        ap_result = "CLEAN"
    else:
        ap_result = "UNKNOWN"

    report = {
        "tool": "laptop_check.py", "version": VERSION,
        "time": NOW.isoformat(), "platform": platform,
        "job_id": args.job_id, "asset_id": args.asset_id,
        "technician": args.tech, "identity": ident,
        "internal_disks": internal,
        "windows_partitions": win_parts, "mac_partitions": mac_parts,
        "encrypted_partitions": encrypted, "unreadable_partitions": unreadable,
        "notes": cannot,
        "absolute_result": abs_result, "absolute_signs": abs_signs,
        "autopilot_state": ap_state, "autopilot_result": ap_result,
        "autopilot_analysis": autopilot_analysis,
        "mac_enrolment": mac_enrol,
        "warnings": warns,
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
    ml = ident["model"] or "-"
    if ident["version"] and ident["version"] != ident["model"]:
        ml += " " + ident["version"]
    print(" Model       : %s" % ml)
    print(" Serial      : %s" % ident["serial"])
    print(" BIOS        : %s %s (%s)" % (ident["bios_vendor"],
                                        ident["bios_version"],
                                        ident["bios_date"]))
    print(" Boot mode   : %s  SecureBoot: %s" % (ident["boot_mode"],
                                               ident["secure_boot"]))
    for d in internal:
        print(" Drive       : %s  %s  %s GB" % (d["path"],
                                               d["model"] or "-",
                                               d["size_gb"]))
    if not internal:
        print(" Drive       : none found")
    print("-" * 60)
    print(" ABSOLUTE (firmware) : %s" % abs_result)
    for s in abs_signs:
        print("    - %s" % s)
    for w in warns:
        print("    (i) %s" % w)

    print_autopilot_section(autopilot_analysis, os_checked, platform)

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
        print(" DO NOT WIPE. Escalate.")
    elif result == "CANNOT CHECK":
        print(" DO NOT WIPE YET. Manager decision required.")
    else:
        print(" OK to proceed to laptop_wipe.py.")
    print(line)
    if args.json != "-":
        print(" Saved: %s" % args.json)

    sys.exit(code)


if __name__ == "__main__":
    main()