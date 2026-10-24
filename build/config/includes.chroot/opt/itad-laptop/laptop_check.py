#!/usr/bin/env python3
"""
laptop_check.py - ITAD pre-wipe check (v3.1.1)  MULTI-PLATFORM
READ ONLY. Never wipes or writes to any internal drive.

v3.1.1: bug fixes over v3.1.0 (no change to check logic):
  - --json - no longer pollutes stdout (human output goes to stderr)
  - registry hives are streamed, not capped at 8 MB
  - file handles closed properly
  - --verbose now prints diagnostics
  - variable shadowing and unused parameters removed
  - PAST state now also lists Azure AD traces
"""
import argparse, glob, fnmatch, json, os, re, shutil, subprocess, sys, tempfile
import threading, time
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    NOW = datetime.now(ZoneInfo("Europe/Dublin"))
except Exception:
    NOW = datetime.now()

VERSION = "3.1.1"

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

VERBOSE = False


def vlog(msg):
    if VERBOSE:
        print(paint("    · " + msg, "dim"))


# =============================================================================
# LOOKS
# =============================================================================
ANSI_RE = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
_TERM = os.environ.get("TERM", "")
LIVE = sys.stdout.isatty() and _TERM != "dumb"
USE_COLOR = LIVE and not os.environ.get("NO_COLOR")
_ENC = (getattr(sys.stdout, "encoding", None) or "").lower()
UNICODE = "utf" in _ENC
FANCY = UNICODE and _TERM != "linux"

try:
    W = max(60, min(shutil.get_terminal_size((80, 24)).columns, 78))
except Exception:
    W = 78
INNER = W - 4

if UNICODE:
    BX = dict(h="─", v="│", tl="┌", tr="┐", bl="└", br="┘", ml="├", mr="┤",
              H="═", V="║", TL="╔", TR="╗", BL="╚", BR="╝",
              full="█", empty="░", ell="…")
else:
    BX = dict(h="-", v="|", tl="+", tr="+", bl="+", br="+", ml="+", mr="+",
              H="=", V="|", TL="+", TR="+", BL="+", BR="+",
              full="#", empty=".", ell="~")

if FANCY:
    ICON = {"ok": "✔", "bad": "✖", "warn": "▲", "skip": "○", "info": "●",
            "lock": "■", "go": "▸"}
else:
    ICON = {"ok": "+", "bad": "x", "warn": "!", "skip": "-", "info": "*",
            "lock": "#", "go": ">"}

_CODES = {
    "reset": "0", "bold": "1", "dim": "2",
    "black": "30", "red": "31", "green": "32", "yellow": "33",
    "blue": "34", "magenta": "35", "cyan": "36", "white": "37",
    "bg_red": "41", "bg_green": "42", "bg_yellow": "43", "bg_blue": "44",
}


def paint(text, *styles):
    if not USE_COLOR or not styles:
        return text
    return "\033[%sm%s\033[0m" % (";".join(_CODES[s] for s in styles), text)


def vlen(s):
    return len(ANSI_RE.sub("", s))


def pad(s, n):
    return s + " " * max(0, n - vlen(s))


def _trunc(s, n):
    if vlen(s) <= n:
        return s
    out, count = [], 0
    for tok in re.split(r"(\033\[[0-9;?]*[A-Za-z])", s):
        if ANSI_RE.fullmatch(tok):
            out.append(tok)
            continue
        room = n - 1 - count
        if room <= 0:
            break
        take = tok[:room]
        out.append(take)
        count += len(take)
        if len(take) < len(tok):
            break
    return "".join(out) + BX["ell"] + ("\033[0m" if USE_COLOR else "")


def _wrap(text, width):
    lines, cur = [], ""
    for word in str(text).split():
        while len(word) > width:
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:width])
            word = word[width:]
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = (cur + " " + word) if cur else word
    if cur:
        lines.append(cur)
    return lines


