#!/usr/bin/env python3
"""
laptop_window.py - LaptopSeal window (v1.1.0)
This is the GRAPHICAL USER INTERFACE (GUI).

v1.1.0
  - New "Sign in" screen: technician name + password.
    Passwords are never stored. Only a salted PBKDF2-SHA256 hash is
    saved in technicians.txt (on the ITAD_DATA stick when it is plugged in).
  - "Add technician" button on the sign-in screen.
  - Hardware card shows the real RAM size (from the RAM modules, not the
    smaller number Linux reports), RAM type, speed and slots used.
  - Hardware card and report show the screen size in inches and the
    resolution (read from the built-in screen's EDID).
"""
import argparse
import glob
import hashlib
import hmac
import importlib.util
import json
import math
import os
import pty
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Dublin")
except Exception:
    TZ = None

VERSION = "1.1.0"
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

# --- THESE ARE THE FILES THIS GUI WILL RUN ---
CHECK = os.path.join(TOOLS_DIR, "laptop_check.py")    # The MDM/Autopilot scanner
WIPE = os.path.join(TOOLS_DIR, "laptop_wipe.py")      # The disk wiper
HW_TEST = os.path.join(TOOLS_DIR, "laptop_test.py")   # The automated CPU/RAM test
PY = sys.executable

# Colours: same family as the SwitchSeal window
NAVY, PANEL, DEEP, BORDER = "#0B1F3A", "#102A4D", "#08172C", "#23406B"
BLUE, CYAN, TEXT, MUTED = "#1E6FE0", "#22C7E8", "#E8F1FF", "#9BB2D1"
OK, BAD, WARN = "#3DDC97", "#FF8A8A", "#FFC857"
RED_BAN, GREEN_BAN = "#C62F2F", "#1FA971"
FONT = "DejaVu Sans"

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\([A-Z0-9]")
STEPS = ["1  Sign in", "2  Job", "3  Checks", "4  Wipe & Tests", "5  Report"]
TECH_FILE = "technicians.txt"
PBKDF_ITER = 200000
MIN_PASSWORD = 6
MAX_TRIES = 5          # wrong passwords before a short lock
LOCK_SECONDS = 30
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._'-]{1,29}$")
STD_SCREENS = [10.1, 11.6, 12.0, 12.3, 12.5, 13.0, 13.3, 13.4, 13.5, 13.6,
               14.0, 14.5, 15.0, 15.6, 16.0, 16.1, 17.0, 17.3, 18.0]
STD_RAM_GB = [1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 36, 40, 48, 64, 96, 128,
              192, 256]
LOCATIONS = ["Warehouse DPA3 Test Bay 1", "Warehouse DPA3 Test Bay 2",
             "Warehouse DPA3 Test Bay 3", "Warehouse DPA3 Test Bay 4", "Other"]
AUTO_TESTS = "cpu,ram,drive,battery,webcam,wifi,touchpad,usb"
COMPONENTS = ["CPU", "RAM", "Drive Health", "Battery", "Webcam", "Wi-Fi",
              "Touchpad", "USB", "Screen", "Keyboard", "Speakers"]
MANUAL = ["Screen", "Keyboard", "Speakers"]
BANNER_NAMES = {"CPU": "CPU", "RAM": "RAM", "DRIVE": "Drive Health",
                "BATTERY": "Battery", "WEBCAM": "Webcam", "WIFI": "Wi-Fi",
                "TOUCHPAD": "Touchpad", "USB": "USB"}
BAD_SERIALS = {
    "", "none", "default string", "to be filled by o.e.m.",
    "to be filled by o.e.m", "system serial number", "not specified",
    "0", "n/a", "unknown", "o.e.m.", "oem", "chassis serial number",
    "0123456789", "1234567890",
}

SCREEN_COLORS = [("RED", "#FF0000", "white"), ("GREEN", "#00FF00", "black"),
                 ("BLUE", "#0000FF", "white"), ("WHITE", "#FFFFFF", "black"),
                 ("BLACK", "#000000", "white"), ("GREY", "#808080", "white")]
KB_ROWS = [
    [("ESC", "ESC")] + [(c, c) for c in "1234567890"]
    + [("-", "-"), ("=", "="), ("BKSP", "BKSP")],
    [("TAB", "TAB")] + [(c, c) for c in "QWERTYUIOP"]
    + [("[", "["), ("]", "]")],
    [("CAPS", "CAPS")] + [(c, c) for c in "ASDFGHJKL"]
    + [(";", ";"), ("'", "'"), ("ENTER", "ENTER")],
    [("SHIFT", "LSHIFT")] + [(c, c) for c in "ZXCVBNM"]
    + [(",", ","), (".", "."), ("/", "/"), ("SHIFT", "RSHIFT")],
    [("CTRL", "CTRL"), ("ALT", "ALT"), ("SPACE", "SPACE"), ("ALT", "RALT"),
     ("<", "<"), ("^", "^"), ("v", "v"), (">", ">")],
]
KEYMAP = {
    "Escape": "ESC", "Tab": "TAB", "Caps_Lock": "CAPS", "Return": "ENTER",
    "KP_Enter": "ENTER", "space": "SPACE", "BackSpace": "BKSP",
    "Shift_L": "LSHIFT", "Shift_R": "RSHIFT", "Control_L": "CTRL",
    "Control_R": "CTRL", "Alt_L": "ALT", "Alt_R": "RALT",
    "ISO_Level3_Shift": "RALT", "Left": "<", "Right": ">", "Up": "^",
    "Down": "v", "minus": "-", "equal": "=", "bracketleft": "[",
    "bracketright": "]", "semicolon": ";", "apostrophe": "'", "comma": ",",
    "period": ".", "slash": "/", "exclam": "1", "at": "2", "numbersign": "3",
    "dollar": "4", "percent": "5", "asciicircum": "6", "ampersand": "7",
    "asterisk": "8", "parenleft": "9", "parenright": "0", "underscore": "-",
    "plus": "=", "braceleft": "[", "braceright": "]", "colon": ";",
    "quotedbl": "'", "less": ",", "greater": ".", "question": "/",
}


# =============================================================================
# plain helpers (no screen needed)
# =============================================================================
def now():
    return datetime.now(TZ) if TZ else datetime.now()


def load_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def valid_serial(s):
    return bool(s) and str(s).strip().lower() not in BAD_SERIALS


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s)) or "UNKNOWN"


def pick_out_dir():
    env = os.environ.get("ITAD_DATA")
    if env and os.path.isdir(env) and os.access(env, os.W_OK):
        return env, True
    for c in ("/mnt/itad", "/media/ITAD_DATA", "/mnt/ITAD_DATA"):
        if os.path.ismount(c) and os.access(c, os.W_OK):
            return c, True
    d = "/tmp/itad-reports"
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.expanduser("~/itad-reports")
        os.makedirs(d, exist_ok=True)
    return d, False


# =============================================================================
# technician accounts (technicians.txt)
#   One line per technician:   Name:pbkdf2_sha256$200000$<salt>$<hash>
#   A line with only a name has no password yet and cannot sign in.
#   The real password is NEVER written anywhere.
# =============================================================================
def tech_files(out_dir):
    seen, files = set(), []
    for base in (out_dir, "/mnt/itad", TOOLS_DIR):
        p = os.path.join(base, TECH_FILE)
        rp = os.path.realpath(p)
        if rp not in seen and os.path.isfile(p):
            seen.add(rp)
            files.append(p)
    return files


def parse_tech_line(ln):
    ln = ln.strip()
    if not ln or ln.startswith("#"):
        return None, ""
    if ":" in ln:
        name, rest = ln.split(":", 1)
    else:
        name, rest = re.split(r",", ln, 1)[0], ""
    name = name.strip()
    rest = rest.strip()
    return (name or None), (rest if rest.startswith("pbkdf2_sha256$") else "")


def load_accounts(out_dir):
    """Returns {lower name: {"name": Name, "hash": stored hash or ""}}."""
    acc = {}
    for p in tech_files(out_dir):
        try:
            with open(p) as f:
                for ln in f:
                    name, h = parse_tech_line(ln)
                    if not name:
                        continue
                    key = name.lower()
                    if key not in acc or (h and not acc[key]["hash"]):
                        acc[key] = {"name": name, "hash": h, "file": p}
        except Exception:
            pass
    return acc


def hash_password(password, salt=None, iterations=PBKDF_ITER):
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                             iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, salt.hex(), dk.hex())


def verify_password(password, stored):
    try:
        algo, it, salt, want = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), want)
    except Exception:
        return False


def save_account(out_dir, name, password):
    """Adds or updates one technician. Returns the file path used."""
    path = os.path.join(out_dir, TECH_FILE)
    lines = []
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except Exception:
        lines = ["# LaptopSeal technicians. Passwords are stored as hashes only.",
                 "# Add technicians from the sign-in screen."]
    new_line = "%s:%s" % (name, hash_password(password))
    done = False
    for i, ln in enumerate(lines):
        n, _ = parse_tech_line(ln)
        if n and n.lower() == name.lower():
            lines[i] = new_line
            done = True
    if not done:
        lines.append(new_line)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return path