_STATE = {
    "CLEAR": ("ok", "green"), "CLEAN": ("ok", "green"),
    "PASS": ("ok", "green"), "OK": ("ok", "green"),
    "BLOCKED": ("bad", "red"), "FAIL": ("bad", "red"),
    "ACTIVE": ("bad", "red"),
    "WARN": ("warn", "yellow"), "PAST": ("warn", "yellow"),
    "CANNOT CHECK": ("warn", "yellow"), "UNKNOWN": ("warn", "yellow"),
    "SKIP": ("skip", None),
}


def tag(state, label=None):
    key = str(state).split(" (")[0].strip().upper()
    icon, color = _STATE.get(key, ("info", "cyan"))
    text = "%s %s" % (ICON[icon], label if label is not None else state)
    return paint(text, "bold", color) if color else paint(text, "dim")


def rule(title, color="cyan"):
    t = " %s " % title
    fill = max(2, W - 4 - len(t))
    print()
    print(paint(BX["h"] * 2, color) + paint(t, "bold", color)
          + paint(BX["h"] * fill, color))


def box(title, rows, color="cyan"):
    t = (" %s " % title) if title else ""
    fill = max(0, W - 3 - len(t))
    print(paint(BX["tl"] + BX["h"], color) + paint(t, "bold", color)
          + paint(BX["h"] * fill + BX["tr"], color))
    for row in rows:
        if row is None:
            print(paint(BX["ml"] + BX["h"] * (W - 2) + BX["mr"], color))
            continue
        row = _trunc(row, INNER)
        print(paint(BX["v"], color) + " " + pad(row, INNER) + " "
              + paint(BX["v"], color))
    print(paint(BX["bl"] + BX["h"] * (W - 2) + BX["br"], color))


def kv(label, value, style=(), lw=11):
    text = str(value).strip() or "-"
    lines = _wrap(text, INNER - lw - 2) or ["-"]
    rows = []
    for i, ln in enumerate(lines):
        lab = paint(label.ljust(lw), "dim") if i == 0 else " " * lw
        rows.append(lab + "  " + (paint(ln, *style) if style else ln))
    return rows


def item(prefix, text, style=(), indent=0):
    plen = vlen(prefix)
    lines = _wrap(text, INNER - plen - 1 - indent) or [""]
    rows = []
    for i, ln in enumerate(lines):
        lead = prefix if i == 0 else " " * plen
        rows.append(" " * indent + lead + " "
                    + (paint(ln, *style) if style else ln))
    return rows


def para(text, style=(), indent=0):
    return [" " * indent + (paint(ln, *style) if style else ln)
            for ln in _wrap(text, INNER - indent)]


def title_banner(title, right=""):
    inner = W - 2
    left = " " + title
    mid = max(1, inner - len(left) - len(right) - 1)
    body = paint(left, "bold") + " " * mid + paint(right, "dim") + " "
    if vlen(body) > inner:
        body = _trunc(body, inner)
    print(paint(BX["TL"] + BX["H"] * inner + BX["TR"], "cyan"))
    print(paint(BX["V"], "cyan") + pad(body, inner) + paint(BX["V"], "cyan"))
    print(paint(BX["BL"] + BX["H"] * inner + BX["BR"], "cyan"))


_BANNER_STYLE = {
    "green": ("bold", "black", "bg_green"),
    "red": ("bold", "white", "bg_red"),
    "yellow": ("bold", "black", "bg_yellow"),
}


def big_banner(lines, color):
    print()
    if USE_COLOR:
        st = _BANNER_STYLE[color]
        blank = paint(" " * W, *st)
        print(blank)
        for ln in lines:
            print(paint(ln.center(W), *st))
        print(blank)
    else:
        print(BX["TL"] + BX["H"] * (W - 2) + BX["TR"])
        for ln in lines:
            print(BX["V"] + ln.center(W - 2) + BX["V"])
        print(BX["BL"] + BX["H"] * (W - 2) + BX["BR"])
    print()


def spaced(text):
    return " ".join(text)


# =============================================================================
# Live progress line
# =============================================================================
class Step:
    FRAMES = "|/-\\"

    def __init__(self, label):
        self.label = label
        self.t0 = time.time()
        self._stop = threading.Event()
        self._th = None
        if LIVE:
            self._th = threading.Thread(target=self._spin, daemon=True)
            self._th.start()

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            sys.stdout.write("\r\033[K  %s %s" % (
                paint(self.FRAMES[i % 4], "cyan"), self.label))
            sys.stdout.flush()
            i += 1
            self._stop.wait(0.12)

    def __del__(self):
        try:
            self._stop.set()
        except Exception:
            pass

    def _end(self, kind, note):
        self._stop.set()
        if self._th:
            self._th.join(timeout=1)
            sys.stdout.write("\r\033[K")
        icon, color = {"ok": (ICON["ok"], "green"),
                       "warn": (ICON["warn"], "yellow"),
                       "fail": (ICON["bad"], "red"),
                       "skip": (ICON["skip"], None)}[kind]
        mark = paint(icon, "bold", color) if color else paint(icon, "dim")
        dt = time.time() - self.t0
        took = paint(" (%.1fs)" % dt, "dim") if dt >= 0.5 else ""
        line = "  %s %s %s%s" % (mark, self.label.ljust(30)[:30],
                                 paint(note, color) if color and note
                                 else paint(note, "dim") if note else "",
                                 took)
        print(_trunc(line, W))

    def ok(self, note=""):
        self._end("ok", note)

    def warn(self, note=""):
        self._end("warn", note)

    def fail(self, note=""):
        self._end("fail", note)

    def skip(self, note=""):
        self._end("skip", note)