# =============================================================================
# hardware details for the card and the report
# =============================================================================
def run_cmd(cmd, timeout=15):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, p.stdout
    except Exception:
        return 1, ""


def _size_to_gb(text):
    m = re.match(r"(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)", text or "", re.I)
    if not m:
        return 0
    n, unit = float(m.group(1)), m.group(2).upper()
    return {"KB": n / 1048576, "MB": n / 1024, "GB": n, "TB": n * 1024}[unit]


def _gb_text(gb):
    return ("%d GB" % round(gb)) if abs(gb - round(gb)) < 0.05 else \
        ("%.1f GB" % gb)


def parse_dmidecode_memory(text):
    """Reads 'dmidecode -t 17' output. Returns a list of slot dicts."""
    slots = []
    cur = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line == "Memory Device":
            cur = {}
            slots.append(cur)
            continue
        if cur is None or ":" not in line or raw.startswith("\t\t"):
            continue
        k, v = line.split(":", 1)
        cur[k.strip()] = v.strip()
    out = []
    for s in slots:
        gb = _size_to_gb(s.get("Size", ""))
        speed = s.get("Configured Memory Speed") or s.get("Speed") or ""
        if speed.lower() in ("unknown", "none", "0 mt/s", "0 mhz"):
            speed = s.get("Speed", "")
        if speed.lower() in ("unknown", "none"):
            speed = ""
        out.append({
            "size_gb": gb,
            "type": s.get("Type", ""),
            "speed": speed.replace("MHz", "MT/s") if speed else "",
            "form": s.get("Form Factor", ""),
            "locator": s.get("Locator", ""),
            "maker": s.get("Manufacturer", ""),
            "part": s.get("Part Number", ""),
        })
    return out


def ram_from_meminfo():
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemTotal:"):
                    gb = int(ln.split()[1]) / 1048576
                    for std in STD_RAM_GB:
                        if gb <= std:
                            return std
                    return round(gb)
    except Exception:
        pass
    return 0


def ram_details():
    rc, out = run_cmd(["dmidecode", "-t", "17"])
    slots = parse_dmidecode_memory(out) if rc == 0 else []
    used = [s for s in slots if s["size_gb"] > 0]
    info = {"total_gb": 0, "type": "", "speed": "", "slots_total": len(slots),
            "slots_used": len(used), "modules": used, "soldered": False,
            "source": "dmidecode"}
    if used:
        info["total_gb"] = sum(s["size_gb"] for s in used)
        types = [s["type"] for s in used
                 if s["type"] and s["type"].lower() not in ("unknown", "other")]
        if types:
            info["type"] = max(set(types), key=types.count)
        speeds = []
        for s in used:
            m = re.match(r"(\d+)", s["speed"] or "")
            if m:
                speeds.append(int(m.group(1)))
        if speeds:
            info["speed"] = "%d MT/s" % max(speeds)
        info["soldered"] = any(s["form"].lower() in ("row of chips", "chip")
                               for s in used)
    else:
        info["total_gb"] = ram_from_meminfo()
        info["source"] = "estimate"
    return info


def ram_text(r, short=False):
    if not r or not r.get("total_gb"):
        return "-"
    main = " ".join(x for x in (_gb_text(r["total_gb"]), r.get("type"),
                                r.get("speed")) if x)
    if r.get("source") == "estimate":
        return main + " (estimated, type not readable)"
    if short:
        return main
    mods = " + ".join(_gb_text(m["size_gb"]) for m in r.get("modules", []))
    slots = "%d of %d slot%s used" % (r["slots_used"], r["slots_total"],
                                      "" if r["slots_total"] == 1 else "s")
    if r.get("soldered"):
        slots += ", soldered on board"
    return "%s\n%s: %s" % (main, slots, mods)