# =============================================================================
# CHECKS
# =============================================================================
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
        with open(p, "r", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return ""


def read_bytes(p, n=None):
    try:
        with open(p, "rb") as f:
            if n is None:
                return f.read()
            chunks, remaining = [], n
            while remaining > 0:
                buf = f.read(remaining)
                if not buf:
                    break
                chunks.append(buf)
                remaining -= len(buf)
            return b"".join(chunks)
    except Exception:
        return b""


def file_contains_any(path, needles, chunk=4 * 1024 * 1024):
    """Stream a (potentially huge) file looking for any of `needles`.
    Returns the subset of `needles` (original form) that were found."""
    if not needles:
        return set()
    found = set()
    lower_map = {n.lower(): n for n in needles}
    try:
        with open(path, "rb") as f:
            carry = b""
            while True:
                buf = f.read(chunk)
                if not buf:
                    break
                hay = (carry + buf).lower()
                for nl, orig in lower_map.items():
                    if orig not in found and nl in hay:
                        found.add(orig)
                carry = hay[-256:]
                if len(found) == len(lower_map):
                    break
    except Exception:
        pass
    return found


def _fnmatch_ci(name, pat):
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
        with open("/proc/mounts") as f:
            for line in f:
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
        with open(dev, "rb") as f:
            return f.read(4) == b"LUKS"
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
        with open(path, "r", errors="replace") as f:
            data = json.loads(f.read(256 * 1024))
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

    all_hints = ([n for n, _ in AUTOPILOT_REG_HINTS] +
                 [n for n, _ in MDM_REG_HINTS])

    for hive_rel in REGISTRY_HIVES:
        hive = find_ci(root, hive_rel)
        if not hive:
            continue
        hits = file_contains_any(hive, all_hints)
        for needle, label in AUTOPILOT_REG_HINTS:
            if needle in hits:
                a["active"].append({"description": label + " (registry)",
                                    "path": hive_rel.replace("/", "\\")})
        for needle, label in MDM_REG_HINTS:
            if needle in hits:
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


# =============================================================================
# SCREEN SECTIONS
# =============================================================================
def device_box(platform, ident, internal):
    ml = ident["model"] or "-"
    if ident["version"] and ident["version"] != ident["model"]:
        ml += " " + ident["version"]
    rows = []
    rows += kv("Platform", PLATFORM_LABEL.get(platform, platform))
    rows += kv("Make", ident["manufacturer"] or "-")
    rows += kv("Model", ml)
    rows += kv("Serial", ident["serial"], ("bold",))
    rows += kv("BIOS", "%s %s (%s)" % (ident["bios_vendor"],
                                       ident["bios_version"],
                                       ident["bios_date"]))
    rows += kv("Boot mode", "%s   SecureBoot: %s" % (ident["boot_mode"],
                                                     ident["secure_boot"]))
    if internal:
        for d in internal:
            rows += kv("Drive", "%s  %s  %s GB" % (
                d["path"], d["model"] or "-", d["size_gb"]))
    else:
        rows += kv("Drive", "none found", ("yellow",))
    box("DEVICE", rows, "cyan")


def absolute_box(abs_result, abs_signs, warns):
    rows = [tag(abs_result) + paint("   firmware / disk agent check", "dim")]
    if abs_signs:
        rows.append("")
        for s in abs_signs:
            rows += item(paint(ICON["bad"], "bold", "red"), s)
    for w in warns:
        rows += item(paint(ICON["info"], "yellow"), w, ("yellow",))
    color = {"CLEAR": "green", "BLOCKED": "red"}.get(abs_result, "yellow")
    box("ABSOLUTE", rows, color)


def autopilot_box(analysis, os_checked):
    if not os_checked:
        rows = [tag("UNKNOWN", "UNKNOWN  could not inspect the Windows drive"),
                ""]
        rows += para("The Windows volume could not be read (encrypted, "
                     "missing, or unsupported filesystem). Autopilot and "
                     "MDM enrollment cannot be checked. A manager must "
                     "decide, or the drive must be unlocked / decrypted "
                     "first.")
        box("AUTOPILOT / MDM", rows, "yellow")
        return "UNKNOWN"

    state = analysis["state"]

    if state == "NONE":
        rows = [tag("CLEAN", "CLEAN  no on-disk evidence of Autopilot or MDM"),
                ""]
        rows += para("Found: nothing. No Autopilot profile, no MDM "
                     "enrollment key, no Intune/SCCM agent, no Azure AD "
                     "artifact on disk.")
        rows.append(None)
        rows += para("This CANNOT rule out a laptop whose hardware hash is "
                     "registered in a customer's Intune / Apple Business "
                     "Manager portal but which has never been powered on. "
                     "That leaves no disk trace. Only the portal knows.",
                     ("yellow",))
        rows.append("")
        rows += para("Before resale: check the serial number in the "
                     "customer's Intune / ABM portal. If it shows "
                     "'registered' or 'assigned', do not sell it until it "
                     "is removed from the portal.", ("bold",))
        box("AUTOPILOT / MDM", rows, "green")
        return "CLEAN"

    if state == "PAST":
        rows = [tag("PAST", "PAST ENROLLMENT  leftover traces, no active keys"),
                ""]
        for e in analysis["past"]:
            rows += item(paint("[PAST]", "yellow"), e["description"])
            rows += para(e["path"], ("dim",), indent=7)
        for k in analysis["mdm"]:
            rows += item(paint("[PAST]", "yellow"), k)
        for e in analysis["azure_ad"]:
            rows += item(paint("[AZURE-AD]", "yellow"), e["description"])
            rows += para(e["path"], ("dim",), indent=11)
        rows.append(None)
        rows += para("This laptop was enrolled at some point. The active "
                     "enrollment is gone, but logs / keys remain. Usually "
                     "a reimaged or unenrolled device.")
        rows.append("")
        rows += para("Before resale: check the serial in the Intune / ABM "
                     "portal. It MAY still be hash-registered even though "
                     "the disk is clean.", ("bold",))
        box("AUTOPILOT / MDM", rows, "yellow")
        return "PAST"

    if state == "ACTIVE":
        rows = [tag("ACTIVE", "ACTIVE ENROLLMENT  this laptop IS enrolled NOW"),
                ""]
        for e in analysis["active"]:
            rows += item(paint("[ACTIVE]", "bold", "red"), e["description"])
            rows += para(e["path"], ("dim",), indent=9)
            if e.get("profile"):
                p = e["profile"]
                if p.get("tenant_id"):
                    rows += para("Tenant: %s" % p["tenant_id"], indent=9)
                if p.get("device_name"):
                    rows += para("Device: %s" % p["device_name"], indent=9)
                if p.get("serial"):
                    rows += para("Serial: %s" % p["serial"], indent=9)
        for k in analysis["mdm"]:
            rows += item(paint("[ACTIVE]", "bold", "red"), k)
        rows.append(None)
        rows += para("This laptop is under ACTIVE management by an MDM "
                     "provider. Wiping the disk does NOT remove the cloud "
                     "registration. The customer MUST remove it from their "
                     "Intune / ABM / SCCM portal.")
        rows.append("")
        rows.append(paint("ACTION REQUIRED", "bold", "red"))
        rows += item("1.", "Contact the customer to remove this device "
                           "from their portal, OR", indent=1)
        rows += item("2.", "If you have portal access, remove it "
                           "yourself.", indent=1)
        rows += item("3.", "Re-run this check on the same laptop to "
                           "confirm.", indent=1)
        rows.append("")
        rows += para("DO NOT WIPE until the portal shows this device as "
                     "removed or unassigned.", ("bold", "red"))
        box("AUTOPILOT / MDM", rows, "red")
        return "BLOCKED"

    return "UNKNOWN"


def cannot_box(encrypted, unreadable, cannot):
    rows = []
    for s in encrypted:
        rows += item(paint(ICON["lock"], "yellow"),
                     "Encrypted volume, cannot inspect: %s" % s)
    for s in unreadable:
        rows += item(paint(ICON["warn"], "yellow"),
                     "Could not mount: %s" % s)
    for s in cannot:
        if s in encrypted or s in unreadable:
            continue
        rows += item(paint(ICON["warn"], "yellow"), s)
    if rows:
        box("COULD NOT CHECK", rows, "yellow")


# =============================================================================
# MAIN
# =============================================================================
def main():
    global USE_COLOR, VERBOSE
    ap = argparse.ArgumentParser(description="ITAD pre-wipe check")
    ap.add_argument("--job-id", default="")
    ap.add_argument("--asset-id", default="")
    ap.add_argument("--tech", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--no-color", action="store_true",
                    help="Plain text, no colours")
    args = ap.parse_args()
    if args.no_color:
        USE_COLOR = False
    VERBOSE = args.verbose

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print(paint("Must run as root. Use: sudo python3 laptop_check.py ...",
                    "bold", "red"))
        sys.exit(3)

    if not args.json:
        args.json = _default_json_path()

    # --json - : keep stdout clean for JSON; human output goes to stderr.
    json_stream = sys.stdout
    if args.json == "-":
        sys.stdout = sys.stderr

    title_banner("ITAD PRE-WIPE CHECK", "v%s   READ ONLY" % VERSION)
    box("JOB", kv("Time", NOW.strftime("%Y-%m-%d  %H:%M:%S"))
        + kv("Job ID", args.job_id or "-")
        + kv("Asset ID", args.asset_id or "-")
        + kv("Technician", args.tech or "-"), "blue")

    rule("SCANNING")

    st = Step("Detecting platform")
    platform = detect_platform()
    st.ok(PLATFORM_LABEL.get(platform, platform))

    st = Step("Reading device identity")
    ident = get_identity()
    if ident["serial"] == "UNKNOWN":
        st.warn("serial not found")
    else:
        st.ok("serial " + ident["serial"])

    st = Step("Scanning internal drives")
    internal, parts, disk_notes = scan_disks()
    if disk_notes:
        st.warn(disk_notes[0])
    elif not internal:
        st.warn("no internal drives found")
    else:
        st.ok("%d drive(s), %d partition(s)" % (len(internal), len(parts)))

    try:
        tmp = tempfile.mkdtemp(prefix="itad_lc_")
    except Exception as e:
        print(paint("Cannot create temp dir: %s" % e, "bold", "red"))
        sys.exit(4)

    mounted = []
    unreadable, encrypted = [], []
    win_roots, mac_roots = [], []
    win_parts, mac_parts = [], []
    abs_signs, warns = [], []
    autopilot_analysis = {"state": "NONE", "active": [], "past": [],
                          "tenant": [], "mdm": [], "azure_ad": []}
    mac_enrol = []

    try:
        st = Step("Checking firmware for Absolute")
        fw_s, fw_w = check_firmware_absolute()
        abs_signs.extend(fw_s)
        warns.extend(fw_w)
        if fw_s:
            st.fail("%d sign(s) found" % len(fw_s))
        elif fw_w:
            st.warn("verify in BIOS")
        else:
            st.ok("nothing found")

        for i, p in enumerate(parts):
            dev, fs = p["path"], p["fstype"] or ""
            st = Step("Inspecting %s" % dev)
            if fs in ENCRYPTED_FS:
                encrypted.append("%s (%s)" % (dev, fs))
                st.warn("encrypted (%s)" % fs)
                continue
            if _has_bitlocker_sig(dev):
                encrypted.append("%s (BitLocker signature)" % dev)
                st.warn("encrypted (BitLocker)")
                continue
            if _has_luks_sig(dev):
                encrypted.append("%s (LUKS signature)" % dev)
                st.warn("encrypted (LUKS)")
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
                st.skip("not checked (fs=%s)" % (fs or "?"))
                vlog("%s skipped: fs=%s not in try_fs" % (dev, fs or "?"))
                continue

            mnt = os.path.join(tmp, "p%d" % i)
            os.makedirs(mnt, exist_ok=True)
            if not mount_ro(dev, mnt, fs):
                unreadable.append("%s (fs=%s)" % (dev, fs or "?"))
                st.warn("could not mount")
                vlog("%s: mount_ro failed (fs=%s)" % (dev, fs or "?"))
                continue
            mounted.append(mnt)
            found = "mounted read-only, no OS found"

            if platform == "pc" and find_ci(mnt, "Windows/System32"):
                win_roots.append(mnt)
                win_parts.append(dev)
                abs_signs.extend(check_win_absolute(mnt))
                ap_data = analyze_autopilot(mnt)
                if ap_data["state"] == "ACTIVE":
                    autopilot_analysis["state"] = "ACTIVE"
                elif (ap_data["state"] == "PAST"
                      and autopilot_analysis["state"] != "ACTIVE"):
                    autopilot_analysis["state"] = "PAST"
                for k in ("active", "past", "tenant", "mdm", "azure_ad"):
                    autopilot_analysis[k].extend(ap_data[k])
                found = "Windows volume"
            elif platform == "mac":
                if find_ci(mnt, "Library") or find_ci(mnt, "System/Library"):
                    mac_roots.append(mnt)
                    mac_parts.append(dev)
                    abs_signs.extend(check_mac_absolute(mnt))
                    mac_enrol.extend(check_mac_enrolment(mnt))
                    found = "macOS volume"
            elif platform == "unknown":
                abs_signs.extend(check_linux_absolute(mnt))
                found = "mounted read-only"
            st.ok(found)
    finally:
        for m in mounted:
            unmount(m)
        shutil.rmtree(tmp, ignore_errors=True)

    os_checked = bool(
        (platform == "pc" and win_roots) or
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
        json.dump(report, json_stream, indent=2)
        json_stream.write("\n")
        json_stream.flush()
    else:
        try:
            with open(args.json, "w") as f:
                json.dump(report, f, indent=2)
        except Exception as e:
            print("Could not save JSON: %s" % e)

    rule("RESULTS")
    device_box(platform, ident, internal)
    absolute_box(abs_result, abs_signs, warns)
    autopilot_box(autopilot_analysis, os_checked)
    cannot_box(encrypted, unreadable, cannot)

    if result == "BLOCKED":
        big_banner(["", "%s  %s" % (ICON["bad"], spaced("BLOCKED")),
                    "DO NOT WIPE.  Escalate.", ""], "red")
    elif result == "CANNOT CHECK":
        big_banner(["", "%s  %s" % (ICON["warn"], spaced("CANNOT CHECK")),
                    "DO NOT WIPE YET.  Manager decision required.", ""],
                   "yellow")
    else:
        big_banner(["", "%s  %s" % (ICON["ok"], spaced("CLEAR")),
                    "OK to proceed to laptop_wipe.py", ""], "green")
    if args.json != "-":
        print(paint(" Saved: %s" % args.json, "dim"))

    sys.exit(code)


if __name__ == "__main__":
    main()