def parse_edid(b):
    """Returns dict with maker, name, width/height mm, resolution."""
    if len(b) < 128 or b[0:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    w = (b[8] << 8) | b[9]
    maker = "".join(chr(((w >> s) & 0x1F) + 64) for s in (10, 5, 0))
    info = {"maker": maker, "name": "", "w_mm": 0, "h_mm": 0,
            "res_w": 0, "res_h": 0}
    for off in (54, 72, 90, 108):
        d = b[off:off + 18]
        if d[0] == 0 and d[1] == 0:
            if d[3] == 0xFC:
                info["name"] = d[5:18].decode("ascii", "ignore").strip()
            continue
        if not info["res_w"]:
            info["res_w"] = d[2] | ((d[4] & 0xF0) << 4)
            info["res_h"] = d[5] | ((d[7] & 0xF0) << 4)
            info["w_mm"] = d[12] | ((d[14] & 0xF0) << 4)
            info["h_mm"] = d[13] | ((d[14] & 0x0F) << 8)
    if not info["w_mm"] or not info["h_mm"]:
        info["w_mm"], info["h_mm"] = b[21] * 10, b[22] * 10
    return info


def screen_details():
    found = []
    for d in sorted(glob.glob("/sys/class/drm/card*-*")):
        conn = os.path.basename(d).split("-", 1)[1]
        try:
            with open(os.path.join(d, "status")) as f:
                if f.read().strip() != "connected":
                    continue
            with open(os.path.join(d, "edid"), "rb") as f:
                e = parse_edid(f.read())
        except Exception:
            continue
        if e:
            e["connector"] = conn
            e["internal"] = bool(re.match(r"(eDP|LVDS|DSI)", conn))
            found.append(e)
    found.sort(key=lambda e: not e["internal"])
    if found:
        s = found[0]
    else:
        s = None
        rc, out = run_cmd(["xrandr", "--query"], timeout=5)
        for ln in out.splitlines():
            m = re.search(r"^(\S+) connected.*?(\d+)x(\d+)\+.*?(\d+)mm x "
                          r"(\d+)mm", ln)
            if m:
                s = {"connector": m.group(1), "maker": "", "name": "",
                     "res_w": int(m.group(2)), "res_h": int(m.group(3)),
                     "w_mm": int(m.group(4)), "h_mm": int(m.group(5)),
                     "internal": bool(re.match(r"(eDP|LVDS|DSI)",
                                               m.group(1)))}
                if s["internal"]:
                    break
    if not s:
        return None
    s["inches"] = 0
    if s["w_mm"] and s["h_mm"]:
        raw = math.hypot(s["w_mm"], s["h_mm"]) / 25.4
        near = min(STD_SCREENS, key=lambda x: abs(x - raw))
        # use the size printed on the box when it is very close
        s["inches"] = near if abs(near - raw) <= 0.2 else round(raw, 1)
    return s


def screen_text(s):
    if not s:
        return "not detected"
    parts = []
    if s.get("inches"):
        parts.append('%.1f"' % s["inches"])
    if s.get("res_w"):
        parts.append("%d x %d" % (s["res_w"], s["res_h"]))
    txt = "  ".join(parts) or "size unknown"
    extra = [x for x in (s.get("name") or s.get("maker"),
                         s.get("connector")) if x]
    if not s.get("internal"):
        extra.append("external screen")
    return txt + ("  (%s)" % ", ".join(extra) if extra else "")


def load_check_module():
    try:
        spec = importlib.util.spec_from_file_location("laptop_check_mod", CHECK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def quick_identity():
    mod = load_check_module()
    if mod:
        try:
            return mod.get_identity(), mod.scan_disks()[0]
        except Exception:
            pass

    def rd(n):
        try:
            with open("/sys/class/dmi/id/" + n) as f:
                return f.read().strip()
        except Exception:
            return ""
    return ({"manufacturer": rd("sys_vendor"), "model": rd("product_name"),
             "serial": rd("product_serial") or "UNKNOWN", "cpu": "", "ram_gb": 0,
             "device_type": "", "bios_vendor": rd("bios_vendor"),
             "bios_version": rd("bios_version"), "boot_mode": ""}, [])


def compute_overall(wipe_state, wipe_overall, tests):
    reasons = []
    failed = False
    if wipe_state == "FAILED" or wipe_overall == "FAIL":
        failed = True
        reasons.append("Wipe failed")
    for t in tests:
        if t.get("status") == "FAIL":
            failed = True
            reasons.append("%s failed" % t.get("component"))
    if failed:
        return "FAIL", reasons
    if wipe_state != "WIPED":
        reasons.append("Wipe not done (%s)" % wipe_state.lower())
    for t in tests:
        if "Not tested" in (t.get("notes") or []):
            reasons.append("%s not tested" % t.get("component"))
    if reasons:
        return "INCOMPLETE", reasons
    return "PASS", []


def render_txt(rep):
    L = []
    add = L.append
    add("LAPTOPSEAL  ITAD REPORT")
    add("RESULT: %s" % rep["overall"])
    for r in rep["reasons"]:
        add("  - %s" % r)
    add("=" * 60)
    for k, v in (("Job ID", rep["job_id"]), ("Asset ID", rep["asset_id"]),
                 ("Serial", rep["serial"]), ("Technician", rep["technician"]),
                 ("Location", rep["location"]), ("Date/time", rep["time"]),
                 ("Make / model", rep["make_model"]), ("CPU", rep["cpu"]),
                 ("RAM", rep["ram"]), ("Screen", rep.get("screen")),
                 ("Storage", rep["storage"])):
        add("%-14s: %s" % (k, v or "-"))
    add("")
    add("STAGES")
    add("%-14s: %s" % ("Absolute lock", rep["absolute"]))
    add("%-14s: %s" % ("Enrolment", rep["enrolment"]))
    add("%-14s: %s" % ("Wipe", rep["wipe_state"]))
    for d in rep["wipe_devices"]:
        add("   %s  %s  method=%s  verified=%s" % (
            d.get("path"), d.get("model") or "?", d.get("achieved", "-"),
            "yes" if (d.get("verification") or {}).get("verified") else "no"))
    if rep.get("certificate"):
        add("   certificate sha256: %s" % rep["certificate"])
    add("")
    add("COMPONENTS")
    for t in rep["tests"]:
        note = "; ".join(t.get("notes") or [])
        add("%-14s %-6s %s" % (t.get("component"), t.get("status"), note))
    add("")
    add("Tool: LaptopSeal window v%s" % VERSION)
    return "\n".join(L) + "\n"


def write_pdf(path, rep):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)
    st = getSampleStyleSheet()
    col = {"PASS": "#1FA971", "FAIL": "#C62F2F"}.get(rep["overall"], "#B8860B")
    doc = SimpleDocTemplate(path, pagesize=A4, title="LaptopSeal ITAD report")
    el = [Paragraph("LaptopSeal - ITAD report", st["Title"]),
          Paragraph('<font color="%s"><b>RESULT: %s</b></font>'
                    % (col, rep["overall"]), st["Heading2"])]
    for r in rep["reasons"]:
        el.append(Paragraph("- %s" % r, st["Normal"]))
    el.append(Spacer(1, 10))
    rows = [[k, v or "-"] for k, v in (
        ("Job ID", rep["job_id"]), ("Asset ID", rep["asset_id"]),
        ("Serial", rep["serial"]), ("Technician", rep["technician"]),
        ("Location", rep["location"]), ("Date/time", rep["time"]),
        ("Make / model", rep["make_model"]), ("CPU", rep["cpu"]),
        ("RAM", rep["ram"]), ("Screen", rep.get("screen")),
        ("Storage", rep["storage"]),
        ("Absolute lock", rep["absolute"]), ("Enrolment", rep["enrolment"]),
        ("Wipe", rep["wipe_state"]))]
    t = Table(rows, colWidths=[110, 380])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                           ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    el += [t, Spacer(1, 12), Paragraph("Components", st["Heading3"])]
    rows = [["Component", "Status", "Notes"]]
    for x in rep["tests"]:
        rows.append([x.get("component"), x.get("status"),
                     Paragraph("; ".join(x.get("notes") or []) or "-",
                               st["Normal"])])
    t2 = Table(rows, colWidths=[100, 50, 340])
    t2.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                            ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    el.append(t2)
    if rep.get("certificate"):
        el += [Spacer(1, 8), Paragraph("Wipe certificate SHA-256: %s"
                                       % rep["certificate"], st["Normal"])]
    doc.build(el)


def fallback_terminal(reason, dry_run=False):
    print("LaptopSeal window not available (%s). Starting terminal version."
          % reason)
    args = [PY, os.path.join(TOOLS_DIR, "itad_run.py")]
    if dry_run:
        args.append("--dry-run")
    os.execv(PY, args)


# =============================================================================
# the window
# =============================================================================
class App:
    def __init__(self, tk, ttk, messagebox, root, dry_run, quick, windowed):
        self.tk, self.ttk, self.mb, self.root = tk, ttk, messagebox, root
        self.dry, self.quick = dry_run, quick
        self.q = queue.Queue()
        self.log = []
        self.wiping = False
        self.out_dir, self.persistent = pick_out_dir()
        self.p_check = os.path.join(self.out_dir, "laptop_check.json")
        self.p_wipe_dry = os.path.join(self.out_dir, "laptop_wipe_preview.json")
        self.p_wipe = os.path.join(self.out_dir, "laptop_wipe.json")
        self.p_test = os.path.join(self.out_dir, "laptop_test.json")
        self.ident, self.disks = quick_identity()
        self.ram = ram_details()
        self.screen = screen_details()
        ser = self.ident.get("serial", "")
        self.s = {"job_id": "JOB-" + now().strftime("%Y%m%d-%H%M%S"),
                  "serial": ser if valid_serial(ser) else "",
                  "check": None, "wipe_dry": None, "wipe": None,
                  "wipe_state": "NOT RUN", "auto": [], "manual": {}}
        self.pages, self.step_labels, self.prog = {}, [], {}
        self.test_rows, self.manual_btns = {}, {}
        self.fails, self.locked_until = 0, 0.0
        root.title("LaptopSeal")
        root.configure(bg=PANEL)
        if not windowed:
            try:
                root.attributes("-fullscreen", True)
            except Exception:
                pass
        else:
            root.geometry("1100x700")
        self.setup_style()
        self.build_shell()
        self.build_login()
        self.build_start()
        self.build_checks()
        self.build_wipe()
        self.build_report()
        # "Next laptop" restarts the window but keeps the technician
        # signed in (only if that account still has a password).
        keep = os.environ.get("ITAD_SIGNED_IN", "")
        acc = load_accounts(self.out_dir).get(keep.lower())
        if keep and acc and acc["hash"]:
            self.signed_in(acc["name"])
        else:
            os.environ.pop("ITAD_SIGNED_IN", None)
            self.show_login()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.bind("<Control-q>", lambda e: self.quit())
        root.bind("<Escape>", lambda e: self.toggle_fullscreen())
        root.after(100, self.pump)

    # ---------- small widget helpers ----------
    def F(self, size, bold=False):
        return (FONT, size, "bold") if bold else (FONT, size)

    def lbl(self, parent, text="", size=11, bold=False, fg=TEXT, bg=None, **kw):
        return self.tk.Label(parent, text=text, font=self.F(size, bold), fg=fg,
                             bg=bg if bg else NAVY, anchor="w", justify="left",
                             **kw)

    def btn(self, parent, text, cmd, kind="primary"):
        bg = {"primary": BLUE, "ghost": BORDER, "danger": "#8A2A2A"}[kind]
        return self.tk.Button(parent, text=text, command=cmd, bg=bg, fg="white",
                              activebackground=CYAN, activeforeground=NAVY,
                              disabledforeground="#6F86A8", relief="flat", bd=0,
                              padx=18, pady=9, font=self.F(11, True),
                              cursor="hand2")

    def card(self, parent, title):
        tk = self.tk
        outer = tk.Frame(parent, bg=NAVY, highlightbackground=BORDER,
                         highlightthickness=1)
        tk.Label(outer, text=title.upper(), font=self.F(9, True), fg=MUTED,
                 bg=NAVY, anchor="w").pack(fill="x", padx=14, pady=(10, 2))
        inner = tk.Frame(outer, bg=NAVY)
        inner.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        return outer, inner

    def entry(self, parent, var, readonly=False, secret=False):
        return self.tk.Entry(parent, textvariable=var, bg=PANEL, fg=TEXT,
                             insertbackground=TEXT, relief="flat",
                             font=self.F(12), highlightthickness=1,
                             highlightbackground=BORDER, highlightcolor=CYAN,
                             readonlybackground=BORDER, disabledforeground=MUTED,
                             show="•" if secret else "",
                             state="readonly" if readonly else "normal")

    def setup_style(self):
        ttk = self.ttk
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("TCombobox", fieldbackground=PANEL, background=BORDER,
                     foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL)],
               foreground=[("readonly", TEXT)],
               selectbackground=[("readonly", PANEL)],
               selectforeground=[("readonly", TEXT)])
        st.configure("Seal.Horizontal.TProgressbar", troughcolor=BORDER,
                     background=CYAN, bordercolor=BORDER, lightcolor=CYAN,
                     darkcolor=CYAN)
        for opt, val in (("*TCombobox*Listbox.background", PANEL),
                         ("*TCombobox*Listbox.foreground", TEXT),
                         ("*TCombobox*Listbox.selectBackground", BLUE)):
            self.root.option_add(opt, val)

    def build_shell(self):
        tk, root = self.tk, self.root
        head = tk.Frame(root, bg="white")
        head.pack(fill="x")
        tk.Label(head, text="Laptop", font=self.F(22, True), fg=NAVY,
                 bg="white").pack(side="left", padx=(22, 0), pady=10)
        tk.Label(head, text="Seal", font=self.F(22, True), fg=BLUE,
                 bg="white").pack(side="left", pady=10)
        tk.Label(head, text="Test. Wipe. Verify. Certify.   v%s" % VERSION,
                 font=self.F(10), fg="#4A5D78", bg="white").pack(
            side="right", padx=22)
        if self.dry:
            tk.Label(head, text="  DRY RUN - nothing will be erased  ",
                     font=self.F(10, True), fg=NAVY, bg=WARN).pack(
                side="right", padx=10)
        tk.Frame(root, bg=CYAN, height=3).pack(fill="x")
        tabs = tk.Frame(root, bg=DEEP)
        tabs.pack(fill="x")
        for name in STEPS:
            lab = tk.Label(tabs, text=name, font=self.F(11), fg="#A7BAD4",
                           bg=DEEP, padx=20, pady=9)
            lab.pack(side="left", padx=(8, 0), pady=(8, 0))
            self.step_labels.append(lab)
        self.content = tk.Frame(root, bg=PANEL)
        self.content.pack(fill="both", expand=True)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

    def new_page(self, name):
        f = self.tk.Frame(self.content, bg=PANEL)
        f.grid(row=0, column=0, sticky="nsew")
        self.pages[name] = f
        return f

    def bar(self, page):
        b = self.tk.Frame(page, bg=PANEL)
        b.pack(side="bottom", fill="x", padx=24, pady=(6, 16))
        left = self.tk.Frame(b, bg=PANEL)
        left.pack(side="left")
        right = self.tk.Frame(b, bg=PANEL)
        right.pack(side="right")
        mid = self.lbl(b, "", 10, fg=MUTED, bg=PANEL)
        mid.pack(side="left", padx=18)
        return left, right, mid

    def show(self, name):
        self.pages[name].tkraise()

    def set_step(self, i):
        for k, lab in enumerate(self.step_labels):
            if k == i:
                lab.configure(bg=PANEL, fg="white", font=self.F(11, True))
            else:
                lab.configure(bg=DEEP, fg="#A7BAD4", font=self.F(11))

    def toggle_fullscreen(self):
        try:
            cur = bool(self.root.attributes("-fullscreen"))
            self.root.attributes("-fullscreen", not cur)
        except Exception:
            pass

    def set_prog(self, name, text, color=MUTED):
        lab = self.prog.get(name)
        if lab:
            lab.configure(text=text, fg=color)

    # ---------- running the other tools ----------
    def pump(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def run_stage(self, cmd, on_line, on_done):
        def work():
            master, slave = pty.openpty()
            env = dict(os.environ)
            env.update(NO_COLOR="1", TERM="xterm", PYTHONUNBUFFERED="1")
            try:
                p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                     stdout=slave, stderr=slave, env=env,
                                     close_fds=True)
            except Exception as e:
                os.close(slave)
                os.close(master)
                msg = str(e)
                self.q.put(lambda: self._line(on_line, "ERROR: " + msg))
                self.q.put(lambda: on_done(127))
                return
            os.close(slave)
            buf = b""
            while True:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                parts = re.split(rb"[\r\n]", buf)
                buf = parts.pop()
                for part in parts:
                    ln = ANSI_RE.sub("", part.decode("utf-8", "replace")).strip()
                    if ln:
                        self.q.put(lambda l=ln: self._line(on_line, l))
            rc = p.wait()
            try:
                os.close(master)
            except OSError:
                pass
            self.q.put(lambda: on_done(rc))
        threading.Thread(target=work, daemon=True).start()

    def _line(self, on_line, ln):
        self.log.append(ln)
        on_line(ln)

    def common_args(self):
        return ["--job-id", self.job(), "--asset-id", self.asset(),
                "--tech", self.tech(), "--no-color"]

    def job(self):
        return self.v_job.get().strip()

    def asset(self):
        return self.v_asset.get().strip()

    def tech(self):
        return self.v_tech.get().strip()

    # ---------- screen 1: sign in ----------
    def build_login(self):
        tk = self.tk
        pg = self.new_page("login")
        self.v_user = tk.StringVar(value="")
        self.v_pass = tk.StringVar(value="")
        mid = tk.Frame(pg, bg=PANEL)
        mid.place(relx=0.5, rely=0.42, anchor="center")
        box = tk.Frame(mid, bg=NAVY, highlightbackground=BORDER,
                       highlightthickness=1)
        box.pack()
        inner = tk.Frame(box, bg=NAVY)
        inner.pack(padx=40, pady=30)
        self.lbl(inner, "Technician sign in", 20, True).pack(anchor="w")
        self.lbl(inner, "Sign in with your own name and password.", 10,
                 fg=MUTED).pack(anchor="w", pady=(4, 18))
        self.lbl(inner, "NAME", 9, True, fg=MUTED).pack(anchor="w")
        self.l_user = self.entry(inner, self.v_user)
        self.l_user.configure(width=32)
        self.l_user.pack(fill="x", pady=(2, 12), ipady=4)
        self.lbl(inner, "PASSWORD", 9, True, fg=MUTED).pack(anchor="w")
        self.l_pass = self.entry(inner, self.v_pass, secret=True)
        self.l_pass.pack(fill="x", pady=(2, 6), ipady=4)
        self.l_msg = self.lbl(inner, "", 10, fg=WARN, wraplength=380)
        self.l_msg.pack(anchor="w", pady=(4, 10))
        self.l_go = self.btn(inner, "Sign in", self.do_login)
        self.l_go.pack(fill="x")
        self.l_user.bind("<Return>", lambda e: self.l_pass.focus_set())
        self.l_pass.bind("<Return>", lambda e: self.do_login())
        left, right, msg = self.bar(pg)
        self.btn(left, "Power off", self.power_off, "ghost").pack()
        self.btn(right, "Add technician", self.add_tech_dialog,
                 "ghost").pack()
        self.l_where = msg

    def show_login(self):
        self.v_tech.set("")
        self.v_pass.set("")
        acc = load_accounts(self.out_dir)
        ready = [a for a in acc.values() if a["hash"]]
        if not ready:
            self.l_msg.configure(
                text="No technician accounts yet. Press 'Add technician' "
                     "(bottom right) to create the first one.", fg=WARN)
        else:
            self.l_msg.configure(text="", fg=WARN)
        self.l_where.configure(
            text="" if self.persistent else
            "No ITAD_DATA stick: new accounts and reports are lost at "
            "power off.", fg=WARN)
        self.show("login")
        self.set_step(0)
        self.root.after(200, self.l_user.focus_set)

    def do_login(self):
        left = self.locked_until - time.time()
        if left > 0:
            self.l_msg.configure(text="Too many wrong tries. Wait %d seconds."
                                 % (int(left) + 1), fg=BAD)
            return
        name = self.v_user.get().strip()
        pw = self.v_pass.get()
        if not name or not pw:
            self.l_msg.configure(text="Type your name and password.", fg=WARN)
            return
        acc = load_accounts(self.out_dir).get(name.lower())
        if acc and acc["hash"] and verify_password(pw, acc["hash"]):
            self.fails = 0
            self.v_pass.set("")
            self.signed_in(acc["name"])
            return
        self.v_pass.set("")
        self.fails += 1
        if acc and not acc["hash"]:
            text = ("%s has no password yet. Use 'Add technician' to set "
                    "one." % acc["name"])
        else:
            text = "Wrong name or password."
        if self.fails >= MAX_TRIES:
            self.fails = 0
            self.locked_until = time.time() + LOCK_SECONDS
            text += "  Locked for %d seconds." % LOCK_SECONDS
        self.l_msg.configure(text=text, fg=BAD)
        self.l_pass.focus_set()

    def signed_in(self, name):
        self.v_tech.set(name)
        os.environ["ITAD_SIGNED_IN"] = name
        self.start_msg.configure(text="")
        self.show("start")
        self.set_step(1)

    def sign_out(self):
        os.environ.pop("ITAD_SIGNED_IN", None)
        self.v_user.set("")
        self.show_login()

    def add_tech_dialog(self):
        tk = self.tk
        acc = load_accounts(self.out_dir)
        approvers = {k: a for k, a in acc.items() if a["hash"]}
        need_ok = bool(approvers)
        win = tk.Toplevel(self.root)
        win.title("Add technician")
        win.configure(bg=NAVY)
        win.transient(self.root)
        inner = tk.Frame(win, bg=NAVY)
        inner.pack(padx=30, pady=24)
        self.lbl(inner, "Add or change a technician", 16, True).pack(
            anchor="w")
        self.lbl(inner, "The password is saved as a secure hash, never as "
                        "plain text.", 10, fg=MUTED).pack(anchor="w",
                                                          pady=(2, 14))
        v = {k: tk.StringVar(value="") for k in
             ("name", "pw1", "pw2", "by", "bypw")}
        fields = [("NEW TECHNICIAN NAME", "name", False),
                  ("PASSWORD (at least %d characters)" % MIN_PASSWORD,
                   "pw1", True),
                  ("TYPE THE PASSWORD AGAIN", "pw2", True)]
        if need_ok:
            fields += [("APPROVED BY (an existing technician)", "by", False),
                       ("THEIR PASSWORD", "bypw", True)]
        first = None
        for label, key, secret in fields:
            self.lbl(inner, label, 9, True, fg=MUTED).pack(anchor="w")
            e = self.entry(inner, v[key], secret=secret)
            e.configure(width=34)
            e.pack(fill="x", pady=(2, 10), ipady=3)
            first = first or e
        msg = self.lbl(inner, "" if need_ok else
                       "This is the first account, so no approval is "
                       "needed.", 10, fg=MUTED, wraplength=360)
        msg.pack(anchor="w", pady=(0, 10))

        def save():
            name = v["name"].get().strip()
            p1, p2 = v["pw1"].get(), v["pw2"].get()
            if not NAME_RE.match(name):
                msg.configure(text="Name: 2-30 letters or numbers (spaces, "
                                   "dot, dash allowed).", fg=BAD)
                return
            if len(p1) < MIN_PASSWORD:
                msg.configure(text="Password is too short.", fg=BAD)
                return
            if p1 != p2:
                msg.configure(text="The two passwords are not the same.",
                              fg=BAD)
                return
            if need_ok:
                a = approvers.get(v["by"].get().strip().lower())
                if not a or not verify_password(v["bypw"].get(), a["hash"]):
                    v["bypw"].set("")
                    msg.configure(text="Approval name or password is wrong.",
                                  fg=BAD)
                    return
            try:
                path = save_account(self.out_dir, name, p1)
            except Exception as e:
                msg.configure(text="Could not save: %s" % e, fg=BAD)
                return
            for k in v:
                v[k].set("")
            win.destroy()
            note = "Saved %s in %s." % (name, path)
            if not self.persistent:
                note += ("  WARNING: no ITAD_DATA stick, this account is "
                         "lost at power off.")
            self.v_user.set(name)
            self.l_msg.configure(text=note + "  Now sign in.",
                                 fg=OK if self.persistent else WARN)
            self.l_pass.focus_set()

        bf = tk.Frame(inner, bg=NAVY)
        bf.pack(fill="x")
        self.btn(bf, "Save", save).pack(side="left")
        self.btn(bf, "Cancel", win.destroy, "ghost").pack(side="left",
                                                           padx=8)
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry("+%d+%d" % (max(0, (sw - w) // 2), max(0, (sh - h) // 2)))
        try:
            win.grab_set()
        except Exception:
            pass
        first.focus_set()

    # ---------- screen 2: job ----------
    def build_start(self):
        tk = self.tk
        pg = self.new_page("start")
        self.v_tech = tk.StringVar(value="")
        self.v_job = tk.StringVar(value=self.s["job_id"])
        self.v_asset = tk.StringVar(value=self.s["serial"])
        self.v_loc = tk.StringVar(value=LOCATIONS[0])
        form = tk.Frame(pg, bg=PANEL)
        form.pack(fill="x", padx=24, pady=(18, 8))
        for c in range(4):
            form.grid_columnconfigure(c, weight=1, uniform="f")
        fields = [("Technician (signed in)", "entry", self.v_tech, None, True),
                  ("Job ID", "entry", self.v_job, None, False),
                  ("Asset ID", "entry", self.v_asset, None,
                   bool(self.s["serial"])),
                  ("Location", "combo", self.v_loc, LOCATIONS, False)]
        for c, (name, kind, var, vals, ro) in enumerate(fields):
            cell = tk.Frame(form, bg=NAVY, highlightbackground=BORDER,
                            highlightthickness=1)
            cell.grid(row=0, column=c, sticky="nsew", padx=5)
            tk.Label(cell, text=name.upper(), font=self.F(9, True), fg=MUTED,
                     bg=NAVY, anchor="w").pack(fill="x", padx=12, pady=(10, 2))
            if kind == "combo":
                w = self.ttk.Combobox(cell, textvariable=var, values=vals,
                                      state="readonly" if ro else "normal",
                                      font=self.F(12))
            else:
                w = self.entry(cell, var, readonly=ro)
            w.pack(fill="x", padx=12, pady=(0, 12))
        hint = ("Asset ID is filled from the serial number."
                if self.s["serial"] else
                "No usable serial number found: type the Asset ID.")
        self.lbl(pg, hint, 10, fg=MUTED, bg=PANEL).pack(
            fill="x", padx=30, pady=(0, 6))
        body = tk.Frame(pg, bg=PANEL)
        body.pack(fill="both", expand=True, padx=24, pady=6)
        dev_outer, dev = self.card(body, "Detected laptop")
        dev_outer.pack(side="left", fill="both", expand=True, padx=5)
        i = self.ident
        stor = ["%s (%s) %s GB" % (d.get("model") or "Drive",
                                   d.get("drive_type", "?"), d.get("size_gb"))
                for d in self.disks] or ["none found"]
        rows = [("Type", i.get("device_type") or "-"),
                ("Make", i.get("manufacturer") or "-"),
                ("Model", i.get("model") or "-"),
                ("Serial", self.s["serial"] or "not found"),
                ("CPU", i.get("cpu") or "-"),
                ("RAM", ram_text(self.ram)),
                ("Screen", screen_text(self.screen)),
                ("Storage", "\n".join(stor)),
                ("BIOS / boot", "%s %s  %s" % (i.get("bios_vendor", ""),
                                               i.get("bios_version", ""),
                                               i.get("boot_mode", "")))]
        for r, (k, v) in enumerate(rows):
            self.lbl(dev, k, 10, fg=MUTED).grid(row=r, column=0, sticky="nw",
                                                 padx=(0, 14), pady=2)
            self.lbl(dev, v, 11, True, wraplength=520).grid(
                row=r, column=1, sticky="nw", pady=2)
        pr_outer, pr = self.card(body, "Progress")
        pr_outer.pack(side="left", fill="y", padx=5)
        for r, name in enumerate(("Absolute", "Enrolment", "Wipe", "Tests",
                                  "Report")):
            self.lbl(pr, name, 11).grid(row=r, column=0, sticky="w",
                                        padx=(0, 20), pady=5)
            lab = self.lbl(pr, "waiting", 11, fg=MUTED)
            lab.grid(row=r, column=1, sticky="w", pady=5)
            self.prog[name] = lab
        left, right, mid = self.bar(pg)
        self.btn(left, "Power off", self.power_off, "ghost").pack(
            side="left")
        self.btn(left, "Sign out", self.sign_out, "ghost").pack(
            side="left", padx=(10, 0))
        self.start_msg = mid
        self.btn(right, "Start checks", self.start_checks).pack()

    def start_checks(self):
        if not self.tech():
            self.show_login()
            return
        if not self.job() or not self.asset():
            self.start_msg.configure(text="Job ID and Asset ID are needed.",
                                     fg=WARN)
            return
        self.start_msg.configure(text="")
        self.run_check()

    # ---------- screen 2: checks ----------
    def build_checks(self):
        tk = self.tk
        pg = self.new_page("checks")
        self.c_banner = tk.Label(pg, text="Checking...", font=self.F(20, True),
                                 fg=TEXT, bg=BORDER, pady=14)
        self.c_banner.pack(fill="x", padx=24, pady=(18, 10))
        row = tk.Frame(pg, bg=PANEL)
        row.pack(fill="both", expand=True, padx=24)
        a_outer, a = self.card(row, "1  Absolute lock")
        a_outer.pack(side="left", fill="both", padx=5)
        self.c_abs = self.lbl(a, "-", 20, True, fg=MUTED)
        self.c_abs.pack(anchor="w")
        self.c_abs_note = self.lbl(a, "", 10, fg=MUTED, wraplength=260)
        self.c_abs_note.pack(anchor="w", pady=(8, 0))
        e_outer, e = self.card(row, "2  Enrolment (Intune / Autopilot / SCCM)")
        e_outer.pack(side="left", fill="both", expand=True, padx=5)
        self.c_enr = self.lbl(e, "-", 20, True, fg=MUTED)
        self.c_enr.pack(anchor="w")
        self.c_enr_note = self.lbl(e, "", 11, wraplength=560)
        self.c_enr_note.pack(anchor="w", pady=(8, 0))
        left, right, mid = self.bar(pg)
        self.btn(left, "Power off", self.power_off, "ghost").pack()
        self.c_status = mid
        self.c_again = self.btn(right, "Run check again", self.run_check, "ghost")
        self.c_again.pack(side="left", padx=(0, 10))
        self.c_next = self.btn(right, "Continue to wipe", self.enter_wipe)
        self.c_next.pack(side="left")

    def banner(self, text, kind):
        bg, fg = {"ok": (GREEN_BAN, NAVY), "bad": (RED_BAN, "white"),
                  "warn": (WARN, NAVY), "wait": (BORDER, TEXT)}[kind]
        self.c_banner.configure(text=text, bg=bg, fg=fg)

    def run_check(self):
        self.show("checks")
        self.set_step(2)
        self.banner("Checking...", "wait")
        for w, t in ((self.c_abs, "-"), (self.c_enr, "-")):
            w.configure(text=t, fg=MUTED)
        self.c_abs_note.configure(text="")
        self.c_enr_note.configure(text="")
        self.c_again.configure(state="disabled")
        self.c_next.configure(state="disabled")
        self.set_prog("Absolute", "checking...", CYAN)
        self.set_prog("Enrolment", "checking...", CYAN)
        if os.path.exists(self.p_check):
            try:
                os.remove(self.p_check)
            except OSError:
                pass
        cmd = [PY, CHECK] + self.common_args() + ["--json", self.p_check]
        self.run_stage(cmd, lambda ln: self.c_status.configure(
            text=ln[:90], fg=MUTED), self.check_done)

    def check_done(self, rc):
        data = load_json(self.p_check)
        self.s["check"] = data
        self.c_status.configure(text="")
        self.c_again.configure(state="normal")
        if not data:
            self.banner("CHECK DID NOT RUN", "bad")
            self.c_enr_note.configure(text="\n".join(self.log[-6:]) or
                                      "laptop_check.py gave no result.")
            self.set_prog("Absolute", "error", BAD)
            self.set_prog("Enrolment", "error", BAD)
            return
        result = data.get("result", "?")
        ar = data.get("absolute_result", "?")
        er = data.get("autopilot_result", "?")
        colors = {"CLEAR": OK, "CLEAN": OK, "BLOCKED": BAD}
        self.c_abs.configure(text=ar, fg=colors.get(ar, WARN))
        enr_text = {"CLEAN": "CLEAN", "BLOCKED": "ACTIVE ENROLMENT",
                    "UNKNOWN": "NOT CHECKED"}.get(er, "PAST ENROLMENT")
        self.c_enr.configure(text=enr_text, fg=colors.get(er, WARN))
        self.set_prog("Absolute", ar, colors.get(ar, WARN))
        self.set_prog("Enrolment", enr_text, colors.get(er, WARN))
        reasons = data.get("block_reasons") or (
            list(data.get("absolute_signs") or [])
            + [e.get("description", "") for e in
               (data.get("autopilot_analysis") or {}).get("active", [])])
        if data.get("absolute_signs"):
            self.c_abs_note.configure(text="\n".join(data["absolute_signs"][:4]))
        if result == "BLOCKED":
            self.banner("BLOCKED  -  DO NOT WIPE", "bad")
            self.c_enr_note.configure(
                text="Why it is blocked:\n- " + "\n- ".join(reasons[:6] or ["see report"])
                + "\n\nAsk the customer to remove this laptop from their "
                  "portal, then run the check again.")
        elif result == "CLEAR":
            self.banner("CLEAR  -  ready to wipe", "ok")
            self.c_enr_note.configure(
                text="No on-disk sign of Autopilot or MDM.\n\nStill check the "
                     "serial number in the customer's Intune / ABM portal "
                     "before resale.")
            self.c_next.configure(state="normal")
        else:
            self.banner("CANNOT CHECK  -  manager decision needed", "warn")
            notes = (data.get("notes") or []) + (data.get("warnings") or [])
            self.c_enr_note.configure(
                text="\n".join(notes[:6] or ["Some drives could not be read."]))

    # ---------- screen 3: wipe and tests ----------
    def build_wipe(self):
        tk = self.tk
        pg = self.new_page("wipe")
        row = tk.Frame(pg, bg=PANEL)
        row.pack(fill="both", expand=True, padx=24, pady=(18, 6))
        w_outer, w = self.card(row, "3  Wipe")
        w_outer.pack(side="left", fill="both", padx=5)
        self.w_title = self.lbl(w, "Checking drives...", 15, True)
        self.w_title.pack(anchor="w")
        self.w_targets = self.lbl(w, "", 10, fg=MUTED, wraplength=380)
        self.w_targets.pack(anchor="w", pady=(6, 10))
        self.w_progress = tk.Frame(w, bg=NAVY)
        self.w_pbar = self.ttk.Progressbar(
            self.w_progress, style="Seal.Horizontal.TProgressbar",
            mode="determinate", maximum=100, length=380)
        self.w_pbar.pack(anchor="w")
        self.w_pct = self.lbl(self.w_progress, "0%", 10, fg=MUTED)
        self.w_pct.pack(anchor="w", pady=(4, 0))
        self.w_confirm = tk.Frame(w, bg=NAVY)
        self.w_ask = self.lbl(self.w_confirm, "", 10, fg=WARN, wraplength=380)
        self.w_ask.pack(anchor="w")
        self.v_confirm = tk.StringVar(value="")
        self.w_entry = self.entry(self.w_confirm, self.v_confirm)
        self.w_entry.pack(fill="x", pady=8)
        self.v_confirm.trace_add("write", lambda *a: self.update_confirm())
        btns = tk.Frame(self.w_confirm, bg=NAVY)
        btns.pack(anchor="w")
        self.w_go = self.btn(btns, "Wipe now", self.do_wipe, "danger")
        self.w_go.pack(side="left", padx=(0, 8))
        self.w_skip = self.btn(btns, "Skip wipe", self.skip_wipe, "ghost")
        self.w_skip.pack(side="left")
        t_outer, t = self.card(row, "4  Component tests")
        t_outer.pack(side="left", fill="both", expand=True, padx=5)
        self.t_status = self.lbl(t, "Waiting for the wipe step...", 10, fg=MUTED)
        self.t_status.grid(row=0, column=0, columnspan=2, sticky="w",
                           pady=(0, 6))
        for r, comp in enumerate(COMPONENTS, start=1):
            self.lbl(t, comp, 11).grid(row=r, column=0, sticky="w",
                                       padx=(0, 24), pady=2)
            lab = self.lbl(t, "waiting", 11, True, fg=MUTED)
            lab.grid(row=r, column=1, sticky="w", pady=2)
            self.test_rows[comp] = lab
        mrow = tk.Frame(t, bg=NAVY)
        mrow.grid(row=len(COMPONENTS) + 1, column=0, columnspan=2, sticky="w",
                  pady=(12, 0))
        for comp, fn in (("Screen", self.manual_screen),
                         ("Keyboard", self.manual_keyboard),
                         ("Speakers", self.manual_speakers)):
            b = self.btn(mrow, comp + " test", fn, "ghost")
            b.configure(state="disabled")
            b.pack(side="left", padx=(0, 8))
            self.manual_btns[comp] = b
        left, right, mid = self.bar(pg)
        self.w_power = self.btn(left, "Power off", self.power_off, "ghost")
        self.w_power.pack()
        self.w_status = mid
        self.w_next = self.btn(right, "Continue to report", self.finish)
        self.w_next.configure(state="disabled")
        self.w_next.pack()

    def set_row(self, comp, status):
        lab = self.test_rows.get(comp)
        if not lab:
            return
        text = {"RUN": "running...", "WAIT": "waiting", "DONE": "done",
                "PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL",
                "SKIP": "skipped"}.get(status, status)
        color = {"RUN": CYAN, "PASS": OK, "WARN": WARN,
                 "FAIL": BAD}.get(status, MUTED)
        lab.configure(text=text, fg=color)

    def serial(self):
        chk = self.s.get("check") or {}
        ser = (chk.get("identity") or {}).get("serial") or self.s["serial"]
        return ser if valid_serial(ser) else ""

    def hide_all_wipe_parts(self):
        self.w_progress.pack_forget()
        self.w_confirm.pack_forget()

    def enter_wipe(self):
        self.show("wipe")
        self.set_step(3)
        self.set_prog("Wipe", "checking drives...", CYAN)
        self.w_title.configure(text="Checking drives...", fg=TEXT)
        self.w_targets.configure(text="")
        self.hide_all_wipe_parts()
        cmd = ([PY, WIPE] + self.common_args() +
               ["--json", self.p_wipe_dry, "--check-report", self.p_check,
                "--method", "auto"])
        self.run_stage(cmd, lambda ln: self.w_status.configure(
            text=ln[:90], fg=MUTED), self.wipe_preview_done)

    def wipe_preview_done(self, rc):
        data = load_json(self.p_wipe_dry) or {}
        self.s["wipe_dry"] = data
        self.w_status.configure(text="")
        devs = data.get("devices") or []
        self.w_confirm.pack(anchor="w", fill="x")
        if not devs:
            why = list(data.get("refusals") or [])
            if data.get("check_report"):
                why.append(str(data["check_report"]))
            self.w_title.configure(text="The wipe is not possible", fg=BAD)
            self.w_targets.configure(
                text="\n".join(why[:5] or ["No internal drive can be wiped."]))
            self.w_ask.configure(text="You can skip the wipe and run the "
                                      "tests only. The report will say "
                                      "the laptop was NOT wiped.")
            self.w_entry.pack_forget()
            self.w_go.pack_forget()
            self.set_prog("Wipe", "not possible", BAD)
            return
        lines = ["%s   %s   %s GB   SN %s" % (
            d.get("path"), d.get("model") or "?", d.get("size_gb"),
            d.get("serial") or "-") for d in devs]
        self.w_targets.configure(text="These drives will be ERASED:\n" +
                                 "\n".join(lines))
        self.set_prog("Wipe", "ready", WARN)
        if self.dry:
            self.w_title.configure(text="Dry run: nothing will be erased",
                                   fg=WARN)
            self.w_ask.configure(text="Dry run mode is on.")
            self.w_entry.pack_forget()
            self.w_go.configure(text="Continue (dry run)", state="normal")
            return
        self.w_title.configure(text="Ready to wipe", fg=WARN)
        self.need = (self.serial()[-4:].upper() if self.serial() else "WIPE")
        self.w_ask.configure(
            text=("Serial %s.  Type the last 4 characters of the serial "
                  "number to allow the wipe:" % self.serial())
            if self.serial() else
            "Serial unknown.  Type WIPE to allow the wipe:")
        self.v_confirm.set("")
        self.w_go.configure(text="Wipe now", state="disabled")

    def update_confirm(self):
        if self.dry:
            return
        ok = self.v_confirm.get().strip().upper() == getattr(self, "need", "?")
        self.w_go.configure(state="normal" if ok else "disabled")

    def skip_wipe(self):
        if not self.mb.askyesno("Skip wipe", "Skip the wipe?\n\nThe report "
                                "will say this laptop was NOT wiped."):
            return
        self.s["wipe_state"] = "SKIPPED"
        self.set_prog("Wipe", "skipped", WARN)
        self.w_title.configure(text="Wipe skipped", fg=WARN)
        self.hide_all_wipe_parts()
        self.start_auto_tests()

    def do_wipe(self):
        if self.dry:
            self.s["wipe_state"] = "DRY RUN"
            self.s["wipe"] = self.s.get("wipe_dry")
            self.set_prog("Wipe", "dry run", WARN)
            self.w_title.configure(text="Dry run done (nothing erased)", fg=WARN)
            self.hide_all_wipe_parts()
            self.start_auto_tests()
            return
        self.wiping = True
        self.hide_all_wipe_parts()
        self.w_progress.pack(anchor="w")
        self.w_pbar.configure(value=0)
        self.w_pct.configure(text="0%")
        self.w_title.configure(text="Wiping... do not power off", fg=WARN)
        self.w_power.configure(state="disabled")
        self.set_prog("Wipe", "wiping...", CYAN)
        cmd = ([PY, WIPE] + self.common_args() +
               ["--json", self.p_wipe, "--check-report", self.p_check,
                "--method", "auto", "--commit", "--yes-i-mean-it"])
        self.run_stage(cmd, self.wipe_line, self.wipe_done)

    def wipe_line(self, ln):
        m = re.findall(r"(\d{1,3})%", ln)
        if m:
            pct = min(100, int(m[-1]))
            self.w_pbar.configure(value=pct)
            self.w_pct.configure(text="%d%%   %s" % (pct, ln[-50:]))
        self.w_status.configure(text=ln[:90], fg=MUTED)

    def wipe_done(self, rc):
        self.wiping = False
        self.w_power.configure(state="normal")
        data = load_json(self.p_wipe) or {}
        self.s["wipe"] = data
        self.w_status.configure(text="")
        if data.get("overall") == "PASS":
            self.s["wipe_state"] = "WIPED"
            self.w_title.configure(text="Wiped and verified", fg=OK)
            self.set_prog("Wipe", "WIPED", OK)
        else:
            self.s["wipe_state"] = "FAILED"
            self.w_title.configure(text="WIPE FAILED - do not ship", fg=BAD)
            self.set_prog("Wipe", "FAILED", BAD)
        self.start_auto_tests()

    def start_auto_tests(self):
        self.set_step(3)
        self.set_prog("Tests", "running...", CYAN)
        self.t_status.configure(text="Running automatic tests...", fg=CYAN)
        for comp in COMPONENTS:
            self.set_row(comp, "WAIT")
        self.w_status.configure(text="")
        cpu, ram = ("5", "0") if self.quick else ("30", "2")
        cmd = ([PY, HW_TEST] + self.common_args() +
               ["--json", self.p_test, "--test", AUTO_TESTS,
                "--no-interactive", "--cpu-load", cpu, "--ram-minutes", ram])
        self.cur_test = None
        self.run_stage(cmd, self.test_line, self.tests_done)

    def test_line(self, ln):
        m = re.search(r"\[(\d+)/(\d+)\]\s+([A-Z]+)", ln)
        if m:
            if self.cur_test:
                self.set_row(self.cur_test, "DONE")
            comp = BANNER_NAMES.get(m.group(3))
            if comp:
                self.cur_test = comp
                self.set_row(comp, "RUN")
                self.t_status.configure(
                    text="Running automatic tests... %s of %s" % (
                        m.group(1), m.group(2)), fg=CYAN)
        else:
            self.w_status.configure(text=ln[:90], fg=MUTED)

    def tests_done(self, rc):
        data = load_json(self.p_test) or {}
        self.s["auto"] = data.get("results") or []
        got = {}
        for r in self.s["auto"]:
            got[r.get("component")] = r
            self.set_row(r.get("component"), r.get("status", "WARN"))
        for comp in COMPONENTS:
            if comp not in MANUAL and comp not in got:
                self.set_row(comp, "WARN")
        self.w_status.configure(text="")
        self.t_status.configure(
            text="Automatic tests finished. Now run the 3 on-screen tests.",
            fg=TEXT)
        self.set_prog("Tests", "on-screen tests", WARN)
        for comp, b in self.manual_btns.items():
            b.configure(state="normal")
        self.w_next.configure(state="normal")

    # ---------- on-screen tests ----------
    def finish_manual(self, comp, status, notes):
        self.s["manual"][comp] = {"component": comp, "status": status,
                                  "notes": notes, "details": {}}
        self.set_row(comp, status)

    def manual_screen(self):
        tk = self.tk
        win = tk.Toplevel(self.root)
        try:
            win.attributes("-fullscreen", True)
        except Exception:
            pass
        st = {"i": 0, "fail": [], "seen": []}
        hint = tk.Label(win, text="", font=self.F(11))
        hint.place(relx=0.5, rely=0.98, anchor="s")

        def paint_now():
            name, col, fg = SCREEN_COLORS[st["i"]]
            win.configure(bg=col)
            hint.configure(text="%s    Enter / click = next    F = failure    "
                                "S = stop" % name, bg=col, fg=fg)

        def close():
            win.destroy()
            if st["fail"]:
                self.finish_manual("Screen", "FAIL",
                                   ["Problem on: " + ", ".join(st["fail"])])
            elif st["seen"]:
                self.finish_manual("Screen", "PASS",
                                   ["%d colour(s) shown" % len(st["seen"])])
            else:
                self.finish_manual("Screen", "WARN", ["Not tested"])

        def nxt(fail=False):
            name = SCREEN_COLORS[st["i"]][0]
            st["seen"].append(name)
            if fail:
                st["fail"].append(name)
            st["i"] += 1
            if st["i"] >= len(SCREEN_COLORS):
                close()
            else:
                paint_now()

        win.bind("<Return>", lambda e: nxt())
        win.bind("<space>", lambda e: nxt())
        win.bind("<Button-1>", lambda e: nxt())
        win.bind("f", lambda e: nxt(True))
        win.bind("F", lambda e: nxt(True))
        win.bind("s", lambda e: close())
        win.bind("S", lambda e: close())
        paint_now()
        win.focus_force()

    def manual_keyboard(self):
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.title("Keyboard test")
        win.configure(bg=PANEL)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = min(1000, sw), min(430, sh)
        win.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2),
                                      max(0, (sh - h) // 2)))
        tk.Label(win, text="Press every key. Keys turn green when they work.",
                 font=self.F(12, True), fg=TEXT, bg=PANEL).pack(pady=(14, 8))
        cells, seen = {}, set()
        for row in KB_ROWS:
            rf = tk.Frame(win, bg=PANEL)
            rf.pack(pady=3)
            for label, kid in row:
                c = tk.Label(rf, text=label, font=self.F(10, True), fg=TEXT,
                             bg=BORDER, width=max(3, len(label) + 1), pady=6)
                c.pack(side="left", padx=2)
                cells[kid] = c
        counter = tk.Label(win, text="", font=self.F(10), fg=MUTED, bg=PANEL)
        counter.pack(pady=(10, 4))

        def update():
            counter.configure(text="%d of %d keys pressed" % (len(seen),
                                                              len(cells)))

        def on_key(ev):
            kid = KEYMAP.get(ev.keysym)
            if not kid and len(ev.keysym) == 1:
                kid = ev.keysym.upper()
            if kid in cells and kid not in seen:
                seen.add(kid)
                cells[kid].configure(bg=OK, fg=NAVY)
                update()
            return "break"

        def done():
            win.destroy()
            if len(seen) >= len(cells):
                self.finish_manual("Keyboard", "PASS",
                                   ["All %d keys pressed" % len(cells)])
            elif len(seen) >= 10:
                self.finish_manual("Keyboard", "PASS",
                                   ["Partial test: %d of %d keys pressed"
                                    % (len(seen), len(cells))])
            else:
                self.finish_manual("Keyboard", "WARN",
                                   ["Only %d keys pressed" % len(seen)])

        def failed():
            win.destroy()
            missing = sorted(set(cells) - seen)
            self.finish_manual("Keyboard", "FAIL",
                               ["Key not working. Not pressed: "
                                + ", ".join(missing[:12])])

        bf = tk.Frame(win, bg=PANEL)
        bf.pack(pady=8)
        self.btn(bf, "Done", done).pack(side="left", padx=6)
        self.btn(bf, "A key does not work", failed, "danger").pack(side="left")
        update()
        win.bind("<KeyPress>", on_key)
        win.focus_force()

    def manual_speakers(self):
        def work():
            if not shutil.which("speaker-test"):
                self.q.put(lambda: self.finish_manual(
                    "Speakers", "WARN", ["speaker-test not installed"]))
                return
            try:
                subprocess.call(["speaker-test", "-t", "sine", "-f", "440",
                                 "-l", "1", "-c", "2"], timeout=15,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
            except Exception:
                pass
            self.q.put(ask)

        def ask():
            if self.mb.askyesno("Speakers", "Did you hear a clear tone from "
                                "the speakers?"):
                self.finish_manual("Speakers", "PASS", ["Tone heard"])
            else:
                self.finish_manual("Speakers", "FAIL", ["No sound heard"])
        self.set_row("Speakers", "RUN")
        threading.Thread(target=work, daemon=True).start()

    # ---------- screen 4: report ----------
    def build_report(self):
        tk = self.tk
        pg = self.new_page("report")
        self.r_banner = tk.Label(pg, text="", font=self.F(26, True), fg=NAVY,
                                 bg=GREEN_BAN, pady=14)
        self.r_banner.pack(fill="x", padx=24, pady=(18, 10))
        self.r_reasons = self.lbl(pg, "", 10, fg=WARN, bg=PANEL)
        self.r_reasons.pack(fill="x", padx=30)
        row = tk.Frame(pg, bg=PANEL)
        row.pack(fill="both", expand=True, padx=24, pady=6)
        l_outer, self.r_left = self.card(row, "Laptop and job")
        l_outer.pack(side="left", fill="both", expand=True, padx=5)
        r_outer, self.r_right = self.card(row, "Results")
        r_outer.pack(side="left", fill="both", expand=True, padx=5)
        self.r_files = self.lbl(pg, "", 10, fg=MUTED, bg=PANEL, wraplength=900)
        self.r_files.pack(fill="x", padx=30, pady=(0, 4))
        left, right, mid = self.bar(pg)
        self.btn(left, "Power off", self.power_off, "ghost").pack()
        self.btn(right, "Next laptop", self.next_laptop).pack()

    def build_report_data(self):
        chk = self.s.get("check") or {}
        wipe = self.s.get("wipe") or {}
        tests = []
        seen = set()
        for r in self.s["auto"]:
            comp = r.get("component")
            if comp in MANUAL:
                continue
            tests.append(r)
            seen.add(comp)
        for comp in MANUAL:
            tests.append(self.s["manual"].get(comp) or {
                "component": comp, "status": "WARN",
                "notes": ["Not tested"], "details": {}})
        i = self.ident
        stor = "; ".join("%s (%s) %s GB" % (d.get("model") or "Drive",
                                            d.get("drive_type", "?"),
                                            d.get("size_gb"))
                         for d in self.disks) or "-"
        overall, reasons = compute_overall(self.s["wipe_state"],
                                           wipe.get("overall"), tests)
        er = chk.get("autopilot_result", "?")
        return {
            "tool": "laptop_window.py", "version": VERSION,
            "time": now().strftime("%Y-%m-%d %H:%M:%S") + " (Europe/Dublin)",
            "job_id": self.job(), "asset_id": self.asset(),
            "serial": self.serial() or "UNKNOWN", "technician": self.tech(),
            "location": self.v_loc.get().strip(),
            "make_model": ("%s %s" % (i.get("manufacturer", ""),
                                      i.get("model", ""))).strip(),
            "cpu": i.get("cpu", ""),
            "ram": ram_text(self.ram).replace("\n", "; "),
            "ram_details": self.ram,
            "screen": screen_text(self.screen),
            "screen_details": self.screen or {},
            "storage": stor,
            "absolute": chk.get("absolute_result", "?"),
            "enrolment": {"CLEAN": "CLEAN", "BLOCKED": "ACTIVE",
                          "UNKNOWN": "NOT CHECKED"}.get(er, "PAST ENROLMENT"),
            "check_result": chk.get("result", "?"),
            "wipe_state": self.s["wipe_state"],
            "wipe_devices": wipe.get("devices") or [],
            "certificate": wipe.get("certificate_sha256", ""),
            "tests": tests, "overall": overall, "reasons": reasons,
        }

    def finish(self):
        rep = self.build_report_data()
        self.set_prog("Tests", "done", OK)
        self.set_step(4)
        self.show("report")
        bg, fg = {"PASS": (GREEN_BAN, NAVY), "FAIL": (RED_BAN, "white")}.get(
            rep["overall"], (WARN, NAVY))
        self.r_banner.configure(text=rep["overall"], bg=bg, fg=fg)
        self.r_reasons.configure(text="  ".join("- " + r for r in rep["reasons"]))
        for frame in (self.r_left, self.r_right):
            for w in frame.winfo_children():
                w.destroy()
        left = [("Job ID", rep["job_id"]), ("Asset ID", rep["asset_id"]),
                ("Serial", rep["serial"]), ("Technician", rep["technician"]),
                ("Location", rep["location"]), ("Time", rep["time"]),
                ("Model", rep["make_model"]),
                ("RAM", ram_text(self.ram, short=True)),
                ("Screen", rep["screen"])]
        for r, (k, v) in enumerate(left):
            self.lbl(self.r_left, k, 10, fg=MUTED).grid(row=r, column=0,
                                                        sticky="nw", padx=(0, 14),
                                                        pady=2)
            self.lbl(self.r_left, v, 11, True, wraplength=380).grid(
                row=r, column=1, sticky="nw", pady=2)
        good = sum(1 for t in rep["tests"] if t.get("status") == "PASS")
        right = [("Absolute", rep["absolute"]), ("Enrolment", rep["enrolment"]),
                 ("Wipe", rep["wipe_state"]),
                 ("Components", "%d of %d PASS" % (good, len(rep["tests"])))]
        for r, (k, v) in enumerate(right):
            self.lbl(self.r_right, k, 10, fg=MUTED).grid(row=r, column=0,
                                                         sticky="nw", padx=(0, 14),
                                                         pady=2)
            col = OK if v in ("CLEAR", "CLEAN", "WIPED") else TEXT
            self.lbl(self.r_right, v, 11, True, fg=col).grid(
                row=r, column=1, sticky="nw", pady=2)
        saved = self.save_reports(rep)
        msg = "Saved: " + ", ".join(os.path.basename(p) for p in saved) + \
              "   in   " + self.out_dir
        if not self.persistent:
            msg += ("\nWARNING: these files are only in memory. Plug in the "
                    "ITAD_DATA stick and run again, or copy them before "
                    "powering off.")
        self.r_files.configure(text=msg, fg=MUTED if self.persistent else WARN)
        self.set_prog("Report", "saved" if saved else "not saved",
                      OK if saved else BAD)

    def save_reports(self, rep):
        base = "SN_" + safe_name(rep["serial"] if rep["serial"] != "UNKNOWN"
                                 else rep["asset_id"])
        saved = []
        for ext, fn in (("txt", lambda p: open(p, "w").write(render_txt(rep))),
                        ("json", lambda p: json.dump(rep, open(p, "w"),
                                                     indent=2)),
                        ("pdf", lambda p: write_pdf(p, rep)),
                        ("log", lambda p: open(p, "w").write(
                            "\n".join(self.log) + "\n"))):
            path = os.path.join(self.out_dir, "%s.%s" % (base, ext))
            try:
                fn(path)
                saved.append(path)
            except Exception:
                pass
        return saved

    # ---------- misc ----------
    def next_laptop(self):
        # keeps ITAD_SIGNED_IN so the technician stays signed in
        os.execv(PY, [PY] + sys.argv)

    def power_off(self):
        if self.wiping:
            self.mb.showinfo("Wipe running", "A wipe is running. Do not power "
                             "off until it has finished.")
            return
        if self.mb.askyesno("Power off", "Power off this laptop now?"):
            if subprocess.call(["systemctl", "poweroff"]) != 0:
                subprocess.call(["poweroff"])

    def quit(self):
        if self.wiping:
            self.mb.showinfo("Wipe running", "A wipe is running. Please wait.")
            return
        self.root.destroy()
        sys.exit(0)


def main():
    ap = argparse.ArgumentParser(description="LaptopSeal window")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--windowed", action="store_true")
    args = ap.parse_args()

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        if shutil.which("sudo"):
            os.execvp("sudo", ["sudo", "-E", PY] + sys.argv)
        print("Must run as root.")
        sys.exit(3)
    for p in (CHECK, WIPE, HW_TEST):
        if not os.path.isfile(p):
            print("Missing tool: %s" % p)
            sys.exit(1)
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        fallback_terminal("no screen session", args.dry_run)
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except Exception as e:
        fallback_terminal("Tk is not installed: %s" % e, args.dry_run)
    try:
        root = tk.Tk()
    except Exception as e:
        fallback_terminal("cannot open the window: %s" % e, args.dry_run)
    App(tk, ttk, messagebox, root, args.dry_run, args.quick, args.windowed)
    root.mainloop()


if __name__ == "__main__":
    main()